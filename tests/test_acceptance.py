"""验收专项测试：对应 PRD §10 中未在其他文件覆盖的条目。"""
from __future__ import annotations

import hashlib
import os

import pytest

from app.core import db, matcher, scan
from app.core.models import Track
from app.providers.base import BaseProvider
from app.write import service
from app.write.service import PendingWrite
from conftest import make_wav


def sha(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def test_b04_scan_and_match_never_write_files(conn, tmp_path):
    """B-04：扫描和匹配不写文件，静置后字节不变。"""
    root = tmp_path / "music"
    a = make_wav(str(root / "01 晴天.wav"), seconds=0.2)
    before = sha(a)
    mtime = os.path.getmtime(a)

    scan.scan(conn, str(root))
    track = db.get_track_by_path(conn, a)
    track.id = db.upsert_track(conn, track)

    class Silent(BaseProvider):
        name = "静默源"

        def search(self, query):
            return []

    matcher.match_track(conn, track, [Silent()], force=True)

    assert sha(a) == before
    assert os.path.getmtime(a) == mtime


def test_e01_index_can_be_rebuilt(conn, tmp_path):
    """E-01：删掉索引库后重新扫描能恢复。"""
    root = tmp_path / "music"
    make_wav(str(root / "a.wav"), seconds=0.2)
    make_wav(str(root / "b.wav"), seconds=0.2)
    scan.scan(conn, str(root))
    assert len(db.list_tracks(conn)) == 2

    db_file = tmp_path / "index.db"
    conn.close()
    for suffix in ("", "-wal", "-shm"):
        path = str(db_file) + suffix
        if os.path.exists(path):
            os.remove(path)

    fresh = db.connect(str(db_file))
    try:
        assert db.list_tracks(fresh) == []
        scan.scan(fresh, str(root))
        assert len(db.list_tracks(fresh)) == 2
    finally:
        fresh.close()


def test_b03_no_automatic_write_path():
    """B-03：写文件的唯一入口要求显式传入待写项。"""
    import inspect

    signature = inspect.signature(service.write_batch)
    assert "pending" in signature.parameters
    assert signature.parameters["pending"].default is inspect.Parameter.empty

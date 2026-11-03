"""批量写回编排测试（对应验收 A-12 / A-13 / B-06）。"""
from __future__ import annotations

import hashlib
import os

import pytest

from app.core import db, probe
from app.core.models import Track
from app.write import service, writer
from app.write.service import PendingWrite
from conftest import make_wav


def sha_file(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "music"
    a = make_wav(str(root / "a.wav"), seconds=0.3)
    b = make_wav(str(root / "b.wav"), seconds=0.3)
    return root, a, b


def make_pending(path: str, **fields) -> PendingWrite:
    track = Track(id=None, path=path, name=os.path.basename(path))
    return PendingWrite(track=track, fields=fields)


def test_write_batch_updates_files_and_db(conn, library):
    root, a, b = library
    ta = Track(path=a, name="a.wav")
    tb = Track(path=b, name="b.wav")
    ta.id = db.upsert_track(conn, ta)
    tb.id = db.upsert_track(conn, tb)

    summary = service.write_batch(
        conn,
        [PendingWrite(ta, {"title": "晴天", "artist": "周杰伦"}),
         PendingWrite(tb, {"title": "夜曲"})],
        str(root),
    )
    assert summary["ok"] == 2
    assert summary["failed"] == 0
    assert probe.probe(a).title == "晴天"
    assert probe.probe(b).title == "夜曲"
    assert db.get_track(conn, ta.id).tag_title == "晴天"


def test_failed_item_does_not_block_others(conn, library, monkeypatch):
    """A-12：单条失败不影响其余。"""
    root, a, b = library
    ta = Track(path=a, name="a.wav")
    tb = Track(path=b, name="b.wav")
    ta.id = db.upsert_track(conn, ta)
    tb.id = db.upsert_track(conn, tb)

    real_write = writer.write

    def flaky(path, fields, cover=None, lyrics=None, opts=None):
        if path.endswith("a.wav"):
            raise OSError("文件被占用")
        return real_write(path, fields, cover, lyrics, opts)

    monkeypatch.setattr(service.writer, "write", flaky)
    summary = service.write_batch(
        conn,
        [PendingWrite(ta, {"title": "晴天"}), PendingWrite(tb, {"title": "夜曲"})],
        str(root),
    )
    assert summary["ok"] == 1
    assert summary["failed"] == 1
    failed = [i for i in summary["items"] if i.status == "failed"][0]
    assert "占用" in failed.error


def test_empty_pending_returns_immediately(conn, library):
    root, _, _ = library
    summary = service.write_batch(conn, [], str(root))
    assert summary["batch_id"] == 0


def test_batch_history_recorded(conn, library):
    root, a, _ = library
    ta = Track(path=a, name="a.wav")
    ta.id = db.upsert_track(conn, ta)
    service.write_batch(conn, [PendingWrite(ta, {"title": "晴天"})], str(root))
    batches = db.list_batches(conn)
    assert batches and batches[0]["ok"] == 1
    items = db.batch_items(conn, batches[0]["id"])
    assert items[0]["status"] == "done"
    assert items[0]["fields"] == {"title": "晴天"}

"""全格式走完整保存链路（备份 → 写入 → 校验），复现「保存失败」。"""
from __future__ import annotations

import os
import shutil

import pytest

from app.core import db, probe
from app.core.models import Track
from app.write import service
from app.write.service import PendingWrite

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, "samples")


def sample_paths():
    """没有本地 samples/ 时返回占位项，让测试正常跳过而不是收集阶段报错。"""
    if not os.path.isdir(SAMPLES):
        return [None]
    found = sorted(
        os.path.join(SAMPLES, n) for n in os.listdir(SAMPLES)
        if os.path.isfile(os.path.join(SAMPLES, n))
    )
    return found or [None]


@pytest.mark.parametrize("sample", sample_paths(), ids=lambda p: os.path.basename(p) if p else "no-samples")
def test_save_through_service(conn, tmp_path, sample):
    if sample is None:
        pytest.skip("需要本地 samples/ 样本文件")
    music = tmp_path / "music"
    music.mkdir()
    target = str(music / os.path.basename(sample))
    shutil.copy2(sample, target)

    track = Track(path=target, name=os.path.basename(target), size=os.path.getsize(target))
    track.id = db.upsert_track(conn, track)

    summary = service.write_batch(
        conn,
        [PendingWrite(track=track, fields={"title": "测试标题", "artist": "测试歌手"})],
        str(music),
    )
    assert summary.get("error", "") == ""
    assert summary["ok"] == 1, summary["items"][0].error if summary["items"] else "no items"
    assert probe.probe(target).title == "测试标题"


def test_save_reports_reason_when_file_locked(conn, tmp_path, monkeypatch):
    """文件被占用时必须给出可读原因，而不是静默失败。"""
    from conftest import make_wav

    music = tmp_path / "music"
    path = make_wav(str(music / "a.wav"), seconds=0.2)
    track = Track(path=path, name="a.wav")
    track.id = db.upsert_track(conn, track)

    real_write = service.writer.write

    def locked(*args, **kwargs):
        raise PermissionError("文件被占用")

    monkeypatch.setattr(service.writer, "write", locked)
    summary = service.write_batch(conn, [PendingWrite(track, {"title": "x"})], str(music))
    assert summary["failed"] == 1
    assert "占用" in summary["items"][0].error

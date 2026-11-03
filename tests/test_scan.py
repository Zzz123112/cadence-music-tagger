"""扫描与读取测试（对应验收 A-01 / A-02 / A-03）。"""
from __future__ import annotations

import os

from app.core import db, probe, scan
from app.core.models import STATUS_EMPTY, STATUS_NO_COVER
from conftest import make_wav, tag_wav


def test_probe_reads_wav_parameters(tmp_path):
    path = make_wav(str(tmp_path / "a.wav"), seconds=1.0, rate=48000, channels=2, sampwidth=3)
    result = probe.probe(path)
    assert result.error == ""
    assert result.fmt == "WAV"
    assert result.sample_rate == 48000
    assert result.channels == 2
    assert result.bit_depth == 24
    assert abs(result.duration - 1.0) < 0.1


def test_probe_reads_tags(tmp_path):
    path = make_wav(str(tmp_path / "b.wav"), seconds=0.5)
    tag_wav(path, title="晴天", artist="周杰伦", album="叶惠美", year="2003", track="3")
    result = probe.probe(path)
    assert result.title == "晴天"
    assert result.artist == "周杰伦"
    assert result.album == "叶惠美"
    assert result.year.startswith("2003")
    assert result.track.startswith("3")


def test_probe_missing_file_reports_error(tmp_path):
    result = probe.probe(str(tmp_path / "nope.wav"))
    assert result.error != ""


def test_sidecar_lrc_detected(tmp_path):
    path = make_wav(str(tmp_path / "c.wav"), seconds=0.2)
    assert probe.sidecar_lrc(path) == ""
    open(os.path.splitext(path)[0] + ".lrc", "w", encoding="utf-8").write("[00:01.00]test")
    assert probe.sidecar_lrc(path) != ""
    assert probe.probe(path).has_lyrics is True


def test_scan_counts_all_files(conn, library):
    stats = scan.scan(conn, str(library))
    assert stats["total"] == 3
    assert stats["added"] == 3
    assert len(db.list_tracks(conn)) == 3


def test_scan_marks_empty_tags(conn, library):
    scan.scan(conn, str(library))
    statuses = {t.status for t in db.list_tracks(conn)}
    assert statuses == {STATUS_EMPTY}


def test_scan_is_incremental(conn, library):
    scan.scan(conn, str(library))
    stats = scan.scan(conn, str(library))
    assert stats["unchanged"] == 3
    assert stats["added"] == 0


def test_scan_detects_changed_file(conn, library):
    scan.scan(conn, str(library))
    target = library / "01 晴天.wav"
    tag_wav(str(target), title="晴天", artist="周杰伦", album="叶惠美")
    stats = scan.scan(conn, str(library))
    assert stats["updated"] == 1
    statuses = {t.status for t in db.list_tracks(conn)}
    assert STATUS_NO_COVER in statuses


def test_scan_removes_missing(conn, library):
    scan.scan(conn, str(library))
    os.remove(str(library / "03 夜曲.wav"))
    stats = scan.scan(conn, str(library))
    assert stats["removed"] == 1
    assert len(db.list_tracks(conn)) == 2


def test_scan_ignores_unsupported_files(conn, tmp_path):
    root = tmp_path / "mixed"
    make_wav(str(root / "a.wav"), seconds=0.2)
    (root / "readme.txt").write_text("not audio", encoding="utf-8")
    stats = scan.scan(conn, str(root))
    assert stats["total"] == 1


def test_scan_skips_backup_directory(conn, tmp_path):
    root = tmp_path / "lib"
    make_wav(str(root / "a.wav"), seconds=0.2)
    make_wav(str(root / ".musictagger_backup" / "old.wav"), seconds=0.2)
    stats = scan.scan(conn, str(root))
    assert stats["total"] == 1

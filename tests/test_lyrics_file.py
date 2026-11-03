"""歌词文件（.lrc）读写测试。"""
from __future__ import annotations

import os

import pytest

from app.core import lyrics
from conftest import make_wav


def test_sidecar_path(tmp_path):
    audio = make_wav(str(tmp_path / "01 晴天.wav"), seconds=0.2)
    assert lyrics.sidecar_path(audio) == str(tmp_path / "01 晴天.lrc")


def test_save_lrc_writes_utf8(tmp_path):
    audio = make_wav(str(tmp_path / "a.wav"), seconds=0.2)
    text = "[00:01.00]窗外的麻雀\n[00:05.00]你说这一句"
    target = lyrics.save_lrc(audio, text)
    assert os.path.isfile(target)
    with open(target, encoding="utf-8") as fh:
        assert "窗外的麻雀" in fh.read()


def test_save_lrc_rejects_empty(tmp_path):
    audio = make_wav(str(tmp_path / "b.wav"), seconds=0.2)
    with pytest.raises(ValueError):
        lyrics.save_lrc(audio, "   ")


def test_save_lrc_respects_overwrite_flag(tmp_path):
    audio = make_wav(str(tmp_path / "c.wav"), seconds=0.2)
    lyrics.save_lrc(audio, "[00:01.00]旧")
    with pytest.raises(FileExistsError):
        lyrics.save_lrc(audio, "[00:02.00]新", overwrite=False)
    lyrics.save_lrc(audio, "[00:02.00]新", overwrite=True)
    with open(lyrics.sidecar_path(audio), encoding="utf-8") as fh:
        assert "新" in fh.read()

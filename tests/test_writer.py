"""写入器测试（对应验收 A-09 / A-10 / B-01 / B-07）。"""
from __future__ import annotations

import hashlib

import pytest

from app.core import probe
from app.write import writer
from app.write.writer import WriteNotSupported, WriteOptions
from conftest import make_wav

PNG_1PX = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x03\xe8\x00\x00\x03\xe8\x08\x02\x00\x00\x00"
    b"\x00\x00\x00\x00"
)


def wav_audio_chunk(path: str) -> bytes:
    """取出 WAV 的 data 块负载，用于证明音频字节没变。"""
    with open(path, "rb") as fh:
        data = fh.read()
    assert data[:4] == b"RIFF"
    pos = 12
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        if cid == b"data":
            return data[pos + 8:pos + 8 + size]
        pos += 8 + size + (size & 1)
    return b""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def wav_file(tmp_path):
    return make_wav(str(tmp_path / "song.wav"), seconds=0.5, rate=44100)


def test_write_text_fields(wav_file):
    writer.write(wav_file, {"title": "七里香", "artist": "周杰伦", "album": "七里香",
                            "year": "2004", "track": "2"})
    result = probe.probe(wav_file)
    assert result.title == "七里香"
    assert result.artist == "周杰伦"
    assert result.album == "七里香"
    assert result.track.startswith("2")


def test_audio_bytes_unchanged_after_tag_write(wav_file):
    """B-01：写入标签不得改动音频数据区。"""
    before = sha(wav_audio_chunk(wav_file))
    size_before = len(wav_audio_chunk(wav_file))
    writer.write(wav_file, {"title": "晴天", "artist": "周杰伦"})
    after = sha(wav_audio_chunk(wav_file))
    assert before == after
    assert len(wav_audio_chunk(wav_file)) == size_before


def test_parameters_unchanged_after_write(wav_file):
    """B-02：时长/位深/采样率/声道不变。"""
    before = probe.probe(wav_file)
    writer.write(wav_file, {"title": "晴天"})
    after = probe.probe(wav_file)
    assert before.audio_signature() == after.audio_signature()


def test_write_cover(wav_file):
    writer.write(wav_file, {"title": "晴天"}, cover=PNG_1PX)
    result = probe.probe(wav_file)
    assert result.has_cover is True


def test_cover_replaced_not_stacked(wav_file):
    writer.write(wav_file, {"title": "晴天"}, cover=PNG_1PX)
    writer.write(wav_file, {"title": "晴天"}, cover=PNG_1PX,
                 opts=WriteOptions(replace_cover=True))
    from mutagen.wave import WAVE

    audio = WAVE(wav_file)
    pictures = audio.tags.getall("APIC")
    assert len(pictures) == 1


def test_write_lyrics(wav_file):
    writer.write(wav_file, {"title": "晴天"}, lyrics="[00:01.00]窗外的麻雀")
    assert probe.probe(wav_file).has_lyrics is True


def test_cover_can_be_skipped_by_option(wav_file):
    writer.write(wav_file, {"title": "晴天"}, cover=PNG_1PX,
                 opts=WriteOptions(write_cover=False))
    assert probe.probe(wav_file).has_cover is False


def test_unsupported_format_raises(tmp_path):
    target = tmp_path / "x.dff"
    target.write_bytes(b"DFF")
    with pytest.raises(WriteNotSupported):
        writer.write(str(target), {"title": "x"})


def test_read_only_format_raises(tmp_path):
    target = tmp_path / "x.tak"
    target.write_bytes(b"TAK")
    with pytest.raises(WriteNotSupported):
        writer.write(str(target), {"title": "x"})


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        writer.write(str(tmp_path / "nope.wav"), {"title": "x"})


def test_supports_matrix():
    assert writer.supports("a.flac")
    assert writer.supports("a.dsf")
    assert writer.supports("a.ape")
    assert not writer.supports("a.dff")
    assert not writer.supports("a.txt")

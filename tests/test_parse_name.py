"""文件名解析测试（对应验收 A-04）。"""
from __future__ import annotations

import pytest

from app.core.parse_name import build_keywords, extract_version, parse_name, strip_noise


def test_artist_dash_title():
    r = parse_name("周杰伦 - 晴天.flac")
    assert r.artist == "周杰伦"
    assert r.title == "晴天"
    assert r.confidence >= 0.6


def test_track_prefix_only():
    r = parse_name("01 晴天.flac")
    assert r.track == "1"
    assert r.title == "晴天"
    assert r.artist == ""


def test_track_prefix_with_dash():
    r = parse_name("02 - 稻香.ape")
    assert r.track == "2"
    assert r.title == "稻香"


def test_title_dash_artist_uses_known_artists():
    r = parse_name("晴天 - 周杰伦.mp3", known_artists={"周杰伦"})
    assert r.artist == "周杰伦"
    assert r.title == "晴天"
    assert r.confidence >= 0.85


def test_book_title_form():
    r = parse_name("周杰伦《晴天》.flac")
    assert r.artist == "周杰伦"
    assert r.title == "晴天"
    assert r.confidence >= 0.9


def test_bracket_artist_form():
    r = parse_name("[周杰伦] 晴天.flac")
    assert r.artist == "周杰伦"
    assert r.title == "晴天"


def test_noise_tokens_removed():
    r = parse_name("晴天 [DSD64].dsf")
    assert r.title == "晴天"
    assert "DSD64" not in r.title


def test_hires_suffix_removed():
    r = parse_name("七里香 [Hi-Res] [24bit 96kHz].flac")
    assert r.title == "七里香"


def test_version_word_detected_and_title_kept():
    r = parse_name("七里香 (Live).flac")
    assert r.version == "live"
    assert r.title == "七里香"


def test_version_word_chinese():
    r = parse_name("晴天 伴奏版.flac")
    assert r.version == "instrumental"


def test_plain_title_only():
    r = parse_name("稻香.flac")
    assert r.title == "稻香"
    assert r.artist == ""
    assert 0.3 <= r.confidence < 0.6


def test_unknown_artist_defaults_left_side():
    r = parse_name("群星 - 后来.flac")
    assert r.artist == "群星"
    assert r.title == "后来"
    assert r.confidence == pytest.approx(0.7)


def test_strip_noise_reports_removed():
    cleaned, removed = strip_noise("晴天 [FLAC] [24bit]")
    assert cleaned.strip() == "晴天"
    assert "FLAC" in removed


def test_extract_version_none():
    assert extract_version("晴天") == ""


def test_build_keywords_prefers_title_artist():
    r = parse_name("周杰伦 - 晴天.flac")
    assert build_keywords(r) == "晴天 周杰伦"


def test_parse_never_raises_on_weird_input():
    for name in ("", "   ", "-", "a - b - c.flac", "01.flac", "《》.flac"):
        result = parse_name(name)
        assert isinstance(result.title, str)
        assert 0.0 <= result.confidence <= 1.0

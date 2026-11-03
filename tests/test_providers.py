"""数据源测试：用固定响应验证解析，不依赖外网（对应验收 A-05）。"""
from __future__ import annotations

import pytest

from app.core.models import Candidate
from app.providers import base, huayu_a, huayu_b, musicbrainz
from app.providers.base import ProviderError, SearchQuery
from app.providers.http import parse_mmss


MB_PAYLOAD = {
    "recordings": [
        {
            "id": "rec-1",
            "title": "七里香",
            "length": 299000,
            "artist-credit": [{"name": "周杰伦"}],
            "releases": [
                {
                    "id": "rel-1",
                    "title": "七里香",
                    "date": "2004-08-03",
                    "media": [{"track": [{"number": "2"}]}],
                }
            ],
        }
    ]
}

NETEASE_PAYLOAD = {
    "result": {
        "songs": [
            {
                "id": 123,
                "name": "七里香",
                "artists": [{"name": "周杰伦"}],
                "album": {"name": "七里香", "picUrl": "https://p1.music.126.net/abc.jpg?param=100y100"},
                "duration": 299000,
            }
        ]
    }
}

QQ_PAYLOAD = {
    "data": {
        "song": {
            "list": [
                {
                    "songname": "七里香",
                    "singer": [{"name": "周杰伦"}],
                    "albumname": "七里香",
                    "albummid": "mid123",
                    "songmid": "song123",
                    "interval": 299,
                }
            ]
        }
    }
}


def test_musicbrainz_parse_fields():
    cands = musicbrainz.MusicBrainzProvider.parse(MB_PAYLOAD)
    assert len(cands) == 1
    c = cands[0]
    assert c.title == "七里香"
    assert c.artist == "周杰伦"
    assert c.album == "七里香"
    assert c.year == "2004"
    assert c.track == "2"
    assert c.duration == pytest.approx(299.0)
    assert c.source == "MusicBrainz"
    assert c.cover_urls


def test_netease_parse_fields():
    cands = huayu_a.NeteaseProvider.parse(NETEASE_PAYLOAD)
    assert len(cands) == 1
    c = cands[0]
    assert c.artist == "周杰伦"
    assert c.duration == pytest.approx(299.0)
    assert c.raw_id == "123"
    assert c.source == "网易云"
    assert "param=1000y1000" in c.cover_urls[0]


def test_qq_parse_fields():
    cands = huayu_b.QQMusicProvider.parse(QQ_PAYLOAD)
    assert len(cands) == 1
    c = cands[0]
    assert c.album == "七里香"
    assert c.raw_id == "song123"
    assert c.source == "QQ音乐"
    assert "T002R500x500" in c.cover_urls[0]


def test_parse_tolerates_empty_payload():
    assert musicbrainz.MusicBrainzProvider.parse({}) == []
    assert huayu_a.NeteaseProvider.parse({}) == []
    assert huayu_b.QQMusicProvider.parse({}) == []


def test_netease_search_payload_has_no_cover_url():
    """真实搜索接口只给 album.picId，没有 picUrl —— 这是封面取不到的根因。"""
    payload = {
        "result": {
            "songs": [
                {
                    "id": 1,
                    "name": "TOMATO",
                    "artists": [{"name": "7co"}],
                    "album": {"name": "TOMATO", "picId": 109951170165177009},
                    "duration": 192000,
                }
            ]
        }
    }
    cands = huayu_a.NeteaseProvider.parse(payload)
    assert cands[0].cover_urls == []


def test_netease_detail_cover_parsed():
    detail = {
        "songs": [
            {"id": 123, "album": {"name": "七里香",
                                  "picUrl": "https://p1.music.126.net/xyz.jpg"}}
        ]
    }
    assert huayu_a.parse_detail_cover(detail) == "https://p1.music.126.net/xyz.jpg"
    assert huayu_a.parse_detail_cover({}) == ""


def test_base_provider_has_default_fetch_lyrics():
    """MusicBrainz 不提供歌词，基类必须有默认实现，否则整个歌词请求会崩。"""
    provider = musicbrainz.MusicBrainzProvider()
    cand = Candidate(source="MusicBrainz", title="x")
    assert provider.fetch_lyrics(cand) is None


def test_parse_tolerates_malformed_items():
    payload = {"recordings": [{"title": ""}, {"title": "x", "artist-credit": None, "releases": None}]}
    cands = musicbrainz.MusicBrainzProvider.parse(payload)
    assert len(cands) == 1
    assert cands[0].artist == ""


def test_parse_mmss():
    assert parse_mmss("4:59") == pytest.approx(299.0)
    assert parse_mmss("04:59") == pytest.approx(299.0)
    assert parse_mmss(299) == pytest.approx(299.0)
    assert parse_mmss("") == 0.0
    assert parse_mmss("1:02:03") == pytest.approx(3723.0)


def test_dedupe_removes_identical_rows():
    a = Candidate(source="A", title="七里香", artist="周杰伦", album="七里香")
    b = Candidate(source="A", title="七里香 ", artist="周杰伦", album="七里香")
    assert len(base.dedupe([a, b])) == 1


def test_search_all_isolates_failures(monkeypatch):
    """单源失败不影响其他源（A-05 的降级要求）。"""
    from app.providers import search_all

    class Good(base.BaseProvider):
        name = "好源"

        def search(self, query):
            return [Candidate(source="好源", title="七里香")]

    class Bad(base.BaseProvider):
        name = "坏源"

        def search(self, query):
            raise ProviderError("timeout")

    results, errors = search_all(SearchQuery("七里香"), [Good(), Bad()])
    assert len(results["好源"]) == 1
    assert results["坏源"] == []
    assert "坏源" in errors


def test_offline_mode_raises_provider_error(monkeypatch):
    from app.providers import http

    http.set_offline(True)
    try:
        with pytest.raises(ProviderError):
            http.fetch_json("https://example.invalid")
    finally:
        http.set_offline(False)


def test_cover_fetch_skipped_when_offline():
    from app.providers import http

    http.set_offline(True)
    try:
        provider = huayu_a.NeteaseProvider()
        cand = Candidate(source="网易云", title="x", cover_urls=["https://example.invalid/a.jpg"])
        assert provider.fetch_cover(cand) is None
    finally:
        http.set_offline(False)

"""新增数据源（Apple Music / 酷狗 / 酷我 / TheAudioDB / Audius）的解析测试，不联网。"""
from __future__ import annotations

from app.core.models import Candidate
from app.providers import ALL_PROVIDERS, PROVIDER_NAMES, build_providers
from app.providers import audiodb, audius, itunes, kugou, kuwo
from app.providers.base import SearchQuery


# ---------------------------------------------------------------- 注册

def test_registry_contains_new_sources():
    names = PROVIDER_NAMES
    assert "Apple Music" in names
    assert "酷狗" in names
    assert "酷我" in names
    assert "TheAudioDB" in names
    assert "Audius" in names
    assert len(ALL_PROVIDERS) >= 8
    assert len(set(names)) == len(names)  # 名字不能重复


def test_build_providers_respects_switch():
    only = build_providers({name: False for name in PROVIDER_NAMES})
    assert only == []
    enabled = build_providers({name: name == "Apple Music" for name in PROVIDER_NAMES})
    assert [p.name for p in enabled] == ["Apple Music"]


# ---------------------------------------------------------------- Apple Music

ITUNES_PAYLOAD = {
    "results": [
        {
            "trackName": "晴天",
            "artistName": "周杰伦",
            "collectionName": "叶惠美",
            "trackTimeMillis": 269000,
            "artworkUrl100": "https://is1.mzstatic.com/a/100x100bb.jpg",
            "releaseDate": "2003-07-31T07:00:00Z",
            "trackNumber": 3,
            "trackId": 12345,
            "primaryGenreName": "国语流行",
        }
    ]
}


def test_itunes_parse_fields():
    items = itunes.ItunesProvider.parse(ITUNES_PAYLOAD)
    assert len(items) == 1
    cand = items[0]
    assert cand.title == "晴天"
    assert cand.artist == "周杰伦"
    assert cand.album == "叶惠美"
    assert abs(cand.duration - 269.0) < 0.01
    assert cand.year == "2003"
    assert cand.track == "3"
    assert cand.source == "Apple Music"
    assert cand.raw_id == "12345"
    assert "600x600" in cand.cover_urls[0]


def test_itunes_parse_tolerates_empty():
    assert itunes.ItunesProvider.parse({}) == []
    assert itunes.ItunesProvider.parse({"results": [{"artistName": "x"}]}) == []
    assert itunes.ItunesProvider.parse({"results": [None]}) == []


# ---------------------------------------------------------------- 酷狗

KUGOU_PAYLOAD = {
    "data": {
        "info": [
            {
                "songname": "晴天",
                "singername": "周杰伦",
                "album_name": "叶惠美",
                "duration": 269,
                "hash": "abc123",
                "album_id": "966846",
                "extname": "flac",
            }
        ]
    }
}


def test_kugou_parse_fields():
    items = kugou.KugouProvider.parse(KUGOU_PAYLOAD)
    assert len(items) == 1
    cand = items[0]
    assert cand.title == "晴天"
    assert cand.artist == "周杰伦"
    assert cand.album == "叶惠美"
    assert cand.duration == 269
    assert cand.raw_id == "abc123"
    assert cand.cover_urls == ["kugou-album:966846"]


def test_kugou_parse_without_album_id():
    payload = {"data": {"info": [{"songname": "无名", "singername": "", "duration": 0}]}}
    items = kugou.KugouProvider.parse(payload)
    assert items[0].cover_urls == []


def test_kugou_parse_tolerates_junk():
    assert kugou.KugouProvider.parse({}) == []
    assert kugou.KugouProvider.parse({"data": None}) == []


def test_kugou_fetch_cover_uses_album_api(monkeypatch):
    calls = {}

    def fake_json(url, params=None, headers=None, timeout=8):
        calls["json"] = (url, params)
        return {"data": {"imgurl": "http://imge.kugou.com/stdmusic/{size}/x.jpg"}}

    def fake_bytes(url, timeout=8):
        calls["bytes"] = url
        return b"IMAGEDATA"

    monkeypatch.setattr(kugou.http, "fetch_json", fake_json)
    monkeypatch.setattr(kugou.http, "fetch_bytes", fake_bytes)

    cand = Candidate(source="酷狗", title="晴天", cover_urls=["kugou-album:966846"])
    assert kugou.KugouProvider().fetch_cover(cand) == b"IMAGEDATA"
    assert calls["json"][1]["albumid"] == "966846"
    assert "480" in calls["bytes"]


def test_kugou_fetch_cover_without_marker():
    cand = Candidate(source="酷狗", title="晴天")
    assert kugou.KugouProvider().fetch_cover(cand) is None


# ---------------------------------------------------------------- 酷我

KUWO_TEXT = (
    "{'abslist': [{'SONGNAME': '晴天', 'ARTIST': '周杰伦', 'ALBUM': '叶惠美', "
    "'DURATION': '269', 'MUSICRID': 'MUSIC_51685512', 'ALBUMID': '0', "
    "'web_albumpic_short': ''}]}"
)


def test_kuwo_parse_loose_json():
    items = kuwo.KuwoProvider.parse(KUWO_TEXT)
    assert len(items) == 1
    cand = items[0]
    assert cand.title == "晴天"
    assert cand.artist == "周杰伦"
    assert cand.album == "叶惠美"
    assert cand.duration == 269
    assert cand.raw_id == "MUSIC_51685512"
    assert cand.cover_urls == []  # ALBUMID 为 0 时不拼封面地址


def test_kuwo_parse_uses_album_cover_when_present():
    text = ("{'abslist': [{'SONGNAME': '七里香', 'ARTIST': '周杰伦', 'ALBUM': '七里香', "
            "'DURATION': '299', 'ALBUMID': '12345', 'web_albumpic_short': ''}]}")
    items = kuwo.KuwoProvider.parse(text)
    assert items[0].cover_urls == ["https://img1.kuwo.cn/star/albumcover/12345.jpg"]


def test_kuwo_parse_tolerates_garbage():
    assert kuwo.KuwoProvider.parse("") == []
    assert kuwo.KuwoProvider.parse("<html>not json</html>") == []
    assert kuwo.KuwoProvider.parse("{'abslist': []}") == []


def test_kuwo_parse_keeps_ktv_variant_for_scoring():
    text = ("{'abslist': [{'SONGNAME': '晴天 (KTV版伴奏)', 'ARTIST': '周杰伦', "
            "'ALBUM': '', 'DURATION': '269'}]}")
    items = kuwo.KuwoProvider.parse(text)
    assert items[0].title.startswith("晴天")
    assert "KTV" in items[0].title  # 原样保留，交给证据打分去判断


# ---------------------------------------------------------------- TheAudioDB

AUDIODB_PAYLOAD = {
    "track": [
        {
            "idTrack": "32724184",
            "strTrack": "Yellow",
            "strArtist": "Coldplay",
            "strAlbum": "Parachutes",
            "intDuration": "269240",
            "intTrackNumber": "5",
            "strTrackThumb": "https://r2.theaudiodb.com/img/yellow.jpg",
        }
    ]
}


def test_audiodb_parse_fields():
    items = audiodb.AudioDbProvider.parse(AUDIODB_PAYLOAD)
    assert len(items) == 1
    cand = items[0]
    assert cand.title == "Yellow"
    assert cand.artist == "Coldplay"
    assert cand.album == "Parachutes"
    assert abs(cand.duration - 269.24) < 0.1
    assert cand.track == "5"
    assert cand.raw_id == "32724184"
    assert cand.cover_urls and cand.cover_urls[0].startswith("https://")


def test_audiodb_parse_tolerates_empty():
    assert audiodb.AudioDbProvider.parse({}) == []
    assert audiodb.AudioDbProvider.parse({"track": None}) == []
    assert audiodb.AudioDbProvider.parse({"track": [{"strArtist": "x"}]}) == []


def test_audiodb_requires_artist(monkeypatch):
    called = []
    monkeypatch.setattr(audiodb.http, "fetch_json",
                        lambda *a, **k: called.append(1) or {})
    provider = audiodb.AudioDbProvider()
    assert provider.search(SearchQuery(keywords="晴天", artist="", title="晴天")) == []
    assert called == []  # 缺歌手就不该发请求


# ---------------------------------------------------------------- Audius

AUDIUS_PAYLOAD = {
    "data": [
        {
            "id": "lG0oK",
            "title": "髮如雪",
            "duration": 302,
            "genre": "Pop",
            "user": {"name": "Jay Chou"},
            "artwork": {"480x480": "https://audius/img/480.jpg",
                        "1000x1000": "https://audius/img/1000.jpg"},
        }
    ]
}


def test_audius_parse_fields():
    items = audius.AudiusProvider.parse(AUDIUS_PAYLOAD)
    assert len(items) == 1
    cand = items[0]
    assert cand.title == "髮如雪"
    assert cand.artist == "Jay Chou"
    assert cand.duration == 302
    assert cand.raw_id == "lG0oK"
    assert cand.cover_urls == ["https://audius/img/1000.jpg"]  # 优先最大图


def test_audius_parse_tolerates_empty():
    assert audius.AudiusProvider.parse({}) == []
    assert audius.AudiusProvider.parse({"data": None}) == []
    assert audius.AudiusProvider.parse({"data": [{"title": ""}]}) == []


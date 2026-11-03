"""匹配编排、图片尺寸、歌词校验测试（M3 / M4）。"""
from __future__ import annotations

import pytest

from app.core import db, imagesize, lyrics, matcher
from app.core.models import Candidate, Track
from app.providers.base import BaseProvider, ProviderError, SearchQuery


class FakeProvider(BaseProvider):
    def __init__(self, name, items=None, fail=False):
        self.name = name
        self.items = items or []
        self.fail = fail
        self.calls = []

    def search(self, query: SearchQuery):
        self.calls.append(query)
        if self.fail:
            raise ProviderError("boom")
        return [Candidate(**c) for c in self.items]


def make_track(**kw) -> Track:
    base = dict(
        id=1,
        path="/m/叶惠美/02 七里香.ape",
        name="02 七里香.ape",
        duration=299.0,
        parsed_title="七里香",
        parsed_artist="周杰伦",
    )
    base.update(kw)
    return Track(**base)


def test_group_by_title_keeps_order():
    cands = [
        Candidate(source="A", title="七里香"),
        Candidate(source="B", title="晴天"),
        Candidate(source="C", title="七里香 "),
    ]
    groups = matcher.group_by_title(cands)
    assert len(groups) == 2
    assert len(groups[0]) == 2
    assert groups[0][0].source == "A"


def test_same_name_groups_only_returns_duplicates():
    cands = [
        Candidate(source="A", title="七里香"),
        Candidate(source="B", title="晴天"),
    ]
    assert matcher.same_name_groups(cands) == []


def test_build_query_from_filename():
    q = matcher.build_query(make_track(name="周杰伦 - 七里香.flac"))
    assert q.artist == "周杰伦"
    assert q.title == "七里香"
    assert "七里香" in q.keywords


def test_query_track_separates_sources():
    p1 = FakeProvider("A", [dict(source="A", title="七里香", artist="周杰伦")])
    p2 = FakeProvider("B", [dict(source="B", title="七里香", artist="群星")])
    cands, errors = matcher.query_track(make_track(), [p1, p2])
    assert {c.source for c in cands} == {"A", "B"}
    assert errors == {}


def test_query_track_records_failure():
    p1 = FakeProvider("A", [dict(source="A", title="七里香")])
    p2 = FakeProvider("B", fail=True)
    cands, errors = matcher.query_track(make_track(), [p1, p2])
    assert len(cands) == 1
    assert "B" in errors


def test_match_track_ranks_and_caches(conn):
    track = make_track(id=None)
    track.id = db.upsert_track(conn, track)
    provider = FakeProvider(
        "A",
        [
            dict(source="A", title="七里香", artist="周杰伦", album="七里香", duration=299.0, track="2"),
            dict(source="A", title="七里香", artist="群星", album="翻唱", duration=250.0, track="7"),
        ],
    )
    result = matcher.match_track(conn, track, [provider])
    assert len(result.candidates) == 2
    assert result.candidates[0].artist == "周杰伦"
    assert result.candidates[0].score > result.candidates[1].score

    # 第二次应命中缓存，不再请求
    again = matcher.match_track(conn, track, [provider])
    assert again.from_cache is True
    assert len(provider.calls) == 1


def test_match_track_force_bypasses_cache(conn):
    track = make_track(id=None)
    track.id = db.upsert_track(conn, track)
    provider = FakeProvider("A", [dict(source="A", title="七里香")])
    matcher.match_track(conn, track, [provider])
    matcher.match_track(conn, track, [provider], force=True)
    assert len(provider.calls) == 2


def test_prematch_skips_already_cached(conn):
    t1 = make_track(id=None, path="/m/a.ape", name="01 晴天.ape")
    t1.id = db.upsert_track(conn, t1)
    t2 = make_track(id=None, path="/m/b.ape", name="02 七里香.ape")
    t2.id = db.upsert_track(conn, t2)
    provider = FakeProvider("A", [dict(source="A", title="晴天")])

    db.replace_candidates(conn, t1.id, [Candidate(source="A", title="cached")])
    stats = matcher.prematch(conn, [t1, t2], [provider])
    assert stats["skipped"] == 1
    assert stats["matched"] == 1


def test_remember_choice_persists(conn):
    track = make_track(id=None)
    track.id = db.upsert_track(conn, track)
    cand = Candidate(source="A", title="七里香", artist="周杰伦", album="七里香")
    matcher.remember_choice(conn, track, cand)
    hit = matcher.cached_for_correction(conn, track)
    assert hit is not None
    assert hit.album == "七里香"


# ------------------------------------------------------------ imagesize


def _png(width: int, height: int) -> bytes:
    return (
        imagesize.PNG_SIG
        + b"\x00\x00\x00\rIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )


def test_png_size():
    assert imagesize.image_size(_png(1000, 1000)) == (1000, 1000)


def test_small_cover_flagged():
    assert imagesize.is_small(_png(300, 300)) is True
    assert imagesize.is_small(_png(1000, 1000)) is False


def test_unknown_image_returns_zero():
    assert imagesize.image_size(b"not an image") == (0, 0)
    assert imagesize.image_size(b"") == (0, 0)


# --------------------------------------------------------------- lyrics


LRC = """[00:12.34]窗外的麻雀
[00:18.02]你说这一句
[04:52.00]最后一句
"""


def test_lyrics_last_timestamp():
    assert lyrics.last_timestamp(LRC) == pytest.approx(292.0)


def test_lyrics_timeline_ok():
    check = lyrics.validate(LRC, duration=299.0)
    assert check.has_timeline is True
    assert check.ok is True


def test_lyrics_timeline_misaligned():
    check = lyrics.validate(LRC, duration=400.0)
    assert check.ok is False
    assert "错位" in check.reason


def test_lyrics_ending_early_is_normal():
    """歌曲有尾奏时，歌词提前十几秒结束是正常的，不该报警。"""
    check = lyrics.validate(LRC, duration=309.0)  # 末尾 292，早 17 秒
    assert check.ok is True


def test_lyrics_far_too_early_still_flagged():
    check = lyrics.validate(LRC, duration=420.0)  # 早 108 秒
    assert check.ok is False


def test_lyrics_without_timeline_is_allowed_but_flagged():
    check = lyrics.validate("纯文本歌词\n没有时间", duration=200.0)
    assert check.has_timeline is False
    assert check.ok is True
    assert "不会滚动" in check.reason


def test_lyrics_empty():
    check = lyrics.validate("", duration=200.0)
    assert check.ok is False


def test_lyrics_offset_exceeds_song():
    check = lyrics.validate("[09:00.00]超长", duration=60.0)
    assert check.ok is False

"""状态机测试（对应验收 A-03）。"""
from __future__ import annotations

from app.core.models import (
    STATUS_CONFLICT,
    STATUS_EMPTY,
    STATUS_IGNORED,
    STATUS_NO_COVER,
    STATUS_NO_LYRICS,
    STATUS_READY,
    Track,
)
from app.core.state import compute_status, is_conflicted


def t(**kw) -> Track:
    base = dict(path="/m/a.flac", name="a.flac")
    base.update(kw)
    return Track(**base)


def test_all_empty_is_empty_status():
    assert compute_status(t()) == STATUS_EMPTY


def test_has_tags_no_cover():
    track = t(tag_title="晴天", tag_artist="周杰伦", tag_album="叶惠美")
    assert compute_status(track, [track]) == STATUS_NO_COVER


def test_has_cover_no_lyrics():
    track = t(tag_title="晴天", tag_artist="周杰伦", has_cover=True)
    assert compute_status(track, [track]) == STATUS_NO_LYRICS


def test_complete_is_ready():
    track = t(tag_title="晴天", tag_artist="周杰伦", has_cover=True, has_lyrics=True)
    assert compute_status(track, [track]) == STATUS_READY


def test_ignored_is_preserved():
    track = t(tag_title="晴天", tag_artist="周杰伦", status=STATUS_IGNORED)
    assert compute_status(track, [track]) == STATUS_IGNORED


def test_conflict_detected_on_minority_album():
    folder = [t(path=f"/m/{i}.flac", tag_album="叶惠美") for i in range(4)]
    odd = t(path="/m/9.flac", tag_album="七里香", has_cover=True, has_lyrics=True)
    folder.append(odd)
    assert is_conflicted(odd, folder) is True
    assert compute_status(odd, folder) == STATUS_CONFLICT


def test_no_conflict_below_track_threshold():
    folder = [t(path="/m/1.flac", tag_album="叶惠美"), t(path="/m/2.flac", tag_album="叶惠美")]
    odd = t(path="/m/3.flac", tag_album="七里香")
    folder.append(odd)
    assert is_conflicted(odd, folder) is False


def test_no_conflict_when_majority_matches():
    folder = [t(path=f"/m/{i}.flac", tag_album="叶惠美") for i in range(5)]
    same = t(path="/m/x.flac", tag_album="叶惠美")
    folder.append(same)
    assert is_conflicted(same, folder) is False


def test_conflict_takes_priority_over_missing_cover():
    folder = [t(path=f"/m/{i}.flac", tag_album="叶惠美") for i in range(4)]
    odd = t(path="/m/9.flac", tag_album="七里香", has_cover=False)
    folder.append(odd)
    assert compute_status(odd, folder) == STATUS_CONFLICT

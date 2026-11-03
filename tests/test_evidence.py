"""六项证据测试（对应验收 A-06 / A-07）。"""
from __future__ import annotations

import pytest

from app.core import evidence
from app.core.models import EVIDENCE_WEIGHTS, AlbumContext, Candidate, Track


def make_track(**kw) -> Track:
    base = dict(
        path="/m/叶惠美/02 七里香.ape",
        name="02 七里香.ape",
        duration=299.0,
        parsed_title="七里香",
        parsed_artist="周杰伦",
        tag_album="",
        tag_artist="",
        tag_title="",
        tag_track="",
    )
    base.update(kw)
    return Track(**base)


def make_cand(**kw) -> Candidate:
    base = dict(
        source="MusicBrainz",
        title="七里香",
        artist="周杰伦",
        album="七里香",
        year="2004",
        track="2",
        duration=299.0,
    )
    base.update(kw)
    return Candidate(**base)


CTX = AlbumContext(album="七里香", artist="周杰伦", hit=7, total=8)


def test_perfect_match_scores_six_of_six():
    track = make_track()
    cand = evidence.apply(track, make_cand(), CTX)
    assert evidence.hit_count(cand.evidence) == 6
    assert cand.score > 0.95


def test_duration_within_three_seconds_is_hit():
    track = make_track(duration=299.0)
    cand = evidence.apply(track, make_cand(duration=301.5), CTX)
    assert cand.evidence["duration"] == 1.0


def test_duration_eight_seconds_off_is_partial_not_hit():
    track = make_track(duration=299.0)
    cand = evidence.apply(track, make_cand(duration=307.0), CTX)
    assert cand.evidence["duration"] == 0.5
    assert evidence.hit_count(cand.evidence) == 5


def test_duration_far_off_is_zero():
    track = make_track(duration=299.0)
    cand = evidence.apply(track, make_cand(duration=331.0), CTX)
    assert cand.evidence["duration"] == 0.0


def test_artist_with_multiple_names_matches():
    track = make_track(parsed_artist="周杰伦、费玉清")
    cand = evidence.apply(track, make_cand(artist="周杰伦"), CTX)
    assert cand.evidence["artist"] == 1.0


def test_wrong_artist_fails():
    track = make_track(parsed_artist="周杰伦")
    cand = evidence.apply(track, make_cand(artist="群星"), CTX)
    assert cand.evidence["artist"] == 0.0


def test_title_containment_is_partial():
    track = make_track(parsed_title="七里香")
    cand = evidence.apply(track, make_cand(title="七里香 (Live)"), CTX)
    assert cand.evidence["title"] == 0.6
    assert evidence.hit_count(cand.evidence) < 6


def test_album_neutral_without_reference():
    track = make_track(tag_album="")
    cand = evidence.apply(track, make_cand(), AlbumContext())
    assert cand.evidence["album"] == 0.5
    assert cand.evidence["context"] == 0.0


def test_context_requires_usable_consensus():
    weak = AlbumContext(album="七里香", hit=1, total=8)
    assert not weak.usable
    track = make_track()
    cand = evidence.apply(track, make_cand(), weak)
    assert cand.evidence["context"] == 0.0


def test_album_mismatch_with_context_is_zero():
    track = make_track()
    cand = evidence.apply(track, make_cand(album="2004 演唱会"), CTX)
    assert cand.evidence["album"] == 0.0
    assert cand.evidence["context"] == 0.0


def test_track_number_from_filename():
    track = make_track(name="02 七里香.ape")
    cand = evidence.apply(track, make_cand(track="2"), CTX)
    assert cand.evidence["track"] == 1.0


def test_track_number_mismatch():
    track = make_track(name="05 七里香.ape")
    cand = evidence.apply(track, make_cand(track="2"), CTX)
    assert cand.evidence["track"] == 0.0


def test_rank_puts_best_first():
    track = make_track()
    good = make_cand(source="A", duration=299.0)
    bad = make_cand(source="B", duration=340.0, artist="群星")
    ranked = evidence.rank(track, [bad, good], CTX)
    assert ranked[0].source == "A"
    assert ranked[0].score > ranked[1].score


def test_weights_sum_to_one():
    assert sum(EVIDENCE_WEIGHTS.values()) == pytest.approx(1.0)


def test_normalize_handles_fullwidth_and_punct():
    assert evidence.normalize("七里香（Live）") == evidence.normalize("七里香(live)")
    assert evidence.normalize("ＡＢＣ") == "abc"

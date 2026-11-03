"""存储层测试。"""
from __future__ import annotations

from app.core import db
from app.core.models import STATUS_NO_COVER, Candidate, Draft, Track


def make_track(path="/m/a.flac", **kw) -> Track:
    base = dict(path=path, name="a.flac", size=10, mtime=1, fmt="FLAC", duration=200.0)
    base.update(kw)
    return Track(**base)


def test_upsert_is_idempotent(conn):
    t1 = make_track()
    id1 = db.upsert_track(conn, t1)
    t2 = make_track(tag_title="晴天")
    id2 = db.upsert_track(conn, t2)
    assert id1 == id2
    assert len(db.list_tracks(conn)) == 1
    assert db.get_track(conn, id1).tag_title == "晴天"


def test_count_by_status(conn):
    db.upsert_track(conn, make_track("/m/a.flac", status="empty"))
    db.upsert_track(conn, make_track("/m/b.flac", status=STATUS_NO_COVER))
    db.upsert_track(conn, make_track("/m/c.flac", status=STATUS_NO_COVER))
    counts = db.count_by_status(conn)
    assert counts["all"] == 3
    assert counts[STATUS_NO_COVER] == 2
    assert counts["empty"] == 1


def test_list_filters_by_status_and_keyword(conn):
    db.upsert_track(conn, make_track("/m/a.flac", tag_title="晴天", status="ready"))
    db.upsert_track(conn, make_track("/m/b.flac", tag_title="夜曲", status="empty"))
    assert len(db.list_tracks(conn, statuses=["ready"])) == 1
    assert len(db.list_tracks(conn, keyword="夜曲")) == 1


def test_ignored_excluded_by_default(conn):
    db.upsert_track(conn, make_track("/m/a.flac", status="ignored"))
    assert db.list_tracks(conn) == []
    assert len(db.list_tracks(conn, include_ignored=True)) == 1


def test_draft_roundtrip(conn):
    tid = db.upsert_track(conn, make_track())
    draft = Draft(track_id=tid)
    draft.set_field("title", "晴天", "MusicBrainz")
    db.save_draft(conn, draft)

    loaded = db.load_draft(conn, tid)
    assert loaded.fields["title"]["value"] == "晴天"
    assert loaded.fields["title"]["source"] == "MusicBrainz"

    loaded.drop_field("title")
    db.save_draft(conn, loaded)
    assert db.load_draft(conn, tid).is_empty()


def test_candidates_replace_and_load(conn):
    tid = db.upsert_track(conn, make_track())
    db.replace_candidates(conn, tid, [Candidate(source="A", title="晴天", score=0.9)])
    db.replace_candidates(conn, tid, [Candidate(source="B", title="夜曲", score=0.5)])
    loaded = db.load_candidates(conn, tid)
    assert len(loaded) == 1
    assert loaded[0].source == "B"


def test_corrections_memory(conn):
    cand = Candidate(source="A", title="晴天", artist="周杰伦")
    db.remember_correction(conn, "晴天 周杰伦", cand)
    db.remember_correction(conn, "晴天 周杰伦", cand)
    hit = db.lookup_correction(conn, "晴天 周杰伦")
    assert hit is not None and hit.artist == "周杰伦"
    rows = db.list_corrections(conn)
    assert rows[0][2] == 2  # hits 累加


def test_meta_roundtrip(conn):
    db.set_meta(conn, "library_root", r"F:\Music")
    assert db.get_meta(conn, "library_root") == r"F:\Music"
    assert db.get_meta(conn, "missing", "dflt") == "dflt"


def test_update_tags_syncs_columns(conn):
    tid = db.upsert_track(conn, make_track())
    db.update_tags(conn, tid, {"title": "七里香", "artist": "周杰伦"})
    track = db.get_track(conn, tid)
    assert track.tag_title == "七里香"
    assert track.tag_artist == "周杰伦"


def test_delete_tracks(conn):
    db.upsert_track(conn, make_track("/m/a.flac"))
    db.upsert_track(conn, make_track("/m/b.flac"))
    assert db.delete_tracks(conn, ["/m/a.flac"]) == 1
    assert len(db.list_tracks(conn)) == 1

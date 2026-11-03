"""批量自动打标签的核心判断测试。"""
from __future__ import annotations

import os

import pytest

from app.core import autotag, db, evidence, matcher, scan
from app.core.models import AlbumContext, Candidate, Track
from app.providers.base import BaseProvider
from conftest import make_wav


def make_track(**kw) -> Track:
    base = dict(path="/m/七里香/02 七里香.ape", name="02 七里香.ape",
                duration=299.0, parsed_title="七里香", parsed_artist="周杰伦")
    base.update(kw)
    return Track(**base)


def cand(**kw) -> Candidate:
    base = dict(source="测试源", title="七里香", artist="周杰伦", album="七里香",
                year="2004", track="2", duration=299.0)
    base.update(kw)
    return Candidate(**base)


CTX = AlbumContext(album="七里香", artist="周杰伦", hit=7, total=8)


def prepare(track, cands, ctx=CTX):
    evidence.rank(track, cands, ctx)
    return cands


def test_perfect_match_is_exact():
    track = make_track()
    cands = prepare(track, [cand()])
    fields, reason, level = autotag.decide(track, cands, CTX)
    assert fields is not None
    assert fields["title"] == "七里香"
    assert level == "exact"
    assert reason == ""


def test_artist_mismatch_still_written_as_similar():
    """按用户要求：歌名对得上就写，不再要求歌手也吻合。"""
    track = make_track()
    cands = prepare(track, [cand(artist="群星", album="翻唱合辑")])
    fields, reason, level = autotag.decide(track, cands, CTX)
    assert fields is not None
    assert fields["title"] == "七里香"
    assert level == "similar"


def test_duration_mismatch_still_written_as_similar():
    track = make_track(duration=299.0)
    cands = prepare(track, [cand(duration=331.0)])
    fields, _reason, level = autotag.decide(track, cands, CTX)
    assert fields is not None
    assert level in ("similar", "exact")


def test_too_few_evidences_is_rejected():
    track = make_track(tag_album="", parsed_artist="周杰伦")
    cands = prepare(track, [cand(track="", album="")], AlbumContext())
    fields, reason, _level = autotag.decide(track, cands, AlbumContext(), min_hits=6)
    assert fields is None
    assert "证据不足" in reason


def test_title_only_file_is_inferred_when_duration_matches():
    """文件名没有歌手时，靠「歌名完全一致 + 时长一致」推断。"""
    track = make_track(parsed_artist="", tag_artist="", name="02 七里香.ape")
    cands = prepare(track, [cand(track="2")], AlbumContext())
    fields, reason, level = autotag.decide(track, cands, AlbumContext())
    assert fields is not None
    assert level == "inferred"


def test_title_contained_is_accepted():
    """歌名被包含（七里香 / 七里香 (Live)）也算对上，直接写。"""
    track = make_track(parsed_artist="", tag_artist="", parsed_title="七里香")
    cands = prepare(track, [cand(title="七里香 (Live)")], AlbumContext())
    fields, _reason, level = autotag.decide(track, cands, AlbumContext())
    assert fields is not None
    assert level == "similar"


def test_different_song_is_skipped():
    """歌名完全对不上就是另一首歌，跳过。"""
    track = make_track(parsed_title="七里香")
    cands = prepare(track, [cand(title="晴天")], CTX)
    fields, reason, _level = autotag.decide(track, cands, CTX)
    assert fields is None
    assert "歌名" in reason


def test_no_candidates_is_rejected():
    fields, reason, _level = autotag.decide(make_track(), [], CTX)
    assert fields is None
    assert reason == "没有候选"


class FakeProvider(BaseProvider):
    def __init__(self, name, items, lyrics_text=""):
        self.name = name
        self.items = items
        self.lyrics_text = lyrics_text

    def search(self, query):
        return [Candidate(**c) for c in self.items]

    def fetch_lyrics(self, cand):
        return self.lyrics_text or None


def test_plan_only_collects_acceptable_tracks(conn):
    good = make_track(path="/m/a.ape", name="01 七里香.ape")
    bad = make_track(path="/m/b.ape", name="02 别的.ape", parsed_title="别的")
    good.id = db.upsert_track(conn, good)
    bad.id = db.upsert_track(conn, bad)

    provider = FakeProvider("测试源", [
        dict(source="测试源", title="七里香", artist="周杰伦", album="七里香",
             duration=299.0, track="1"),
    ])
    outcome = autotag.plan(conn, [good, bad], [provider], use_cache=False)
    assert outcome.total == 2
    assert len(outcome.pendings) == 1
    assert outcome.pendings[0].track.path == good.path
    assert outcome.skipped >= 1


def test_plan_respects_field_selection(conn):
    """批量前勾了哪些字段，就只写哪些。"""
    good = make_track(path="/m/a.ape", name="01 七里香.ape")
    good.id = db.upsert_track(conn, good)

    provider = FakeProvider("测试源", [
        dict(source="测试源", title="七里香", artist="周杰伦", album="七里香",
             duration=299.0, track="1"),
    ])
    outcome = autotag.plan(conn, [good], [provider], use_cache=False,
                           fields={"title"})
    assert len(outcome.pendings) == 1
    assert outcome.pendings[0].fields == {"title": "七里香"}


def test_plan_skips_when_selected_fields_empty(conn):
    good = make_track(path="/m/a.ape", name="01 七里香.ape")
    good.id = db.upsert_track(conn, good)
    provider = FakeProvider("测试源", [
        dict(source="测试源", title="七里香", artist="周杰伦", duration=299.0, track="1"),
    ])
    outcome = autotag.plan(conn, [good], [provider], use_cache=False,
                           fields={"album"})
    assert outcome.pendings == []
    assert outcome.skipped == 1
    track = make_track()
    track.id = db.upsert_track(conn, track)

    class Boom(BaseProvider):
        name = "爆炸源"

        def search(self, query):
            raise RuntimeError("网络炸了")

    outcome = autotag.plan(conn, [track], [Boom()], use_cache=False)
    assert outcome.failed + outcome.skipped + len(outcome.review) >= 1
    assert outcome.summary_text().startswith("共 1 首")


def test_run_writes_files_end_to_end(conn, tmp_path):
    music = tmp_path / "music"
    path = make_wav(str(music / "01 晴天.wav"), seconds=0.2)
    scan.scan(conn, str(music))
    track = db.get_track_by_path(conn, path)

    provider = FakeProvider("测试源", [
        dict(source="测试源", title="晴天", artist="周杰伦", album="叶惠美",
             duration=0.2, track="1"),
    ])
    outcome = autotag.run(conn, [track], [provider], str(music),
                          use_cache=False)
    assert outcome.written == 1

    from app.core import probe

    assert probe.probe(path).title == "晴天"


def test_fetch_lyrics_prefers_timeline():
    track = make_track()
    provider = FakeProvider("A", [], lyrics_text="[00:01.00]第一句\n[00:05.00]第二句")
    text = autotag.fetch_lyrics_for(track, [cand(source="A")], [provider])
    assert text.startswith("[00:01.00]")


def test_plan_attaches_lyrics_when_tag_mode(conn):
    good = make_track(path="/m/a.ape", name="01 七里香.ape")
    good.id = db.upsert_track(conn, good)
    provider = FakeProvider(
        "测试源",
        [dict(source="测试源", title="七里香", artist="周杰伦",
              album="七里香", duration=299.0, track="1")],
        lyrics_text="[00:01.00]窗外的麻雀\n[00:05.00]你说这一句",
    )
    outcome = autotag.plan(conn, [good], [provider], use_cache=False,
                           lyrics_mode=autotag.LYRICS_TAG)
    assert len(outcome.pendings) == 1
    assert "窗外的麻雀" in (outcome.pendings[0].lyrics or "")
    assert outcome.lyrics_map == {}


def test_plan_collects_lyrics_for_file_mode(conn):
    good = make_track(path="/m/a.ape", name="01 七里香.ape")
    good.id = db.upsert_track(conn, good)
    provider = FakeProvider(
        "测试源",
        [dict(source="测试源", title="七里香", artist="周杰伦",
              album="七里香", duration=299.0, track="1")],
        lyrics_text="[00:01.00]窗外的麻雀",
    )
    outcome = autotag.plan(conn, [good], [provider], use_cache=False,
                           lyrics_mode=autotag.LYRICS_FILE)
    assert outcome.pendings[0].lyrics is None
    assert good.path in outcome.lyrics_map


def test_run_writes_lrc_file(conn, tmp_path):
    music = tmp_path / "music"
    path = make_wav(str(music / "01 晴天.wav"), seconds=0.2)
    scan.scan(conn, str(music))
    track = db.get_track_by_path(conn, path)

    provider = FakeProvider(
        "测试源",
        [dict(source="测试源", title="晴天", artist="周杰伦",
              album="叶惠美", duration=0.2, track="1")],
        lyrics_text="[00:01.00]窗外的麻雀",
    )
    outcome = autotag.run(conn, [track], [provider], str(music),
                          use_cache=False, lyrics_mode=autotag.LYRICS_BOTH)
    assert outcome.written == 1
    assert outcome.lyrics_saved == 1
    from app.core import lyrics as lyrics_mod

    assert os.path.isfile(lyrics_mod.sidecar_path(path))
    assert "歌词文件 1" in outcome.summary_text()
    assert outcome.lyrics_tagged == 1


def test_plan_queries_run_in_parallel(conn):
    """批量查询要并发，不能一首一首排队。"""
    import threading
    import time

    from app.providers.base import BaseProvider

    peak = {"now": 0, "max": 0}
    lock = threading.Lock()

    class Slow(BaseProvider):
        name = "慢源"

        def search(self, query):
            with lock:
                peak["now"] += 1
                peak["max"] = max(peak["max"], peak["now"])
            time.sleep(0.15)
            with lock:
                peak["now"] -= 1
            return [Candidate(source="慢源", title="七里香", artist="周杰伦")]

    tracks = []
    for i in range(6):
        track = make_track(path=f"/m/{i}.ape", name=f"0{i} 七里香.ape")
        track.id = db.upsert_track(conn, track)
        tracks.append(track)

    autotag.plan(conn, tracks, [Slow()], use_cache=False, workers=4)
    assert peak["max"] > 1, f"没有并发，峰值 {peak['max']}"


def test_plan_results_correct_with_parallelism(conn):
    tracks = []
    for i in range(5):
        track = make_track(path=f"/m/p{i}.ape", name="01 七里香.ape")
        track.id = db.upsert_track(conn, track)
        tracks.append(track)

    provider = FakeProvider("测试源", [
        dict(source="测试源", title="七里香", artist="周杰伦",
             album="七里香", duration=299.0, track="1"),
    ])
    outcome = autotag.plan(conn, tracks, [provider], use_cache=False, workers=4)
    assert outcome.total == 5
    assert len(outcome.pendings) == 5


def test_batch_scan_does_not_touch_rejected_files(conn, tmp_path):
    """被判定为"留给人工"的文件必须原样不动（B-04 延伸）。"""
    import hashlib

    music = tmp_path / "music"
    keep = make_wav(str(music / "01 别的歌.wav"), seconds=0.2)
    scan.scan(conn, str(music))
    track = db.get_track_by_path(conn, keep)
    before = hashlib.sha256(open(keep, "rb").read()).hexdigest()

    provider = FakeProvider("测试源", [
        dict(source="测试源", title="七里香", artist="周杰伦", duration=0.2),
    ])
    outcome = autotag.run(conn, [track], [provider], str(music), use_cache=False)
    after = hashlib.sha256(open(keep, "rb").read()).hexdigest()
    assert outcome.written == 0
    assert before == after

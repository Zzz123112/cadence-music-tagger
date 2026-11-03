"""界面测试：单窗口结构、选中即加载、采用候选、保存写回。"""
from __future__ import annotations

import os

import pytest

from app.core import db, probe, scan
from app.core.models import Candidate
from conftest import make_wav


def rows(window) -> list:
    return window.tree.get_children()


def first_track(window):
    return window._rows[rows(window)[0]]


def test_single_window_lists_scanned_tracks(ui):
    ui.refresh_list()
    assert len(rows(ui)) == 3
    assert ui.count_label.cget("text") == "3 / 3 首"


def test_select_track_fills_editor(ui):
    track = first_track(ui)
    ui.load_track(track)
    assert ui.title_label.cget("text") == track.name
    # 文件名 "01 晴天.wav" 解析出的歌名应填进输入框
    assert ui.vars["title"].get() == "晴天"


def test_filter_switches_list(ui):
    ui.set_filter("empty")
    assert len(rows(ui)) == 3
    ui.set_filter("no_cover")
    assert len(rows(ui)) == 0


def test_search_filters_list(ui):
    ui.search_var.set("晴天")
    ui.refresh_list()
    assert len(rows(ui)) == 1


def test_apply_candidate_fills_all_fields(ui):
    ui.load_track(first_track(ui))
    cand = Candidate(source="测试源", title="晴天", artist="周杰伦",
                     album="叶惠美", year="2003", track="1", duration=0.2)
    from app.core import evidence

    evidence.apply(ui.current, cand)
    ui.show_candidates([cand])
    ui.select_candidate(0)
    ui.apply_candidate()
    assert ui.vars["title"].get() == "晴天"
    assert ui.vars["artist"].get() == "周杰伦"
    assert ui.vars["album"].get() == "叶惠美"


def test_candidate_table_shows_full_details(ui):
    ui.load_track(first_track(ui))
    cand = Candidate(source="测试源", title="晴天", artist="周杰伦",
                     album="叶惠美", year="2003", track="1", duration=0.2)
    from app.core import evidence

    evidence.apply(ui.current, cand)
    ui.show_candidates([cand])
    first = ui.cand_tree.get_children()[0]
    values = ui.cand_tree.item(first, "values")
    assert values[1] == "晴天"
    assert values[2] == "周杰伦"
    assert values[3] == "叶惠美"
    assert values[5] == "测试源"


def test_selection_helpers(ui):
    ui.refresh_list()
    ui.select_all()
    assert len(ui.tree.selection()) == 3
    assert ui.selection_label.cget("text") == "已选 3 首"
    ui.clear_selection()
    assert len(ui.tree.selection()) == 0
    assert ui.selection_label.cget("text") == "未选"
    ui.tree.selection_set(rows(ui)[0])
    ui.invert_selection()
    assert len(ui.tree.selection()) == 2


def test_save_writes_file_and_refreshes(ui):
    track = first_track(ui)
    ui.load_track(track)
    ui.vars["title"].set("晴天")
    ui.vars["artist"].set("周杰伦")
    ui.save_current()
    tk_root = ui.root
    tk_root.update_idletasks()

    assert probe.probe(track.path).title == "晴天"
    refreshed = db.get_track_by_path(ui.ctx.conn, track.path)
    assert refreshed.tag_title == "晴天"
    assert "已保存" in ui.status.cget("text")


def test_save_and_next_moves_selection(ui):
    ui.refresh_list()
    first = rows(ui)[0]
    ui.tree.selection_set(first)
    ui.tree.focus(first)
    ui.load_track(ui._rows[first])
    ui.vars["title"].set("晴天")
    ui.save_current(advance=True)
    assert "已保存" in ui.status.cget("text")


def test_skip_marks_ignored(ui):
    track = first_track(ui)
    ui.load_track(track)
    ui.skip_current()
    assert db.get_track(ui.ctx.conn, track.id).status == "ignored"


def test_no_providers_shows_hint(ui):
    ui.load_track(first_track(ui))
    assert "关闭" in ui.match_hint.cget("text")


def test_settings_dialog_opens(ui):
    from app.ui_tk.settings_dialog import SettingsDialog

    dialog = SettingsDialog(ui.root, ui.ctx, ui)
    try:
        assert dialog.winfo_exists()
    finally:
        dialog.grab_release()
        dialog.destroy()


def test_worker_runs_background_task(ui):
    result = {}
    ui.worker.submit(lambda: 21 * 2, lambda value: result.setdefault("v", value))
    assert ui.worker.wait_idle(3.0)
    assert result["v"] == 42


def test_batch_targets_defaults_to_filtered_list(ui):
    ui.refresh_list()
    assert len(ui._batch_targets()) == 3
    first = rows(ui)[0]
    ui.tree.selection_set(first)
    assert len(ui._batch_targets()) == 1


def test_batch_auto_tag_writes_confident_tracks(ui, monkeypatch):
    """批量流程：确认框自动点「是」，进度窗用桩，跑完文件应被写入。"""
    import app.ui_tk.main_window as mw
    from app.core.models import Candidate
    from app.providers.base import BaseProvider

    class FakeProvider(BaseProvider):
        name = "测试源"

        def search(self, query):
            return [Candidate(source="测试源", title="晴天", artist="周杰伦",
                              album="叶惠美", duration=0.2)]

    class DummyOptions:
        def __init__(self, *a, **k):
            self.result = ["title", "artist", "album"]
            self.lyrics_mode = "none"

    class DummyDialog:
        def __init__(self, *a, **k):
            self.counter = {"done": 0, "total": 1}
            self.cancelled = False

        def grab_release(self):
            pass

        def destroy(self):
            pass

    monkeypatch.setattr(mw.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(mw.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(mw.messagebox, "showwarning", lambda *a, **k: None)
    monkeypatch.setattr(mw, "BatchDialog", DummyDialog)
    monkeypatch.setattr(mw, "BatchOptionsDialog", DummyOptions)
    monkeypatch.setattr(ui.root, "wait_window", lambda *a, **k: None)

    ui.ctx.settings["sources"] = {"MusicBrainz": True}
    monkeypatch.setattr(type(ui.ctx), "providers",
                        property(lambda self: [FakeProvider()]))

    ui.refresh_list()
    ui.tree.selection_set(rows(ui)[0])
    ui.batch_auto_tag()
    assert ui.worker.wait_idle(5.0)
    assert "自动写入" in ui.status.cget("text") or "共" in ui.status.cget("text")


def test_save_lyrics_file_writes_sidecar(ui, monkeypatch):
    from app.core import lyrics
    from conftest import make_wav

    track = first_track(ui)
    ui.load_track(track)
    ui.lyrics_view.delete("1.0", "end")
    ui.lyrics_view.insert("1.0", "[00:01.00]窗外的麻雀")
    import app.ui_tk.main_window as mw

    monkeypatch.setattr(mw.messagebox, "askyesno", lambda *a, **k: True)
    ui.save_lyrics_file()
    target = lyrics.sidecar_path(track.path)
    import os

    assert os.path.isfile(target)
    with open(target, encoding="utf-8") as fh:
        assert "窗外的麻雀" in fh.read()


def test_thumbnails_include_sources_without_url(ui, monkeypatch):
    """网易云搜索不返回封面地址，缩略图不能因此跳过它。"""
    from app.core.models import Candidate
    from app.providers.base import BaseProvider

    called = []

    class LazyCover(BaseProvider):
        name = "懒封面源"

        def fetch_cover(self, cand, min_size=0):
            called.append(cand.title)
            return _png_bytes()

    ui.load_track(first_track(ui))
    ui.show_candidates([
        Candidate(source="懒封面源", title="A"),
        Candidate(source="懒封面源", title="B"),
    ])
    monkeypatch.setattr(type(ui.ctx), "providers",
                        property(lambda self: [LazyCover()]))
    ui._start_thumbs(ui.candidates)
    assert ui.worker.wait_idle(5.0)
    assert sorted(called) == ["A", "B"]  # 并发拉取，顺序不定


def test_lyrics_save_falls_back_when_folder_denied(ui, monkeypatch):
    """音乐文件夹不可写时，应提供一条能走通的保存路径。"""
    import app.ui_tk.main_window as mw

    track = first_track(ui)
    ui.load_track(track)
    ui.lyrics_view.delete("1.0", "end")
    ui.lyrics_view.insert("1.0", "[00:01.00]窗外的麻雀")

    def deny(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(mw.lyrics_mod, "save_lrc", deny)
    monkeypatch.setattr(mw.messagebox, "askyesno", lambda *a, **k: True)

    ui.save_lyrics_file()
    fallback = os.path.join(ui.ctx.data_dir, "lyrics",
                            os.path.basename(mw.lyrics_mod.sidecar_path(track.path)))
    assert os.path.isfile(fallback)
    with open(fallback, encoding="utf-8") as fh:
        assert "窗外的麻雀" in fh.read()


def _png_bytes(color=(200, 60, 60)) -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (80, 80), color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_cover_falls_back_to_next_source(ui, monkeypatch):
    """第一个源没有封面时，应自动往下问，而不是直接报失败。"""
    from app.core.models import Candidate
    from app.providers.base import BaseProvider

    class NoCover(BaseProvider):
        name = "无图源"

        def fetch_cover(self, cand, min_size=0):
            return None

    class HasCover(BaseProvider):
        name = "有图源"

        def fetch_cover(self, cand, min_size=0):
            return _png_bytes()

    ui.load_track(first_track(ui))
    ui.show_candidates([
        Candidate(source="无图源", title="晴天"),
        Candidate(source="有图源", title="晴天"),
    ])
    monkeypatch.setattr(type(ui.ctx), "providers",
                        property(lambda self: [NoCover(), HasCover()]))
    ui._cover_bytes = None
    ui.cycle_cover()
    assert ui.worker.wait_idle(5.0)
    assert ui._cover_bytes is not None
    assert "有图源" in ui.status.cget("text")


def test_lyrics_skips_junk_and_prefers_timeline(ui, monkeypatch):
    from app.core.models import Candidate
    from app.providers.base import BaseProvider

    class Junk(BaseProvider):
        name = "废歌词源"

        def fetch_lyrics(self, cand):
            return "纯音乐，请欣赏"

    class Plain(BaseProvider):
        name = "纯文本源"

        def fetch_lyrics(self, cand):
            return "第一句歌词\n第二句歌词\n第三句歌词\n第四句歌词"

    class Timed(BaseProvider):
        name = "带时间轴源"

        def fetch_lyrics(self, cand):
            return "[00:01.00]第一句\n[00:05.00]第二句"

    ui.load_track(first_track(ui))
    ui.show_candidates([
        Candidate(source="废歌词源", title="x", raw_id="1"),
        Candidate(source="纯文本源", title="x", raw_id="2"),
        Candidate(source="带时间轴源", title="x", raw_id="3"),
    ])
    monkeypatch.setattr(type(ui.ctx), "providers",
                        property(lambda self: [Junk(), Plain(), Timed()]))
    ui.fetch_lyrics()
    assert ui.worker.wait_idle(5.0)
    text = ui.lyrics_view.get("1.0", "end")
    assert "00:01" in text
    assert "带时间轴源" in ui.lyric_hint.cget("text")


def test_start_match_prefers_cache(ui, monkeypatch):
    """同一首歌再点开时直接用缓存，不再走网络。"""
    from app.core.models import Candidate
    from app.providers.base import BaseProvider

    calls = []

    class Counting(BaseProvider):
        name = "计数源"

        def search(self, query):
            calls.append(query.keywords)
            return [Candidate(source="计数源", title="晴天")]

    track = first_track(ui)
    db.replace_candidates(ui.ctx.conn, track.id,
                          [Candidate(source="缓存源", title="晴天")])
    ui.ctx.conn.commit()
    monkeypatch.setattr(type(ui.ctx), "providers",
                        property(lambda self: [Counting()]))

    ui.load_track(track)
    ui.worker.wait_idle(3.0)
    assert len(ui.candidates) == 1
    assert ui.candidates[0].source == "缓存源"
    assert "缓存" in ui.match_hint.cget("text")
    assert calls == []


def test_incremental_match_merges_sources(ui):
    """边查边显示：每回来一个源就合并进列表，不等全部完成。"""
    from app.core import evidence
    from app.core.models import Candidate

    ui.load_track(first_track(ui))
    token = ui._match_token
    ui._pending_sources = 2
    ui._source_results = {}

    first = Candidate(source="A", title="晴天", artist="周杰伦", duration=0.2)
    second = Candidate(source="B", title="夜曲", artist="周杰伦", duration=0.2)
    evidence.apply(ui.current, first)
    evidence.apply(ui.current, second)

    ui._on_source_result(token, "A", [first], "")
    assert len(ui.candidates) == 1
    assert "还在查 1 个源" in ui.match_hint.cget("text")

    ui._on_source_result(token, "B", [second], "")
    assert len(ui.candidates) == 2
    assert "2 条候选" in ui.match_hint.cget("text")


def test_stale_match_result_ignored(ui):
    from app.core.models import Candidate

    ui.load_track(first_track(ui))
    stale = ui._match_token - 1
    ui._source_results = {}
    ui._on_source_result(stale, "A", [Candidate(source="A", title="旧")], "")
    assert ui._source_results == {}


def test_cover_cache_roundtrip(tmp_path):
    from app.core import covercache
    from app.core.models import Candidate

    cand = Candidate(source="A", title="晴天", artist="周杰伦", raw_id="1")
    assert covercache.get(str(tmp_path), cand) is None
    covercache.put(str(tmp_path), cand, b"IMAGE")
    assert covercache.get(str(tmp_path), cand) == b"IMAGE"
    assert covercache.clear(str(tmp_path)) == 1


def test_candidate_thumbnail_applied(ui):
    """缩略图挂在树的第一列上（有 Pillow 时）。"""
    import io

    import pytest

    mw = __import__("app.ui_tk.main_window", fromlist=["HAS_PIL"])
    if not mw.HAS_PIL:
        pytest.skip("没有 Pillow")
    from PIL import Image

    from app.core import evidence
    from app.core.models import Candidate

    ui.load_track(first_track(ui))
    cand = Candidate(source="测试源", title="晴天", artist="周杰伦", duration=0.2)
    evidence.apply(ui.current, cand)
    ui.show_candidates([cand])

    buffer = io.BytesIO()
    Image.new("RGB", (100, 100), (200, 40, 40)).save(buffer, format="PNG")
    ui._on_thumbs(ui._thumb_token, [(0, buffer.getvalue())])

    item = ui.cand_tree.get_children()[0]
    assert ui.cand_tree.item(item, "image")

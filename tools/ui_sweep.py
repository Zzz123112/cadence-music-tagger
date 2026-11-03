"""全流程自检：把界面上的按钮/选项都点一遍，收集回调异常。

运行：python tools/ui_sweep.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import tkinter as tk
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter.messagebox as messagebox  # noqa: E402

messagebox.showinfo = lambda *a, **k: None
messagebox.showerror = lambda *a, **k: None
messagebox.showwarning = lambda *a, **k: None

from app.core import scan  # noqa: E402
from app.core.models import Candidate  # noqa: E402
from app.ui_tk.batch_glass import GlassBatchDialog  # noqa: E402
from app.ui_tk.main_window_v2 import MainWindow  # noqa: E402
from app.ui_tk.settings_glass import GlassSettingsDialog  # noqa: E402
from app.ui_tk.state import open_context  # noqa: E402

ERRORS: list[str] = []


def make_wav(path: str, seconds: float = 0.4) -> None:
    import wave

    frames = int(44100 * seconds)
    with wave.open(path, "wb") as fh:
        fh.setnchannels(2)
        fh.setsampwidth(2)
        fh.setframerate(44100)
        fh.writeframes(b"\x00\x00" * 2 * frames)


def step(name: str, fn) -> None:
    try:
        fn()
        print(f"  ok   {name}")
    except Exception as exc:  # noqa: BLE001
        ERRORS.append(f"{name}: {type(exc).__name__}: {exc}")
        print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
        traceback.print_exc()


class Event:
    def __init__(self, y: int = 0, x: int = 0) -> None:
        self.y, self.x = y, x
        self.x_root, self.y_root = 100, 100
        self.delta = 0


def main() -> int:
    work = tempfile.mkdtemp(prefix="sweep-")
    for i in range(3):
        make_wav(os.path.join(work, f"0{i} 晴天 - 周杰伦.wav"))

    root = tk.Tk()
    root._skip_fade = True
    root.report_callback_exception = lambda exc, val, tb: ERRORS.append(
        f"Tk 回调异常: {val}"
    )

    ctx = open_context(data_dir=tempfile.mkdtemp(prefix="sweep-data-"),
                       library_root=work)
    scan.scan(ctx.conn, work)
    window = MainWindow(ctx, root)
    root.update()
    print("[主窗口]")

    step("选中曲目", lambda: window._select_track(0))
    step("刷新列表", window.refresh_list)

    for key in ("all", "no_cover", "no_lyrics", "empty"):
        step(f"筛选 {key}", lambda k=key: window.set_filter(k))
    window.set_filter("all")

    step("搜索框输入", lambda: (window.search.delete(0, "end"),
                            window.search.insert(0, "晴天"),
                            window._draw_rows()))
    step("搜索框清空", lambda: (window.search.delete(0, "end"),
                            window._draw_rows()))

    window.candidates = [
        Candidate(source=s, title="晴天", artist="周杰伦", album="叶惠美",
                  year="2003", track="3", duration=269)
        for s in ("网易云", "酷狗", "Apple Music")
    ]
    step("绘制候选", window._draw_candidates)
    step("点第 1 条候选", lambda: window._on_cand_click(Event(10)))
    step("点第 3 条候选", lambda: window._on_cand_click(Event(120)))
    step("候选悬停", lambda: window._on_cand_motion(Event(40)))
    step("候选滚轮", lambda: window._on_cand_wheel(Event()))

    step("应用到字段", lambda: window._on_cand_click(Event(10)))
    step("跳过", window.skip_current)
    step("重新搜索", window.start_match)
    step("保存当前", window.save_current)
    step("保存并下一首", window.save_and_next)

    print("[设置窗口]")
    dialog = None

    def open_settings():
        nonlocal dialog
        dialog = GlassSettingsDialog(root, ctx, window)
        window._show_dialog(dialog)

    step("打开设置", open_settings)

    def toggle_all():
        for var in dialog._vars.values():
            var.set(not var.get())
        root.update()

    step("切换全部勾选框", toggle_all)
    step("保存设置", dialog._save)
    step("关闭设置", dialog.close)
    step("关闭后无 grab", lambda: (_ for _ in ()).throw(
        AssertionError("grab 未释放")) if root.grab_current() else None)

    print("[批量窗口]")
    batch = None

    def open_batch():
        nonlocal batch
        batch = GlassBatchDialog(root, ctx, window.tracks[:2], window)
        window._show_dialog(batch)

    step("打开批量", open_batch)
    step("全不选", lambda: batch._set_all(False))
    step("全选", lambda: batch._set_all(True))
    for mode in ("none", "tag", "file", "both"):
        step(f"歌词模式 {mode}", lambda m=mode: batch.lyrics_var.set(m))
    step("开始批量", batch.start)
    for _ in range(40):
        root.update()
        if not getattr(batch, "_running", False):
            break
    step("关闭批量", batch.close)

    print("[窗口行为]")
    step("缩小窗口", lambda: (root.geometry("980x640"), root.update()))
    step("放大窗口", lambda: (root.geometry("1180x820"), root.update()))
    step("拖动开始", lambda: window._start_move(Event()))
    step("拖动一步", window._drag_tick)
    step("拖动结束", window._end_move)
    step("最小化", window._minimize)

    def restore_window():
        from app.ui_tk import win_frame

        win_frame.restore(window.root)
        root.update()

    step("还原窗口", restore_window)
    step("主题切换", lambda: window.apply_theme("dark") if hasattr(
        window, "apply_theme") else None)

    step("关闭主窗口", window._confirm_close)

    print()
    if ERRORS:
        print(f"发现 {len(ERRORS)} 个问题：")
        for item in ERRORS:
            print("  -", item)
        return 1
    print("全部通过，没有回调异常")
    return 0


if __name__ == "__main__":
    sys.exit(main())




"""全面自检：真实网络 + 连点压力 + 批量执行 + 窗口操作。

用法：python tools/full_check.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter.messagebox as messagebox  # noqa: E402

messagebox.showinfo = lambda *a, **k: None
messagebox.showerror = lambda *a, **k: None

from app.core import db, scan  # noqa: E402
from app.core.models import Track  # noqa: E402
from app.ui_tk.batch_glass import GlassBatchDialog  # noqa: E402
from app.ui_tk.main_window_v2 import MainWindow  # noqa: E402
from app.ui_tk.settings_glass import GlassSettingsDialog  # noqa: E402
from app.ui_tk.state import open_context  # noqa: E402

ISSUES: list[str] = []


def make_wav(path: str, seconds: float = 0.5, title: str = "") -> None:
    import wave

    frames = int(44100 * seconds)
    with wave.open(path, "wb") as fh:
        fh.setnchannels(2)
        fh.setsampwidth(2)
        fh.setframerate(44100)
        fh.writeframes(b"\x00\x00" * 2 * frames)


def report(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{('  ' + detail) if detail else ''}")
    if not ok:
        ISSUES.append(f"{name}: {detail}")


def pump(root, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update()
        time.sleep(0.01)


def main() -> int:
    work = tempfile.mkdtemp(prefix="full-")
    names = ["01 晴天 - 周杰伦.wav", "02 七里香 - 周杰伦.wav",
             "03 夜曲 - 周杰伦.wav", "04 稻香 - 周杰伦.wav",
             "05 简单爱 - 周杰伦.wav", "06 告白气球 - 周杰伦.wav"]
    for name in names:
        make_wav(os.path.join(work, name), title=name)

    root = tk.Tk()
    root._skip_fade = True
    errors: list[str] = []
    root.report_callback_exception = lambda e, v, t: errors.append(str(v))

    ctx = open_context(data_dir=tempfile.mkdtemp(prefix="full-data-"),
                       library_root=work)
    scan.scan(ctx.conn, work)
    window = MainWindow(ctx, root)
    pump(root, 0.5)

    print("[1] 连点压力（真实网络）")
    start = time.perf_counter()
    for i in range(len(window.tracks)):
        window._select_track(i)
        root.update()
    click_cost = time.perf_counter() - start
    report("连点 6 首主线程耗时 < 1.2s", click_cost < 1.2,
           f"{click_cost*1000:.0f} ms")

    # 等最后一次搜索出结果
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        root.update()
        text = window.status_label.cget("text")
        if "个源" in text and "还在查" not in text and "搜索中" not in text:
            break
        time.sleep(0.02)
    status = window.status_label.cget("text")
    report("最终结果已显示", "个源" in status or "条候选" in status, status)
    report("候选来自多个源", len({c.source for c in window.candidates}) >= 2,
           f"{len({c.source for c in window.candidates})} 个源")
    report("没有堆积未完成任务", not window._match_pending, "pending 已清")

    print("[2] 搜索期间操作窗口")
    window.start_match()
    window._start_resize("e", type("E", (), {"x_root": 900, "y_root": 500})())
    window._end_resize()
    window.set_filter("empty")
    window.set_filter("all")
    report("搜索中缩放/筛选不报错", not errors, str(errors[:1]))

    print("[3] 批量自动标记（真实网络）")
    before = {t.id: (t.tag_title, t.tag_artist) for t in window.tracks}
    dialog = GlassBatchDialog(root, ctx, window.tracks[:2], window)
    window._show_dialog(dialog)
    pump(root, 0.3)
    dialog._set_all(False)
    dialog.field_vars["title"].set(True)
    dialog.lyrics_var.set("none")
    report("全不选后只有歌名勾上",
           [k for k, v in dialog.field_vars.items() if v.get()] == ["title"])
    dialog.start()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and dialog._running:
        root.update()
        time.sleep(0.02)
    report("批量跑完", not dialog._running, dialog.hint.cget("text")[:40])
    if dialog.outcome is not None:
        report("批量有结果", True, dialog.outcome.summary_text()[:40])
    dialog.close()

    print("[4] 只勾歌名时不应改歌手")
    changed_artist = []
    for track in window.tracks[:2]:
        fresh = db.get_track(ctx.conn, track.id)
        old = before.get(track.id, ("", ""))
        if fresh and fresh.tag_artist and fresh.tag_artist != old[1]:
            changed_artist.append((track.name, old[1], fresh.tag_artist))
    report("歌手未被改动", not changed_artist, str(changed_artist[:1]))

    print("[5] 设置窗口开关数据源")
    window.refresh_list()
    settings = GlassSettingsDialog(root, ctx, window)
    window._show_dialog(settings)
    pump(root, 0.3)
    sources = [k for k in settings._vars if k.startswith("source:")]
    report("数据源列表完整", len(sources) >= 8, f"{len(sources)} 个")
    settings._vars[sources[0]].set(False)
    settings._save()
    report("关闭数据源后立即生效",
           sources[0].split(":", 1)[1] not in [p.name for p in ctx.providers])
    settings._vars[sources[0]].set(True)
    settings._save()
    settings.close()

    print("[6] 保存一首（写入流程）")
    window._select_track(0)
    pump(root, 0.2)
    window.save_current()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        root.update()
        if window.status_label.cget("text").startswith("已保存"):
            break
        time.sleep(0.02)
    report("保存有结果", "已保存" in window.status_label.cget("text")
           or "失败" in window.status_label.cget("text"),
           window.status_label.cget("text"))

    print("[7] 回调异常")
    report("没有 Tk 回调异常", not errors, str(errors[:2]))

    window._confirm_close()

    print()
    if ISSUES:
        print(f"发现 {len(ISSUES)} 个问题：")
        for item in ISSUES:
            print("  -", item)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

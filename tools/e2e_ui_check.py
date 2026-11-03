"""端到端检查：用真实音乐库跑界面链路（选中 → 候选 → 封面 → 歌词）。"""
from __future__ import annotations

import os
import sys
import tempfile
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import scan
from app.ui_tk.main_window import MainWindow
from app.ui_tk.state import open_context


def main(folder: str) -> int:
    ctx = open_context(data_dir=tempfile.mkdtemp(prefix="mt-e2e-"))
    root = tk.Tk()
    root.withdraw()
    window = MainWindow(ctx, root)

    ctx.library_root = folder
    scan.scan(ctx.conn, folder)
    window.refresh_list()
    root.update()
    print(f"库内 {len(window._rows)} 首")

    for index in range(2):
        items = window.tree.get_children()
        if index >= len(items):
            break
        track = window._rows[items[index]]
        window.load_track(track)
        window.worker.wait_idle(40)
        root.update()
        print(f"\n[{track.name}]")
        print(f"  候选 {len(window.candidates)} 条；提示：{window.match_hint.cget('text')}")

        window._cover_bytes = None
        window.cycle_cover()
        window.worker.wait_idle(40)
        root.update()
        size = len(window._cover_bytes or b"")
        print(f"  封面：{'OK ' + str(size) + ' bytes' if size else '没有取到'}｜{window.status.cget('text')}")

        window.fetch_lyrics()
        window.worker.wait_idle(40)
        root.update()
        text = window.lyrics_view.get("1.0", "end").strip()
        print(f"  歌词：{len(text)} 字符｜{window.lyric_hint.cget('text')}")
        if text:
            print(f"    首行：{text.splitlines()[0][:60]}")

    root.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else r"F:\音乐"))

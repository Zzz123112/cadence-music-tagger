"""批量自动标记（毛玻璃版）：选字段 → 跑 → 看结果，一个窗口做完。"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from ..core import autotag
from . import visual
from .glass_dialog import GlassDialog

FIELD_KEYS = (
    ("title", "歌名"),
    ("artist", "歌手"),
    ("album", "专辑"),
    ("year", "年份"),
    ("track", "音轨"),
)

LYRICS_CHOICES = (
    (autotag.LYRICS_NONE, "不处理"),
    (autotag.LYRICS_TAG, "写入标签（播放器直接显示）"),
    (autotag.LYRICS_FILE, "存成 .lrc 文件（和歌曲同目录）"),
    (autotag.LYRICS_BOTH, "两者都要"),
)

COVER_CHOICES = (
    (autotag.COVER_NONE, "不处理"),
    (autotag.COVER_TAG, "写入文件标签（内嵌封面）"),
    (autotag.COVER_FILE, "存成 cover.jpg（和歌曲同目录）"),
    (autotag.COVER_BOTH, "两者都要"),
)


class GlassBatchDialog(GlassDialog):
    def __init__(self, parent, ctx, tracks, window=None):
        super().__init__(parent, "批量自动标记", width=520, height=720,
                         subtitle=f"将处理 {len(tracks)} 首")
        self.ctx = ctx
        self.window = window
        self.tracks = list(tracks)
        self.outcome = None
        self._running = False
        self._build()

    def _build(self) -> None:
        body = self.body
        tk.Label(
            body,
            text="只写入勾选的内容；没勾的不动。\n歌名对得上就直接写，完全对不上的才跳过。",
            bg=self.bg, fg=self.palette["text2"], font=(visual.FONT, 8),
            justify="left", anchor="w",
        ).pack(fill="x", pady=(0, 6))

        self.section(body, "改哪些字段", pady=(4, 3))
        self.field_vars: dict[str, tk.BooleanVar] = {}
        for key, label in FIELD_KEYS:
            self.field_vars[key] = self.check(body, label, True)

        row = tk.Frame(body, bg=self.bg)
        row.pack(fill="x", pady=(4, 6))
        self.button(row, "全选", lambda: self._set_all(True)).pack(side="left")
        self.button(row, "全不选", lambda: self._set_all(False)).pack(side="left", padx=6)

        self.section(body, "歌词怎么处理")
        self.lyrics_var = tk.StringVar(value=autotag.LYRICS_NONE)
        for value, label in LYRICS_CHOICES:
            self.radio(body, label, value, self.lyrics_var)

        self.section(body, "封面怎么处理")
        self.cover_var = tk.StringVar(value=autotag.COVER_TAG)
        for value, label in COVER_CHOICES:
            self.radio(body, label, value, self.cover_var)

        self.hint = tk.Label(body, text="", bg=self.bg, fg=self.palette["text2"],
                             font=(visual.FONT, 8), anchor="w", justify="left")
        self.hint.pack(fill="x", pady=(8, 2))

        self.progress = tk.Canvas(body, height=6, bg=self.bg, highlightthickness=0)
        self.progress.pack(fill="x", pady=(2, 6))
        self._progress_value = 0.0

        foot = tk.Frame(body, bg=self.bg)
        foot.pack(fill="x", side="bottom", pady=(6, 0))
        self.run_btn = self.button(foot, "开始", self.start, primary=True)
        self.run_btn.pack(side="right")
        self.close_btn = self.button(foot, "关闭", self.close)
        self.close_btn.pack(side="right", padx=6)

    def _set_all(self, value: bool) -> None:
        for var in self.field_vars.values():
            var.set(value)

    # ------------------------------------------------------------ 执行

    def start(self) -> None:
        if self._running:
            return
        fields = {key for key, var in self.field_vars.items() if var.get()}
        mode = self.lyrics_var.get()
        cover_mode = self.cover_var.get()
        if not fields and mode == autotag.LYRICS_NONE and cover_mode == autotag.COVER_NONE:
            messagebox.showinfo(
                "批量自动标记",
                "至少选一个字段，或选一种歌词 / 封面处理方式",
                parent=self)
            return

        self._running = True
        self.hint.configure(text="准备中…")
        self._set_progress(0.0)
        tracks = list(self.tracks)
        total = max(1, len(tracks))

        from .worker import BackgroundWorker

        # 进度回调是在工作线程里被调用的，必须走队列回主线程；
        # 直接调 self.after 属于跨线程操作 Tk，会静默失效或报错。
        self._worker = BackgroundWorker(self.winfo_toplevel())

        def progress(done: int, _total: int) -> None:
            def apply() -> None:
                self._set_progress(done / total)
                self.hint.configure(text=f"处理中 {done}/{len(tracks)}")

            try:
                self._worker.post(apply)
            except Exception:
                pass

        def work():
            return autotag.run(
                self.ctx.conn, tracks, self.ctx.providers,
                self.ctx.library_root or "",
                progress=progress,
                fallback_dir=self.ctx.data_dir,
                fields=fields,
                lyrics_mode=mode,
                cover_mode=cover_mode,
            )

        self._worker.submit(work, self._done, self._failed)

    def _failed(self, exc) -> None:
        self._running = False
        self.hint.configure(text=f"失败：{exc}")

    def _done(self, outcome) -> None:
        self._running = False
        self.outcome = outcome
        self._set_progress(1.0)
        self.hint.configure(text=outcome.summary_text())
        if self.window is not None:
            self.window.current = None
            self.window.selected_index = -1
            self.window.refresh_list()
        detail = ""
        if outcome.review:
            preview = "\n".join(f"· {name}：{reason}" for name, reason in outcome.review[:8])
            if len(outcome.review) > 8:
                preview += f"\n… 还有 {len(outcome.review) - 8} 首"
            detail = "\n\n需要看一眼的：\n" + preview
        messagebox.showinfo("批量完成", outcome.summary_text() + detail, parent=self)

    def _set_progress(self, ratio: float) -> None:
        self._progress_value = max(0.0, min(1.0, ratio))
        canvas = self.progress
        canvas.delete("all")
        width = canvas.winfo_width() or 400
        canvas.create_rectangle(0, 1, width, 5, fill=_slightly(self.bg), outline="")
        filled = int(width * self._progress_value)
        if filled > 0:
            canvas.create_rectangle(0, 1, filled, 5,
                                    fill=self.palette["accent"], outline="")


def _slightly(base: str) -> str:
    base = base.lstrip("#")
    r, g, b = (int(base[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % (max(0, r - 14), max(0, g - 14), max(0, b - 14))

"""主界面：一张列表 + 一个编辑面板 + 批量自动打标签。

视觉上按卡片分区：浅灰底 + 白色卡片 + 发丝线，避免「一堆控件平铺」的简陋感。
"""
from __future__ import annotations

import io
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..core import autotag, covercache, db, elevate, imagesize, lockers, lyrics as lyrics_mod, matcher, scan
from ..core.models import STATUS_LABELS, Candidate, Track
from ..core.parse_name import parse_name
from ..write import service
from ..write.service import PendingWrite
from . import style as S
from .settings_dialog import SettingsDialog
from .style import flat_button
from .worker import BackgroundWorker

try:  # 有 Pillow 就显示真实缩略图，没有就退回文字
    from PIL import Image, ImageTk

    HAS_PIL = True
except Exception:  # pragma: no cover
    HAS_PIL = False

FIELD_KEYS = [
    ("title", "歌名"),
    ("artist", "歌手"),
    ("album", "专辑"),
    ("year", "年份"),
    ("track", "曲序"),
]

FILTERS = [
    ("all", "全部"),
    ("no_cover", "缺封面"),
    ("no_lyrics", "缺歌词"),
    ("empty", "空标签"),
]


class BatchDialog(tk.Toplevel):
    """批量进度：不阻塞界面，能看到跑到第几首。"""

    def __init__(self, parent, title: str, total: int):
        super().__init__(parent)
        self.title(title)
        self.geometry("360x150")
        self.configure(bg=S.CARD)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.counter = {"done": 0, "total": max(1, total)}
        self.cancelled = False

        tk.Label(self, text=title, bg=S.CARD, fg=S.T1, font=S.FONT_BOLD).pack(pady=(18, 6))
        self.detail = tk.Label(self, text=f"0 / {total}", bg=S.CARD, fg=S.T2, font=S.FONT_SMALL)
        self.detail.pack()
        self.bar = ttk.Progressbar(self, maximum=100, mode="determinate", length=280)
        self.bar.pack(pady=12)
        flat_button(self, "取消", self._cancel, bg=S.H2, font=S.FONT_SMALL).pack()
        self._poll()

    def _cancel(self) -> None:
        self.cancelled = True
        self.destroy()

    def update_progress(self, done: int, total: int) -> None:
        self.counter["done"] = done
        self.counter["total"] = max(1, total)

    def _poll(self) -> None:
        done = self.counter["done"]
        total = self.counter["total"]
        self.detail.configure(text=f"{done} / {total}")
        self.bar["value"] = min(100, done * 100 / total)
        if self.winfo_exists() and not self.cancelled:
            self.after(150, self._poll)


class BatchOptionsDialog(tk.Toplevel):
    """批量前先问一句：这次只改哪些字段。"""

    def __init__(self, parent, count: int, defaults: set[str] | None = None):
        super().__init__(parent)
        self.title("批量自动打标签")
        self.geometry("400x470")
        self.configure(bg=S.CARD)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.result: list[str] | None = None
        self.lyrics_mode: str = autotag.LYRICS_NONE

        tk.Label(self, text=f"将处理 {count} 首", bg=S.CARD, fg=S.T1,
                 font=S.FONT_BOLD).pack(anchor="w", padx=20, pady=(16, 2))
        tk.Label(
            self,
            text="只写入勾选的内容；没勾的不动。\n证据不够的会跳过，不会硬写。",
            bg=S.CARD, fg=S.T2, font=S.FONT_SMALL, justify="left",
        ).pack(anchor="w", padx=20, pady=(0, 8))

        tk.Label(self, text="改哪些字段", bg=S.CARD, fg=S.T3,
                 font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", padx=20)
        defaults = defaults or {key for key, _ in FIELD_KEYS}
        self.vars: dict[str, tk.BooleanVar] = {}
        for key, label in FIELD_KEYS:
            var = tk.BooleanVar(value=key in defaults)
            ttk.Checkbutton(self, text=label, variable=var).pack(anchor="w", padx=22)
            self.vars[key] = var

        row = tk.Frame(self, bg=S.CARD)
        row.pack(fill="x", padx=20, pady=(4, 8))
        flat_button(row, "全选", lambda: self._set_all(True), bg=S.H2,
                    font=S.FONT_SMALL).pack(side="left")
        flat_button(row, "全不选", lambda: self._set_all(False), bg=S.H2,
                    font=S.FONT_SMALL).pack(side="left", padx=6)

        tk.Label(self, text="歌词怎么处理", bg=S.CARD, fg=S.T3,
                 font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w", padx=20, pady=(6, 2))
        self.lyrics_var = tk.StringVar(value=autotag.LYRICS_NONE)
        for value, label in (
            (autotag.LYRICS_NONE, "不处理"),
            (autotag.LYRICS_TAG, "写入标签（播放器直接显示）"),
            (autotag.LYRICS_FILE, "存成 .lrc 文件（和歌曲同目录）"),
            (autotag.LYRICS_BOTH, "两者都要"),
        ):
            ttk.Radiobutton(self, text=label, value=value,
                            variable=self.lyrics_var).pack(anchor="w", padx=22)

        actions = tk.Frame(self, bg=S.CARD)
        actions.pack(fill="x", padx=20, pady=12, side="bottom")
        ttk.Button(actions, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="开始", style="Primary.TButton",
                   command=self._confirm).pack(side="right", padx=8)

    def _set_all(self, value: bool) -> None:
        for var in self.vars.values():
            var.set(value)

    def _confirm(self) -> None:
        chosen = [key for key, var in self.vars.items() if var.get()]
        self.lyrics_mode = self.lyrics_var.get()
        if not chosen and self.lyrics_mode == autotag.LYRICS_NONE:
            messagebox.showinfo("批量自动打标签", "至少选一个字段，或选一种歌词处理方式")
            return
        self.result = chosen
        self.destroy()


class MainWindow:
    def __init__(self, ctx, root: tk.Tk | None = None):
        self.root = root or tk.Tk()
        self.ctx = ctx
        self.worker = BackgroundWorker(self.root)
        self.current: Track | None = None
        self.candidates: list[Candidate] = []
        self._filter = "all"
        self._rows: dict[str, Track] = {}
        self._search_job = None
        self._cover_list: list[Candidate] = []
        self._cover_index = 0
        self._cover_bytes: bytes | None = None
        self._cover_photo = None
        self._thumbs: dict[int, object] = {}
        self._thumb_token = 0
        self._cover_seq = 0
        self._match_token = 0
        self._source_results: dict[str, list[Candidate]] = {}
        self._pending_sources = 0
        self._lyrics_text = ""
        self._loading = False
        self._retrying = False

        self.root.title("音乐标签管家")
        self.root.geometry("1200x780")
        self.root.minsize(1000, 640)
        self.root.configure(bg=S.BG)
        S.setup(self.root)

        self._build()
        self.refresh_list()
        if self.ctx.library_root:
            self.path_label.configure(text=self.ctx.library_root)
            self.set_status("点「扫描」读取音乐文件")
        else:
            self.set_status("先点右上角「更改」选择音乐文件夹")
        self._show_empty(True)
        self.root.after(600, self._offer_elevation_once)

    # ================================================================ 布局

    def _build(self) -> None:
        self._build_top()
        self._build_status()

        body = tk.Frame(self.root, bg=S.BG)
        body.pack(fill="both", expand=True, padx=14, pady=(12, 8))

        self.left_card = self._card(body)
        self.left_card.configure(width=352)
        self.left_card.pack(side="left", fill="y")
        self.left_card.pack_propagate(False)
        self._build_left(self.left_card)

        self.right_card = self._card(body)
        self.right_card.pack(side="left", fill="both", expand=True, padx=(12, 0))
        self._build_right(self.right_card)

        self._bind_keys()

    def _card(self, parent) -> tk.Frame:
        frame = tk.Frame(parent, bg=S.CARD, highlightbackground="#E3E3E8",
                         highlightthickness=1, bd=0)
        return frame

    def _divider(self, parent, pady=(10, 10)) -> None:
        line = tk.Frame(parent, bg=S.HAIR, height=1)
        line.pack(fill="x", pady=pady)

    def _build_top(self) -> None:
        top = tk.Frame(self.root, bg=S.CARD, height=56,
                       highlightbackground=S.HAIR, highlightthickness=1)
        top.pack(fill="x", side="top")
        top.pack_propagate(False)

        tk.Label(top, text="音乐标签管家", bg=S.CARD, fg=S.T1,
                 font=("Microsoft YaHei UI", 11, "bold")).pack(side="left", padx=(18, 16))

        self.path_label = tk.Label(top, text="未选择文件夹", bg=S.CARD,
                                   fg=S.T2, font=S.FONT_SMALL)
        self.path_label.pack(side="left")
        flat_button(top, "更改", self.choose_folder, bg=S.H2,
                    font=S.FONT_SMALL).pack(side="left", padx=8)
        flat_button(top, "扫描", self.run_scan, bg=S.H2,
                    font=S.FONT_SMALL).pack(side="left")
        flat_button(top, "设置", self.open_settings, bg=S.H2,
                    font=S.FONT_SMALL).pack(side="right", padx=18)

    def _build_status(self) -> None:
        bar = tk.Frame(self.root, bg=S.CARD, height=32,
                       highlightbackground=S.HAIR, highlightthickness=1)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)
        self.status = tk.Label(bar, text="", bg=S.CARD, fg=S.T2,
                               font=S.FONT_SMALL, anchor="w")
        self.status.pack(fill="x", padx=18, pady=7)

    def _build_left(self, parent) -> None:
        head = tk.Frame(parent, bg=S.CARD)
        head.pack(fill="x", padx=16, pady=(14, 8))
        tk.Label(head, text="曲库", bg=S.CARD, fg=S.T3,
                 font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
        self.count_label = tk.Label(head, text="", bg=S.CARD, fg=S.T3, font=S.FONT_SMALL)
        self.count_label.pack(side="left", padx=8)

        filters = tk.Frame(parent, bg=S.CARD)
        filters.pack(fill="x", padx=12, pady=(0, 8))
        self._filter_buttons: dict[str, tk.Button] = {}
        for key, label in FILTERS:
            button = flat_button(filters, label, lambda k=key: self.set_filter(k),
                                 bg=S.CARD, fg=S.T3, font=S.FONT_SMALL)
            button.configure(padx=8, pady=3)
            button.pack(side="left", padx=(0, 2))
            self._filter_buttons[key] = button
        self._paint_filters()

        search_wrap = tk.Frame(parent, bg=S.CARD)
        search_wrap.pack(fill="x", padx=12, pady=(0, 8))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_a: self._on_search_changed())
        ttk.Entry(search_wrap, textvariable=self.search_var).pack(fill="x")

        table_wrap = tk.Frame(parent, bg=S.CARD)
        table_wrap.pack(fill="both", expand=True, padx=12)
        self.tree = ttk.Treeview(table_wrap, columns=("name", "state"), show="headings",
                                 selectmode="extended")
        self.tree.heading("name", text="文件名")
        self.tree.heading("state", text="状态")
        self.tree.column("name", width=250, anchor="w")
        self.tree.column("state", width=74, anchor="w")
        for status in S.STATUS_TAGS:
            self.tree.tag_configure(status, foreground=S.STATUS_TAGS[status])
        self.tree.tag_configure("odd", background="#FAFAFC")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Down>", lambda _e: self._move(1))
        self.tree.bind("<Up>", lambda _e: self._move(-1))

        bottom = tk.Frame(parent, bg=S.CARD)
        bottom.pack(fill="x", padx=12, pady=10)

        sel_row = tk.Frame(bottom, bg=S.CARD)
        sel_row.pack(fill="x", pady=(0, 8))
        for text, command in (("全选", self.select_all),
                              ("反选", self.invert_selection),
                              ("清空", self.clear_selection)):
            flat_button(sel_row, text, command, bg=S.H2,
                        font=S.FONT_SMALL).pack(side="left", padx=(0, 4))
        self.selection_label = tk.Label(sel_row, text="未选", bg=S.CARD,
                                        fg=S.T3, font=S.FONT_SMALL)
        self.selection_label.pack(side="right")

        self.batch_btn = ttk.Button(bottom, text="批量自动打标签", style="Primary.TButton",
                                    command=self.batch_auto_tag)
        self.batch_btn.pack(fill="x")
        self.batch_hint = tk.Label(bottom, text="不选就处理当前筛选结果（先「全选」最快）",
                                   bg=S.CARD, fg=S.T3, font=S.FONT_SMALL, wraplength=310,
                                   justify="left")
        self.batch_hint.pack(fill="x", pady=(6, 0))

    def _build_right(self, parent) -> None:
        # 底部动作先占位，避免被中间区域挤压（封面变大后曾把「换一张」挤出可视区）
        bottom_bar = tk.Frame(parent, bg=S.CARD)
        bottom_bar.pack(side="bottom", fill="x")
        self._divider(bottom_bar, pady=(0, 0))
        actions = tk.Frame(bottom_bar, bg=S.CARD)
        actions.pack(fill="x", padx=20, pady=10)
        self.file_hint = tk.Label(actions, text="", bg=S.CARD, fg=S.T3, font=S.FONT_SMALL)
        self.file_hint.pack(side="left", pady=8)
        self.save_btn = ttk.Button(actions, text="保存", style="Primary.TButton",
                                   command=self.save_current)
        self.save_btn.pack(side="right", pady=8)
        ttk.Button(actions, text="保存并下一首",
                   command=lambda: self.save_current(True)).pack(side="right", padx=8, pady=8)
        ttk.Button(actions, text="跳过", command=self.skip_current).pack(
            side="right", padx=8, pady=8)

        head = tk.Frame(parent, bg=S.CARD)
        head.pack(fill="x", padx=20, pady=(16, 0))
        self.title_label = tk.Label(head, text="从左边选一首歌", bg=S.CARD, fg=S.T1,
                                    font=("Microsoft YaHei UI", 14, "bold"), anchor="w")
        self.title_label.pack(fill="x")
        self.meta_label = tk.Label(head, text="", bg=S.CARD, fg=S.T2,
                                   font=S.FONT_SMALL, anchor="w")
        self.meta_label.pack(fill="x", pady=(2, 0))

        self._divider(parent, pady=(12, 10))

        # 搜索结果
        match_box = tk.Frame(parent, bg=S.CARD)
        match_box.pack(fill="x", padx=20)
        row = tk.Frame(match_box, bg=S.CARD)
        row.pack(fill="x")
        tk.Label(row, text="搜索结果", bg=S.CARD, fg=S.T3,
                 font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
        self.match_hint = tk.Label(row, text="", bg=S.CARD, fg=S.T3, font=S.FONT_SMALL)
        self.match_hint.pack(side="left", padx=8)
        flat_button(row, "用这条", self.apply_candidate, bg=S.BLUE, fg="#FFFFFF",
                    font=S.FONT_SMALL).pack(side="right")
        flat_button(row, "重搜", self.rematch, bg=S.H2,
                    font=S.FONT_SMALL).pack(side="right", padx=6)

        cols = ("hits", "title", "artist", "album", "dur", "src")
        ttk.Style(self.root).configure("Cand.Treeview", rowheight=50)
        self.cand_tree = ttk.Treeview(match_box, columns=cols, show="tree headings",
                                      height=3, selectmode="browse",
                                      style="Cand.Treeview")
        self.cand_tree.heading("#0", text="封面")
        self.cand_tree.column("#0", width=62, minwidth=62, stretch=False, anchor="center")
        for col, title, width in (
            ("hits", "吻合", 48), ("title", "歌名", 170), ("artist", "歌手", 104),
            ("album", "专辑", 146), ("dur", "时长", 56), ("src", "来源", 88),
        ):
            self.cand_tree.heading(col, text=title)
            self.cand_tree.column(col, width=width, anchor="w", stretch=(col == "album"))
        self.cand_tree.tag_configure("high", foreground=S.GREEN)
        self.cand_tree.tag_configure("low", foreground=S.T3)
        self.cand_tree.tag_configure("hint", foreground=S.T3)
        self.cand_tree.pack(fill="x", pady=6)
        self.cand_tree.bind("<Double-1>", lambda _e: self.apply_candidate())

        self._divider(parent, pady=(10, 8))

        # 字段：两列等宽，第三行单独放曲序
        form = tk.Frame(parent, bg=S.CARD)
        form.pack(fill="x", padx=20)
        form.columnconfigure(1, weight=1, uniform="field")
        form.columnconfigure(3, weight=1, uniform="field")
        self.vars: dict[str, tk.StringVar] = {}
        layout = [
            (0, 0, "title"), (0, 2, "artist"),
            (1, 0, "album"), (1, 2, "year"),
            (2, 0, "track"),
        ]
        labels = dict(FIELD_KEYS)
        for row, col, key in layout:
            tk.Label(form, text=labels[key], bg=S.CARD, fg=S.T2, font=S.FONT_SMALL,
                     anchor="w").grid(row=row, column=col, sticky="w", pady=5)
            var = tk.StringVar()
            self.vars[key] = var
            entry = ttk.Entry(form, textvariable=var)
            entry.grid(row=row, column=col + 1, sticky="ew",
                       padx=(0, 22), pady=5)

        self._divider(parent, pady=(12, 8))

        # 封面 + 歌词
        extra = tk.Frame(parent, bg=S.CARD)
        extra.pack(fill="both", expand=True, padx=20)

        cover_box = tk.Frame(extra, bg=S.CARD)
        cover_box.pack(side="left", fill="y", padx=(0, 18))
        cover_head = tk.Frame(cover_box, bg=S.CARD)
        cover_head.pack(fill="x")
        tk.Label(cover_head, text="封面", bg=S.CARD, fg=S.T3,
                 font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
        flat_button(cover_head, "换一张", self.cycle_cover, bg=S.H2,
                    font=S.FONT_SMALL).pack(side="right")
        cover_frame = tk.Frame(cover_box, bg="#F2F2F7", width=160, height=160)
        cover_frame.pack(pady=6)
        cover_frame.pack_propagate(False)
        self.cover_label = tk.Label(cover_frame, bg="#F2F2F7", fg=S.T3, text="无封面")
        self.cover_label.place(relx=0.5, rely=0.5, anchor="center")
        flat_button(cover_box, "换一张", self.cycle_cover, bg=S.H2,
                    font=S.FONT_SMALL).pack(fill="x")

        lyric_box = tk.Frame(extra, bg=S.CARD)
        lyric_box.pack(side="left", fill="both", expand=True)
        head_row = tk.Frame(lyric_box, bg=S.CARD)
        head_row.pack(fill="x")
        tk.Label(head_row, text="歌词", bg=S.CARD, fg=S.T3,
                 font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
        self.lyric_hint = tk.Label(head_row, text="", bg=S.CARD, fg=S.T3, font=S.FONT_SMALL)
        self.lyric_hint.pack(side="left", padx=8)
        flat_button(head_row, "存为 .lrc", self.save_lyrics_file, bg=S.H2,
                    font=S.FONT_SMALL).pack(side="right", padx=6)
        flat_button(head_row, "获取歌词", self.fetch_lyrics, bg=S.H2,
                    font=S.FONT_SMALL).pack(side="right")
        self.lyrics_view = tk.Text(lyric_box, height=6, bd=0, highlightthickness=1,
                                   highlightbackground=S.HAIR, bg="#FAFAFC", fg=S.T1,
                                   font=S.FONT_SMALL, wrap="word")
        self.lyrics_view.pack(fill="both", expand=True, pady=6)

        self.empty_hint = tk.Label(
            parent, text="左边选一首歌\n右侧会自动查好候选", bg=S.CARD,
            fg=S.T3, font=("Microsoft YaHei UI", 12), justify="center",
        )

    def _bind_keys(self) -> None:
        self.root.bind("<Control-s>", lambda _e: self.save_current())
        self.root.bind("<Control-Return>", lambda _e: self.save_current(True))
        self.root.bind("<F1>", lambda _e: self.show_help())

    def _show_empty(self, flag: bool) -> None:
        if flag:
            self.empty_hint.place(relx=0.5, rely=0.45, anchor="center")
        else:
            self.empty_hint.place_forget()

    # ================================================================ 列表

    def _paint_filters(self) -> None:
        for key, button in self._filter_buttons.items():
            active = key == self._filter
            button.configure(fg=S.BLUE if active else S.T3,
                             bg=S.BLUE_BG if active else S.CARD,
                             font=S.FONT_BOLD if active else S.FONT_SMALL)

    def _on_search_changed(self) -> None:
        if self._search_job:
            self.root.after_cancel(self._search_job)
        self._search_job = self.root.after(250, self.refresh_list)

    def set_filter(self, key: str) -> None:
        self._filter = key
        self._paint_filters()
        self.refresh_list()

    def refresh_list(self) -> None:
        self._search_job = None
        keyword = self.search_var.get().strip()
        tracks = db.list_tracks(
            self.ctx.conn,
            statuses=None if self._filter == "all" else [self._filter],
            keyword=keyword,
        )
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        for index, track in enumerate(tracks):
            iid = str(track.id)
            self._rows[iid] = track
            tags = [track.status]
            if index % 2:
                tags.append("odd")
            self.tree.insert("", "end", iid=iid,
                             values=(track.name, STATUS_LABELS.get(track.status, track.status)),
                             tags=tuple(tags))
        total = db.count_by_status(self.ctx.conn).get("all", 0)
        self.count_label.configure(text=f"{len(tracks)} / {total} 首")
        if self.current and str(self.current.id) in self._rows:
            self.tree.selection_set(str(self.current.id))

    def _move(self, step: int) -> None:
        items = self.tree.get_children()
        if not items:
            return
        current = self.tree.focus()
        index = items.index(current) if current in items else 0
        index = max(0, min(len(items) - 1, index + step))
        self.tree.selection_set(items[index])
        self.tree.focus(items[index])
        self.tree.see(items[index])

    def _on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        self._update_selection_label()
        if self._loading or len(selection) != 1:
            return
        track = self._rows.get(selection[0])
        if track:
            self.load_track(track)

    def _update_selection_label(self) -> None:
        count = len(self.tree.selection())
        self.selection_label.configure(
            text=f"已选 {count} 首" if count else "未选",
            fg=S.BLUE if count else S.T3,
        )

    def selected_tracks(self) -> list[Track]:
        return [self._rows[i] for i in self.tree.selection() if i in self._rows]

    def select_all(self) -> None:
        items = self.tree.get_children()
        if items:
            self.tree.selection_set(items)
            self._update_selection_label()

    def invert_selection(self) -> None:
        current = set(self.tree.selection())
        items = [i for i in self.tree.get_children() if i not in current]
        self.tree.selection_set(items)
        self._update_selection_label()

    def clear_selection(self) -> None:
        self.tree.selection_remove(*self.tree.selection())
        self._update_selection_label()

    # ================================================================ 曲目

    def load_track(self, track: Track) -> None:
        self._show_empty(False)
        self.current = track
        self.ctx.set_current(track)
        self._loading = True
        self.title_label.configure(text=track.name)
        self.meta_label.configure(
            text=f"{track.fmt} · {track.quality_label()} · {self._mmss(track.duration)}"
                f" · {STATUS_LABELS.get(track.status, '')}"
        )
        parsed = parse_name(track.name)
        preset = {
            "title": track.tag_title or parsed.title,
            "artist": track.tag_artist or parsed.artist,
            "album": track.tag_album,
            "year": track.tag_year,
            "track": track.tag_track or parsed.track,
        }
        for key, value in preset.items():
            self.vars[key].set(value or "")
        self._cover_bytes = None
        self._cover_photo = None
        self._cover_list = []
        self._cover_index = 0
        self.cover_label.configure(text="无封面", image="")
        self._lyrics_text = ""
        self.lyrics_view.delete("1.0", "end")
        self.lyric_hint.configure(text="")
        self.file_hint.configure(text="")
        self._loading = False
        self.start_match(preset.get("title", ""), preset.get("artist", ""))

    @staticmethod
    def _mmss(seconds: float) -> str:
        if not seconds:
            return "—"
        total = int(round(seconds))
        return f"{total // 60}:{total % 60:02d}"

    # ================================================================ 匹配

    def start_match(self, title: str, artist: str, force: bool = False) -> None:
        """并发查三个源，谁先回来先显示谁——不用等最慢的那个。

        同一首歌已经查过就直接用本地缓存，回车重搜才走网络。
        """
        if not self.current:
            return
        self.cand_tree.delete(*self.cand_tree.get_children())
        self.match_hint.configure(text="查询中…")
        providers = self.ctx.providers
        if not providers:
            self.match_hint.configure(text="数据源已全部关闭")
            self.show_candidates([])
            return

        cached = [] if force else db.load_candidates(self.ctx.conn, self.current.id or 0)
        if cached:
            from ..core.context import album_context

            folder = db.folder_tracks(self.ctx.conn, os.path.dirname(self.current.path))
            ranked = matcher.rank(self.current, cached,
                                  album_context(self.current, folder))
            self.show_candidates(ranked)
            self.match_hint.configure(text=f"{len(ranked)} 条候选（本地缓存，点重搜更新）")
            return

        from ..providers import search_all

        track = self.current
        query = matcher.build_query(track)
        self._match_token += 1
        token = self._match_token
        self._source_results = {}
        self._pending_sources = len(providers)

        def _on_source(name, items, error):
            self.worker.post(
                lambda n=name, i=items, e=error: self._on_source_result(token, n, i, e)
            )

        def _work():
            search_all(query, providers, on_result=_on_source)
            return None

        self.worker.submit(_work, None, self._on_match_error)

    def _on_source_result(self, token: int, name: str, items, error: str) -> None:
        if token != self._match_token or not self.current:
            return
        self._source_results[name] = items
        self._pending_sources = max(0, self._pending_sources - 1)

        from ..core.context import album_context

        merged = [c for lst in self._source_results.values() for c in lst]
        folder = db.folder_tracks(self.ctx.conn, os.path.dirname(self.current.path))
        ranked = matcher.rank(self.current, merged, album_context(self.current, folder))
        self.show_candidates(ranked)
        if self.current.id and ranked:
            db.replace_candidates(self.ctx.conn, self.current.id, ranked)
            self.ctx.conn.commit()

        if self._pending_sources:
            self.match_hint.configure(
                text=f"{len(ranked)} 条 · 还在查 {self._pending_sources} 个源"
            )
        elif error:
            self.match_hint.configure(text=f"{len(ranked)} 条 · {name} 失败：{error}")
        else:
            self.match_hint.configure(text=f"{len(ranked)} 条候选")

    def _on_match_error(self, exc) -> None:
        self.match_hint.configure(text=f"查询失败：{exc}")

    def show_candidates(self, cands: list[Candidate]) -> None:
        self.candidates = cands
        self._thumbs.clear()
        self._thumb_token += 1
        self.cand_tree.delete(*self.cand_tree.get_children())
        if not cands:
            self.cand_tree.insert(
                "", "end",
                values=("", "还没有结果，改歌名后点「重搜」", "", "", "", ""),
                tags=("hint",),
            )
            return
        for index, cand in enumerate(cands):
            hits = sum(1 for v in cand.evidence.values() if v >= 0.75)
            tag = "high" if hits >= 5 else ("low" if hits <= 2 else "")
            self.cand_tree.insert(
                "", "end", iid=str(index),
                values=(
                    f"{hits}/6",
                    cand.title,
                    cand.artist or "—",
                    cand.album or "—",
                    self._mmss(cand.duration),
                    cand.source,
                ),
                tags=(tag,) if tag else (),
            )
        children = self.cand_tree.get_children()
        if children:
            self.cand_tree.selection_set(children[0])
            self.cand_tree.focus(children[0])
        self._start_thumbs(cands)

    def _start_thumbs(self, cands: list[Candidate], limit: int = 6) -> None:
        """给前几条候选拉封面缩略图，方便凭图挑。

        注意：不能只挑「已经有 cover_urls」的候选——网易云的搜索接口
        不返回封面地址，要等 fetch_cover 时再查详情，先过滤就把它们漏掉了。
        """
        if not HAS_PIL:
            return
        # 按来源轮流取，避免前 6 条全是 MusicBrainz（它经常没有封面图），
        # 导致后面明明有封面的网易云/QQ 行一张图都不显示。
        buckets: dict[str, list[int]] = {}
        for index, cand in enumerate(cands):
            buckets.setdefault(cand.source, []).append(index)
        queues = list(buckets.values())
        targets: list[tuple[int, Candidate]] = []
        while len(targets) < limit and any(queues):
            for queue in queues:
                if queue and len(targets) < limit:
                    index = queue.pop(0)
                    targets.append((index, cands[index]))
        if not targets:
            return
        token = self._thumb_token
        providers = self.ctx.providers
        data_dir = self.ctx.data_dir

        def _work():
            from concurrent.futures import ThreadPoolExecutor

            def one(item):
                index, cand = item
                cached = covercache.get(data_dir, cand)
                if cached:
                    return index, cached
                provider = next((p for p in providers if p.name == cand.source), None)
                if provider is None:
                    return None
                try:
                    data = provider.fetch_cover(cand)
                except Exception:
                    return None
                if data:
                    covercache.put(data_dir, cand, data)
                    return index, data
                return None

            # 4 路并发，6 张图从 ~2 秒降到 ~0.5 秒
            with ThreadPoolExecutor(max_workers=4) as pool:
                return [r for r in pool.map(one, targets) if r]

        self.worker.submit(_work, lambda payload: self._on_thumbs(token, payload),
                           lambda exc: None)

    def _on_thumbs(self, token: int, payload) -> None:
        if token != self._thumb_token:
            return  # 已经切歌，旧图丢弃
        children = self.cand_tree.get_children()
        for index, data in payload:
            if index >= len(children):
                continue
            try:
                image = Image.open(io.BytesIO(data))
                image.thumbnail((42, 42))
                photo = ImageTk.PhotoImage(image)
            except Exception:
                continue
            self._thumbs[index] = photo
            self.cand_tree.item(children[index], image=photo)

    def select_candidate(self, index: int) -> None:
        """供测试与快捷键使用。"""
        children = self.cand_tree.get_children()
        if 0 <= index < len(children):
            self.cand_tree.selection_set(children[index])
            self.cand_tree.focus(children[index])

    def rematch(self) -> None:
        self.start_match(self.vars["title"].get().strip(),
                         self.vars["artist"].get().strip(), force=True)

    def apply_candidate(self) -> None:
        selection = self.cand_tree.selection()
        if not selection:
            self.set_status("先在列表里选一条结果")
            return
        try:
            index = int(selection[0])
        except ValueError:
            return
        if index >= len(self.candidates):
            return
        cand = self.candidates[index]
        for key in ("title", "artist", "album", "year", "track"):
            value = getattr(cand, key, "")
            if value:
                self.vars[key].set(str(value))
        self._cover_list = [c for c in self.candidates if c.cover_urls]
        self._cover_index = 0
        self.set_status(f"已填入 {cand.source} 的结果，检查后点保存")
        self.cycle_cover()

    # ================================================================ 封面

    def cycle_cover(self) -> None:
        """换一张：依次向候选的源要封面，第一个成功的就用。

        某一个源没有封面（比如 MusicBrainz 那张唱片没有扫描图）不该直接
        判失败，应该继续问下一个源。
        """
        pool = self.candidates
        if not pool:
            self.cover_label.configure(text="没有候选", image="")
            return
        start = self._cover_index % len(pool)
        self._cover_index = (start + 1) % len(pool)
        order = pool[start:] + pool[:start]
        providers = self.ctx.providers
        self._cover_seq += 1
        token = self._cover_seq
        self.cover_label.configure(text="获取中…", image="")

        def _work():
            for cand in order[:8]:
                cached = covercache.get(self.ctx.data_dir, cand)
                if cached:
                    return cached, cand
                provider = next((p for p in providers if p.name == cand.source), None)
                if provider is None:
                    continue
                try:
                    data = provider.fetch_cover(cand)
                except Exception:
                    data = None
                if data:
                    covercache.put(self.ctx.data_dir, cand, data)
                    return data, cand
            return None, None

        self.worker.submit(_work,
                           lambda payload: self._on_cover_result(token, payload),
                           lambda exc: self._on_cover_error(token, exc))

    def _on_cover_result(self, token: int, payload) -> None:
        if token != self._cover_seq:
            return  # 已经换过别的了
        data, cand = payload
        if not data:
            self.cover_label.configure(text="几个源都没找到封面", image="")
            self.set_status("没有取到封面，可以换个搜索词再试")
            return
        self._on_cover_done(data)
        if cand is not None:
            self.set_status(f"封面来自 {cand.source}")

    def _on_cover_error(self, token: int, exc) -> None:
        if token != self._cover_seq:
            return
        self.cover_label.configure(text="获取失败", image="")
        self.set_status(f"封面获取失败：{exc}")

    def _on_cover_done(self, data) -> None:
        if not data:
            self.cover_label.configure(text="没有取到", image="")
            return
        self._cover_bytes = data
        width, height = imagesize.image_size(data)
        if HAS_PIL:
            try:
                image = Image.open(io.BytesIO(data))
                image.thumbnail((176, 176))
                self._cover_photo = ImageTk.PhotoImage(image)
                self.cover_label.configure(image=self._cover_photo, text="")
                return
            except Exception:
                pass
        self.cover_label.configure(text=f"{width}×{height}\n{len(data) // 1024} KB")

    # ================================================================ 歌词

    def fetch_lyrics(self) -> None:
        if not self.candidates:
            self.set_status("先点「重搜」拿到候选")
            return
        self.lyric_hint.configure(text="获取中…")
        cands = list(self.candidates)
        providers = self.ctx.providers

        def _work():
            best = None
            for cand in cands[:8]:
                provider = next((p for p in providers if p.name == cand.source), None)
                if provider is None:
                    continue
                try:
                    text = provider.fetch_lyrics(cand)
                except Exception:
                    text = None
                if not text or len(text.strip()) < 10:
                    continue
                if lyrics_mod.has_timeline(text):
                    return text, cand.source
                stripped = text.strip()
                if best is None and len(stripped) >= 30 and "\n" in stripped:
                    best = (text, cand.source)
            return best or (None, "")

        self.worker.submit(_work, self._on_lyrics_done,
                           lambda exc: self.lyric_hint.configure(text=f"失败：{exc}"))

    def _on_lyrics_done(self, payload) -> None:
        text, source = payload
        if not text:
            self.lyric_hint.configure(text="没有取到歌词")
            return
        self._lyrics_text = text
        self.lyrics_view.delete("1.0", "end")
        self.lyrics_view.insert("1.0", text)
        duration = self.current.duration if self.current else 0.0
        self.lyric_hint.configure(text=f"{source} · {lyrics_mod.validate(text, duration).reason}")

    def save_lyrics_file(self) -> None:
        """把当前歌词框里的内容存成同名 .lrc。"""
        if not self.current:
            self.set_status("先从左边选一首歌")
            return
        text = self.lyrics_view.get("1.0", "end").strip()
        if not text:
            self.set_status("歌词是空的，先获取或粘贴")
            return
        target = lyrics_mod.sidecar_path(self.current.path)
        if os.path.exists(target):
            if not messagebox.askyesno("覆盖确认",
                                       f"{os.path.basename(target)} 已存在，要覆盖吗？"):
                return
        try:
            written = lyrics_mod.save_lrc(self.current.path, text, overwrite=True)
        except PermissionError:
            # 音乐文件夹不可写：给一条能走通的路，而不是只报个错
            fallback = os.path.join(self.ctx.data_dir, "lyrics",
                                    os.path.basename(target))
            if messagebox.askyesno(
                "无法写入音乐文件夹",
                f"系统拒绝写入：\n{os.path.dirname(target)}\n\n"
                f"可以先把歌词存到别处：\n{fallback}\n\n存到那里吗？",
            ):
                try:
                    os.makedirs(os.path.dirname(fallback), exist_ok=True)
                    with open(fallback, "w", encoding="utf-8") as fh:
                        fh.write(text)
                    self.set_status(f"已存到 {fallback}")
                except OSError as exc:
                    messagebox.showerror("保存失败", str(exc))
                return
            path = filedialog.asksaveasfilename(
                title="歌词另存为", defaultextension=".lrc",
                initialfile=os.path.basename(target),
                filetypes=[("歌词文件", "*.lrc"), ("所有文件", "*.*")],
            )
            if not path:
                return
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text)
                self.set_status(f"已存到 {path}")
            except OSError as exc:
                messagebox.showerror("保存失败", str(exc))
            return
        except Exception as exc:
            messagebox.showerror("保存歌词失败", str(exc))
            return
        self.set_status(f"歌词已存为 {os.path.basename(written)}（与歌曲同目录）")

    # ================================================================ 保存

    def save_current(self, advance: bool = False) -> None:
        if not self.current:
            self.set_status("先从左边选一首歌")
            return
        fields = {key: var.get().strip() for key, var in self.vars.items() if var.get().strip()}
        lyrics = self.lyrics_view.get("1.0", "end").strip() or None
        if not fields and not self._cover_bytes and not lyrics:
            self.set_status("没有可保存的内容")
            return
        self.save_btn.configure(state="disabled")
        self.set_status("正在保存…（若系统拦截，会弹出管理员授权窗口，请点「是」）")
        self.root.update_idletasks()
        item = PendingWrite(track=self.current, fields=fields,
                            cover=self._cover_bytes, lyrics=lyrics)
        summary = service.write_batch(
            self.ctx.conn, [item],
            self.ctx.library_root or os.path.dirname(self.current.path),
            fallback_dir=self.ctx.data_dir,
        )
        self.save_btn.configure(state="normal")
        self._log_save(item, summary)
        if summary.get("error"):
            self._report_save_failure(self.current, summary["error"])
            return
        if summary["ok"] == 1:
            refreshed = scan.refresh_one(self.ctx.conn, self.current.path)
            if refreshed:
                self.current = refreshed
            self.refresh_list()
            note = "，跳到下一首" if advance else ""
            if summary.get("via_helper"):
                note = "（经系统程序写入）" + note
            self.set_status("已保存" + note)
            if advance:
                self._move(1)
        else:
            reason = summary["items"][0].error if summary["items"] else "未知原因"
            self._report_save_failure(self.current, reason)

    def _log_save(self, item, summary) -> None:
        """每次保存都留一行记录，成功失败都记，方便事后定位。"""
        import datetime

        try:
            path = os.path.join(self.ctx.data_dir, "write.log")
            status = "OK" if summary.get("ok") else "FAIL"
            method = "helper" if summary.get("via_helper") else "direct"
            reason = summary.get("error") or ""
            if not reason and summary.get("items"):
                reason = summary["items"][0].error or ""
            line = (
                f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {status} "
                f"{method} fields={list(item.fields)} cover={bool(item.cover)} "
                f"lyrics={bool(item.lyrics)} file={item.track.path} {reason}\n"
            )
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            pass

    def _report_save_failure(self, track: Track, reason: str) -> None:
        """保存失败要说清楚是哪一步、哪个格式、为什么。"""
        fmt = track.fmt or "未知"
        self.set_status(f"保存失败（{fmt}）：{reason}")

        # 被占用时，先看能不能一键结束占用再重试
        looks_locked = any(k in reason for k in ("占用", "Permission", "拒绝访问", "WinError 32", "WinError 5"))
        if looks_locked and not self._retrying:
            holders = lockers.find_lockers(track.path)
            if holders:
                names = "\n".join(f"· {name}（PID {pid}）" for pid, name in holders)
                if messagebox.askyesno(
                    "文件被占用",
                    f"这个文件正被以下程序打开：\n\n{names}\n\n"
                    "结束它们并重新保存吗？\n（未保存的播放进度会丢失）",
                ):
                    killed, failed = lockers.kill_lockers(track.path)
                    if killed:
                        self._retrying = True
                        try:
                            self.save_current()
                        finally:
                            self._retrying = False
                        if failed:
                            self.set_status("已结束占用，但这些没能结束：" + "、".join(failed))
                        return
                    if failed:
                        self.set_status("结束失败：" + "、".join(failed))

        detail = (
            f"文件：{os.path.basename(track.path)}\n"
            f"格式：{fmt}\n"
            f"路径：{track.path}\n\n"
            f"原因：{reason}\n\n"
        )
        if "占用" in reason:
            detail += "文件可能被播放器或其他程序占用，关掉那个程序再试。"
        elif "Permission" in reason or "拒绝访问" in reason or "WinError 13" in reason:
            from ..core import environment

            detail += environment.advice_for_permission_denied()
            detail += (
                "\n\n本程序已尝试：直接写 → PowerShell → cmd → 系统后台进程 → 申请管理员权限。\n"
                "如果刚才出现过「用户账户控制」窗口，需要点「是」才会成功。"
            )
            if messagebox.askyesno("无法写入", detail + "\n\n要现在以管理员身份重启吗？"):
                ok, message = elevate.relaunch_as_admin()
                if ok:
                    self.root.destroy()
                else:
                    messagebox.showinfo("重启失败", message)
            return
        elif "只读" in reason or "不支持" in reason:
            detail += "这个格式暂不支持写入，或文件属性是只读。"
        elif "空间" in reason:
            detail += "备份需要的磁盘空间不足，清理一下再试。"
        elif "时长" in reason or "重读" in reason:
            detail += "写入后校验没通过，文件已自动从备份还原。"
        else:
            detail += "可以把这个框的内容发给我，我来定位。"

        log_path = os.path.join(self.ctx.data_dir, "write.log")
        try:
            import datetime

            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {detail}\n")
            detail += f"\n\n详细日志：{log_path}"
        except OSError:
            pass
        messagebox.showerror("保存失败", detail)

    def skip_current(self) -> None:
        if not self.current:
            return
        db.set_status(self.ctx.conn, self.current.id or 0, "ignored")
        self.ctx.conn.commit()
        self.refresh_list()
        self._move(1)

    # ================================================================ 批量

    def _batch_targets(self) -> list[Track]:
        selected = self.selected_tracks()
        if selected:
            return selected
        return [t for t in (self._rows.get(i) for i in self.tree.get_children()) if t]

    def batch_auto_tag(self) -> None:
        targets = self._batch_targets()
        if not targets:
            messagebox.showinfo("批量自动打标签", "列表里没有歌")
            return
        if not self.ctx.providers:
            messagebox.showwarning("批量自动打标签", "数据源全部关闭了，先去设置里打开")
            return

        options = BatchOptionsDialog(self.root, len(targets))
        self.root.wait_window(options)
        if options.result is None:
            return
        chosen_fields = set(options.result)
        lyrics_mode = options.lyrics_mode

        dialog = BatchDialog(self.root, "批量自动打标签", len(targets))
        providers = self.ctx.providers
        library_root = self.ctx.library_root

        def _progress(done, total):
            if dialog.cancelled:
                raise RuntimeError("用户取消")
            dialog.counter["done"] = done
            dialog.counter["total"] = max(1, total)

        def _work():
            return autotag.run(
                self.ctx.conn, targets, providers, library_root,
                progress=_progress, fallback_dir=self.ctx.data_dir,
                fields=chosen_fields, lyrics_mode=lyrics_mode,
            )

        def _done(outcome):
            try:
                dialog.grab_release()
                dialog.destroy()
            except Exception:
                pass
            self.refresh_list()
            self.set_status(outcome.summary_text())
            detail = ""
            if outcome.review:
                preview = "\n".join(f"· {name}：{reason}" for name, reason in outcome.review[:8])
                if len(outcome.review) > 8:
                    preview += f"\n… 还有 {len(outcome.review) - 8} 首"
                detail = "\n\n留给人工的：\n" + preview
            messagebox.showinfo("批量完成", outcome.summary_text() + detail)

        def _error(exc):
            try:
                dialog.grab_release()
                dialog.destroy()
            except Exception:
                pass
            self.set_status(f"批量处理失败：{exc}")
            messagebox.showerror("批量处理失败", str(exc))

        self.set_status(f"正在批量处理 {len(targets)} 首…")
        self.worker.submit(_work, _done, _error)

    # ================================================================ 顶栏

    def choose_folder(self) -> None:
        from tkinter import filedialog

        folder = filedialog.askdirectory(title="选择音乐文件夹",
                                         initialdir=self.ctx.library_root or "")
        if folder:
            self.ctx.library_root = folder
            db.set_meta(self.ctx.conn, "library_root", folder)
            self.ctx.conn.commit()
            self.path_label.configure(text=folder)
            self.run_scan()

    def run_scan(self) -> None:
        if not self.ctx.library_root:
            messagebox.showinfo("提示", "先选择音乐文件夹")
            return
        self.set_status("正在扫描…")
        folder = self.ctx.library_root

        def _work():
            return scan.scan(self.ctx.conn, folder)

        self.worker.submit(_work, self._on_scan_done,
                           lambda exc: self.set_status(f"扫描失败：{exc}"))

    def _offer_elevation_once(self) -> None:
        """启动时问一次：要不要提权运行，省掉每次保存的授权弹窗。"""
        if getattr(self, "_elevation_asked", False):
            return
        self._elevation_asked = True

        import sys

        from ..core import elevate, elevation_policy

        if not elevation_policy.should_offer(
            self.ctx.library_root, sys.argv[1:]
        ):
            return
        if getattr(self.ctx, "elevate_attempted", False):
            return  # 启动时已经试过一次了，不要连续弹两个框

        answer = messagebox.askyesnocancel(
            "需要写入权限",
            "保存标签需要管理员权限。\n\n"
            "选「是」：以管理员身份重启，之后保存不再弹窗（每次启动只授权一次）\n"
            "选「否」：照常运行，但每保存一首都会弹一次授权窗口\n"
            "选「取消」：这次先不处理",
        )
        if answer is True:
            self.ctx.settings["auto_elevate"] = True
            self.ctx.save_settings()
            ok, message = elevate.relaunch_as_admin()
            if ok:
                self.root.destroy()
            else:
                self.set_status(f"提权重启失败：{message}")
        elif answer is False:
            self.ctx.settings["auto_elevate"] = False
            self.ctx.save_settings()

    def _warn_if_readonly(self) -> None:
        """提前告诉用户「这个文件夹写不进去」，别等保存时才失败。"""
        root = self.ctx.library_root
        if not root:
            return
        probe = os.path.join(root, "_mt_perm.tmp")
        try:
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("x")
            os.remove(probe)
        except OSError:
            from ..core import environment

            av = environment.detect_antivirus()
            if av:
                self.set_status(
                    f"当前无法写入音乐文件夹，可能是 {'、'.join(av)} 拦截了。"
                    "把本程序设为「受信任」后重试。"
                )
            else:
                hint = "注意：当前无法写入音乐文件夹（权限受限），保存会失败。"
                if elevate.in_restricted_session():
                    hint += "检测到受限会话——请关掉窗口，从资源管理器双击启动。"
                else:
                    hint += "可用「以管理员身份启动」，或在设置里做「权限自检」。"
                self.set_status(hint)

    def _on_scan_done(self, stats) -> None:
        self.refresh_list()
        self.set_status(
            f"扫描完成：共 {stats['total']} 个，新增 {stats['added']}，"
            f"更新 {stats['updated']}，移除 {stats['removed']}"
        )
        self._warn_if_readonly()

    def open_settings(self) -> None:
        SettingsDialog(self.root, self.ctx, self)

    def load_library_for_shot(self, folder: str) -> None:
        """截图用：指向一个真实文件夹并扫一遍，选中最难看的那个案例。"""
        self.ctx.library_root = folder
        self.path_label.configure(text=folder)
        scan.scan(self.ctx.conn, folder)
        self.refresh_list()
        self.set_status("已扫描，点左边任意一首开始")
        items = self.tree.get_children()
        if items:
            self.tree.selection_set(items[0])
            self.tree.focus(items[0])
            self.load_track(self._rows[items[0]])

    def demo_candidates_for_shot(self) -> None:
        """仅截图用：造几条带封面的候选，展示「封面」列。"""
        if not self.current:
            return
        from ..core import evidence

        rows = [
            ("七里香", "周杰伦", "七里香", 299.0, 1.0),
            ("七里香（现场版）", "周杰伦", "2004 演唱会", 331.0, 0.6),
            ("七里香", "群星", "翻唱合辑", 252.0, 0.2),
        ]
        cands = []
        for title, artist, album, duration, quality in rows:
            cand = Candidate(source="示例源", title=title, artist=artist,
                             album=album, duration=duration)
            cand.evidence = {
                "title": 1.0, "artist": quality, "album": quality,
                "duration": quality, "track": 0.0, "context": 0.0,
            }
            cand.score = round(quality, 2)
            cands.append(cand)
        self.show_candidates(cands)
        if not HAS_PIL:
            return
        payload = []
        for index, color in enumerate(((198, 96, 72), (86, 120, 180), (110, 152, 116))):
            buffer = io.BytesIO()
            Image.new("RGB", (140, 140), color).save(buffer, format="PNG")
            payload.append((index, buffer.getvalue()))
        self._on_thumbs(self._thumb_token, payload)

    def show_help(self) -> None:
        messagebox.showinfo(
            "怎么用",
            "1. 右上角「更改」选音乐文件夹，点「扫描」\n"
            "2. 左边点一首歌，右边会自动查候选\n"
            "3. 点候选里的「用这条」自动填好，也可以直接手改\n"
            "4. 点「保存」写回文件\n\n"
            "批量：多选（Ctrl / Shift）后点「批量自动打标签」，\n"
            "只处理证据足够的，其余会列出来给你人工看。\n\n"
            "Ctrl+S 保存 · Ctrl+Enter 保存并下一首 · F1 帮助",
        )

    def set_status(self, text: str) -> None:
        self.status.configure(text=text)

    def destroy(self) -> None:
        for child in self.root.winfo_children():
            child.destroy()




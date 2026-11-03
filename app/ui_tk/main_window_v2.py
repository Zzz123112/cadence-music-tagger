"""Cadence 主窗口 v2：iOS 风格的毛玻璃界面。

Tkinter 没有原生模糊和半透明，这里的做法是：
- 整个窗口的背景（模糊封面 + 两块玻璃面板 + 圆角）先合成为一张图
- 控件放在面板之上，底色取自合成图对应位置的平均色，视觉上融为一体
- 动画用 after() 逐帧插值：窗口淡入、封面交叉淡入、面板轻微上滑
"""
from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from tkinter import messagebox
from PIL import Image, ImageTk

from ..core import db, matcher, probe, scan
from ..core.context import album_context
from ..core.models import STATUS_LABELS, Candidate, Track

_FONT_CACHE: dict = {}


def _looks_like_image(data: bytes | None) -> bool:
    """挡住平台返回的错误页 / 空数据，别把非图片写进封面缓存。"""
    if not data or len(data) < 32:
        return False
    head = data[:12]
    return (
        head.startswith(b"\xff\xd8")            # JPEG
        or head.startswith(b"\x89PNG\r\n\x1a\n")  # PNG
        or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
        or head.startswith(b"GIF8")
        or head.startswith(b"BM")               # BMP
    )


def _ellipsis(text: str, font_spec, max_px: float) -> str:
    """按像素宽度截断并加省略号。

    Canvas 的 create_text(width=…) 是「换行」不是省略，长标题会折成两行
    压到副标题上——所以这里自己算宽度。
    """
    if not text or max_px <= 8:
        return text
    key = tuple(font_spec)
    measure = _FONT_CACHE.get(key)
    if measure is None:
        from tkinter import font as tkfont

        measure = _FONT_CACHE[key] = tkfont.Font(font=font_spec)
    if measure.measure(text) <= max_px:
        return text
    ell = "…"
    ell_width = measure.measure(ell)
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if measure.measure(text[:mid]) + ell_width <= max_px:
            low = mid
        else:
            high = mid - 1
    return text[:low] + ell
from ..core.parse_name import parse_name
from . import visual
from .worker import BackgroundWorker

PAD = 18
TITLE_H = 62
LEFT_W = 336
GAP = 14


class MainWindow:
    """单窗口界面。"""

    def __init__(self, ctx, root: tk.Tk):
        self.ctx = ctx
        self.root = root
        self.mode = ctx.settings.get("theme", "light")
        self.palette = visual.theme(self.mode)

        self.tracks: list[Track] = []
        self.current: Track | None = None
        self.candidates: list[Candidate] = []
        self.selected_index = -1
        self.selected_cand = -1
        self._photos: dict[str, ImageTk.PhotoImage] = {}
        self._backdrop_cache: Image.Image | None = None
        self._match_token = 0
        self._match_running = False
        self._match_pending = False
        self._match_cancel = None
        self._scroll_offset = 0
        self._anim_jobs: list[str] = []

        # 小屏幕（比如 1366x768 的笔记本）上要能放下
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        self.width = max(960, min(1180, int(screen_w * 0.94)))
        self.height = max(620, min(800, int(screen_h * 0.88)))

        self._setup_window()
        self._build_layers()
        self._build_titlebar()
        self._build_left()
        self._build_right()
        self._build_statusbar()
        self._apply_window_frame()
        self.root.minsize(960, 620)
        self.root.bind("<Configure>", self._on_resize)
        self._resize_job = None
        self.worker = BackgroundWorker(self.root)
        self.refresh_list()
        self._hint_permission()
        self.root.after(80, self._sync_colors)
        self.root.after(60, self._fade_in)

    def _hint_permission(self) -> None:
        """没提权又写不进音乐目录时，在状态栏提示一次（不弹窗）。"""
        try:
            root_dir = self.ctx.library_root
            if not root_dir:
                return
            from ..core import elevation_policy

            if elevation_policy.can_write_dir(root_dir):
                return
            self.status_label.configure(
                text="提示：当前权限写不进音乐文件夹。用桌面的 Cadence 快捷方式（管理员）启动，保存时就不会再弹权限框。"
            )
        except Exception:
            pass

    def _build_statusbar(self) -> None:
        """底部状态栏：所有提示都显示在这里。"""
        bg = self._panel_color("status")
        self.status_label = tk.Label(
            self.root, text="准备就绪", bg=bg, fg=self.palette["text2"],
            font=(visual.FONT, 8), anchor="w", bd=0, padx=10,
        )
        self._place_statusbar()

    # ------------------------------------------------------------ 窗口

    def _setup_window(self) -> None:
        root = self.root
        root.title("Cadence")
        # 必须在第一次设尺寸之前就把边框样式改掉：
        # Tk 会在第一次 geometry() 时把创建时那圈边框尺寸记下来，
        # 之后再改样式，它每次改窗口大小还会把旧值补上去（拖动时宽高乱跳）。
        try:
            from . import win_frame

            # 不再用 overrideredirect：Windows 11 任务栏不收 WS_POPUP 窗口，
            # 会完全没有任务栏按钮/图标。改成正常窗口 + 隐藏标题栏。
            win_frame.prepare(root, rounded=True)
        except Exception:
            pass
        # 不用 -alpha 淡入：它会让窗口变成分层窗口，拖动时重绘跟不上会留残影
        self._center(self.width, self.height)
        root.configure(bg=visual.to_hex(self.palette["window"]))
        # 窗口框架在界面搭好之后再改（见 __init__ 里的 _apply_window_frame）
        root.bind("<Escape>", lambda _e: self._confirm_close())
        root.bind("<ButtonRelease-1>", self._end_move, add="+")
        root.bind("<Motion>", self._on_root_motion, add="+")
        root.bind("<Down>", lambda _e: self._move(1))
        root.bind("<Up>", lambda _e: self._move(-1))
        root.bind("<Control-s>", lambda _e: self.save_current())
        root.bind("<Control-Return>", lambda _e: self.save_and_next())

    def _center(self, width: int, height: int) -> None:
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2 - 20)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _apply_window_frame(self) -> None:
        """把窗口变成"无标题栏但保留原生行为"，并设置图标与系统圆角。"""
        try:
            from . import logo, win_frame

            logo.apply_window_icon(self.root)
            self.root.update_idletasks()
            win_frame.prepare(self.root, rounded=True)
            win_frame.ensure_taskbar(self.root)
            win_frame.enable_dark_titlebar(self.root, self.mode == "dark")
            win_frame.set_border_color(
                self.root, visual.to_hex(self.palette["window"])
            )
        except Exception:
            pass
        # 无边框窗口的任务栏按钮偶尔会丢（Explorer 在窗口创建时就已经决定了），
        # 等窗口真正显示出来以后强制刷新一次，保证图标稳定出现。
        try:
            self.root.bind("<Map>", self._on_first_map, add="+")
        except tk.TclError:
            pass
        # 去掉标题栏后客户区尺寸会变，重新按真实尺寸排一次，避免边缘露白
        try:
            self.root.update_idletasks()
            width, height = self.root.winfo_width(), self.root.winfo_height()
            if width > 200 and height > 200:
                # Tk 内部还记着创建时那圈边框的尺寸，第一次改窗口大小时会自己补上，
                # 表现就是拖一下窗口宽高莫名变一点。这里先让它补完，之后就稳定了。
                self.root.geometry(f"{width}x{height}")
                self.root.update_idletasks()
                width, height = self.root.winfo_width(), self.root.winfo_height()
                self.width, self.height = width, height
                self._apply_size()
        except tk.TclError:
            pass

    def _on_first_map(self, event) -> None:
        """窗口第一次显示时，强制让任务栏重新评估一次。"""
        if getattr(event, "widget", None) is not self.root:
            return
        if getattr(self, "_taskbar_fixed", False):
            return
        self._taskbar_fixed = True
        try:
            from . import logo, win_frame

            logo.apply_window_icon(self.root)
            win_frame.ensure_taskbar(self.root, refresh=True)
        except Exception:
            pass
        # 再补一次：Explorer 有时在窗口显示之后才建任务栏按钮，
        # 这时候才去读图标，晚 600ms 再设一遍能覆盖这种情况
        try:
            self.root.after(600, self._late_taskbar_refresh)
        except tk.TclError:
            pass

    def _late_taskbar_refresh(self) -> None:
        try:
            from . import logo, win_frame

            logo.apply_window_icon(self.root)
            win_frame.ensure_taskbar(self.root)
        except Exception:
            pass

    def _confirm_close(self) -> None:
        try:
            from . import win_frame

            win_frame.uninstall_nc_handler(self.root)
        except Exception:
            pass
        self.root.destroy()

    # ------------------------------------------------------------ 背景层

    def _build_layers(self) -> None:
        self.canvas = tk.Canvas(
            self.root, width=self.width, height=self.height,
            highlightthickness=0, bd=0,
            bg=visual.to_hex(self.palette["window"]),
        )
        self.canvas.place(x=0, y=0)
        self.canvas.bind("<ButtonPress-1>", self._start_move, add="+")
        self.canvas.bind("<B1-Motion>", self._do_move, add="+")
        self._render_background()

    def _panel_boxes(self) -> dict[str, tuple[int, int, int, int]]:
        top = TITLE_H + 8
        status_h = 26
        bottom = self.height - PAD - status_h
        left = (PAD, top, PAD + LEFT_W, bottom)
        right = (PAD + LEFT_W + GAP, top, self.width - PAD, bottom)
        status = (PAD, self.height - PAD - status_h, self.width - PAD,
                  self.height - PAD)
        title = (PAD, 8, self.width - PAD, TITLE_H + 2)
        return {"title": title, "left": left, "right": right, "status": status}

    def _render_background(self) -> None:
        cover = self._backdrop_cover()
        backdrop = visual.compose_backdrop(cover, self.width, self.height, self.mode)
        self._backdrop_cache = backdrop

        image = backdrop.copy()
        boxes = self._panel_boxes()
        for name, box in boxes.items():
            panel = visual.glass_panel(backdrop, box, visual.RADIUS_CARD,
                                       self.mode, strong=(name == "right"))
            image.alpha_composite(panel, (box[0], box[1]))

        # 窗口圆角交给系统（DWM）来做，图片只负责铺满内容
        photo = ImageTk.PhotoImage(image.convert("RGB"))
        self._bg_image = image.convert("RGB")
        self._photos["bg"] = photo
        if getattr(self, "_bg_item", None) is None:
            self._bg_item = self.canvas.create_image(0, 0, anchor="nw", image=photo)
        else:
            self.canvas.itemconfigure(self._bg_item, image=photo)
        self.canvas.tag_lower(self._bg_item)

    def _panel_color(self, name: str) -> str:
        """取面板内部的平均色。

        必须采样「已经合成过玻璃面板」的图，否则拿到的是原始背景色，
        控件底色就会偏灰，和面板对不上（之前界面发灰就是这个原因）。
        """
        image = getattr(self, "_bg_image", None) or self._backdrop_cache
        if image is None:
            return "#f2f2f7"
        box = self._panel_boxes()[name]
        inset = (box[0] + 12, box[1] + 12, box[2] - 12, box[3] - 12)
        if inset[2] <= inset[0] or inset[3] <= inset[1]:
            inset = box
        return visual.sample_color(image, inset)

    # ------------------------------------------------------------ 配色同步

    def _sync_colors(self) -> None:
        """按每个控件自己的位置采样背景色，消除色带。"""
        image = getattr(self, "_bg_image", None)
        if image is None:
            return
        self.root.update_idletasks()
        root_x = self.root.winfo_rootx()
        root_y = self.root.winfo_rooty()

        def sample(widget) -> str:
            x = widget.winfo_rootx() - root_x
            y = widget.winfo_rooty() - root_y
            w = widget.winfo_width()
            h = widget.winfo_height()
            if w <= 2 or h <= 2:
                return ""
            return visual.sample_color(image, (x, y, x + w - 1, y + h - 1))

        def walk(widget) -> None:
            for child in widget.winfo_children():
                if isinstance(child, tk.Text):
                    base = sample(child)
                    if base:
                        child.configure(
                            bg=self._blend_color(
                                base, (255, 255, 255, 140)
                                if self.mode != "dark" else (255, 255, 255, 22)
                            ),
                            fg=self.palette["text"],
                            insertbackground=self.palette["text"],
                        )
                    continue
                if isinstance(child, tk.Entry):
                    if not getattr(child, "_keep_bg", False):
                        base = sample(child)
                        if base:
                            child.configure(
                                bg=self._blend_color(
                                    base, (255, 255, 255, 150)
                                    if self.mode != "dark" else (255, 255, 255, 26)
                                )
                            )
                    continue
                if isinstance(child, tk.Canvas):
                    value = sample(child)
                    if value:
                        child.configure(bg=value)
                    continue
                if not getattr(child, "_keep_bg", False):
                    if isinstance(child, (tk.Frame, tk.Label)):
                        value = sample(child)
                        if value:
                            child.configure(bg=value)
                walk(child)

        walk(self.root)
        # 顶栏单独统一配色，不参与逐控件采样（否则会取样到面板外，出现灰块）
        self._paint_titlebar()
        # 画布需要重绘，让文字用新底色
        self._draw_rows()
        self._draw_candidates()

    def _paint_titlebar(self) -> None:
        """标题栏整体用一个颜色，保证按钮和底板完全同色。"""
        try:
            bg = self._panel_color("title")
        except Exception:
            return
        widgets = [self.title_bar, self._title_right, self._title_brand_box,
                   self._brand_label, self.logo_label]
        widgets += getattr(self, "_title_pills", [])
        for widget in widgets:
            try:
                widget.configure(bg=bg)
            except tk.TclError:
                pass
        for widget in getattr(self, "_title_pills", []):
            self._recolor_pill(widget, bg)

    def _recolor_pill(self, widget, base: str) -> None:
        widget.configure(bg=base)
        hover = self._blend_color(base, (0, 0, 0, 22))
        widget.bind("<Enter>", lambda _e, w=widget, c=hover: w.configure(bg=c))
        widget.bind("<Leave>", lambda _e, w=widget, c=base: w.configure(bg=c))
        widget.bind("<Button-1>", lambda _e, w=widget: getattr(w, "_command", lambda: None)())

    def _blend_color(self, base_hex: str, rgba) -> str:
        r, g, b = _hex_to_rgb(base_hex)
        ratio = rgba[3] / 255
        return "#%02x%02x%02x" % (
            int(r + (rgba[0] - r) * ratio),
            int(g + (rgba[1] - g) * ratio),
            int(b + (rgba[2] - b) * ratio),
        )

    # ------------------------------------------------------------ 标题栏

    def _build_titlebar(self) -> None:
        bg = self._panel_color("title")
        box = self._panel_boxes()["title"]
        self.title_bar = tk.Frame(self.root, bg=bg, height=TITLE_H)
        self.title_bar.place(x=box[0], y=box[1],
                             width=box[2] - box[0], height=box[3] - box[1])
        self.title_bar.bind("<ButtonPress-1>", self._start_move)
        self.title_bar.bind("<B1-Motion>", self._do_move)
        self.title_bar._keep_bg = True

        # 右侧按钮组用 pack，窗口变宽变窄自动靠右
        rightbox = tk.Frame(self.title_bar, bg=bg)
        rightbox.pack(side="right", padx=(0, PAD), pady=0)
        rightbox.bind("<ButtonPress-1>", self._start_move)
        rightbox.bind("<B1-Motion>", self._do_move)
        rightbox._keep_bg = True
        self._title_right = rightbox

        self._title_pills = []
        self._title_buttons = []
        for text, command in (("文件夹", self.choose_folder), ("扫描", self.scan_library),
                              ("批量", self.open_batch), ("设置", self.open_settings)):
            btn = self._pill(rightbox, text, command, bg=bg)
            btn.pack(side="left", padx=(0, 8))
            self._title_buttons.append(btn)
            self._title_pills.append(btn)

        close = self._round_button(rightbox, "✕", self._confirm_close, bg)
        close.pack(side="left", padx=(6, 0))
        mini = self._round_button(rightbox, "—", self._minimize, bg)
        mini.pack(side="left", padx=(4, 0))
        self._title_pills += [close, mini]

        # 标题在左，可拖动
        brand_box = tk.Frame(self.title_bar, bg=bg)
        brand_box.pack(side="left", padx=(PAD + 6, 0))
        brand_box.bind("<ButtonPress-1>", self._start_move)
        brand_box.bind("<B1-Motion>", self._do_move)
        brand_box._keep_bg = True
        self._title_brand_box = brand_box

        self.logo_label = tk.Label(brand_box, bg=bg, bd=0)
        self.logo_label.pack(side="left", padx=(0, 8))
        self.logo_label._keep_bg = True
        self._apply_logo()

        brand = tk.Label(brand_box, text="Cadence", bg=bg,
                         fg=self.palette["text"], font=(visual.FONT, 16, "bold"))
        brand.pack(side="left")
        brand._keep_bg = True
        self._brand_label = brand
        brand.bind("<ButtonPress-1>", self._start_move)
        brand.bind("<B1-Motion>", self._do_move)

    def _apply_logo(self) -> None:
        """标题栏左侧的小 LOGO。"""
        try:
            from . import logo as logo_mod

            image = logo_mod.window_logo(28)
            if image is None:
                return
            self._logo_photo = ImageTk.PhotoImage(image)
            self.logo_label.configure(image=self._logo_photo)
        except Exception:
            pass

    def _pill(self, parent, text: str, command, bg: str):
        label = tk.Label(parent, text=text, bg=bg, fg=self.palette["text"],
                         font=(visual.FONT, 9), padx=14, pady=6, cursor="hand2")
        label._keep_bg = True
        label._command = command
        if not hasattr(self, "_glass_pills"):
            self._glass_pills = []
        self._glass_pills.append(label)
        hover = self._blend_color(bg, (0, 0, 0, 14))
        label.bind("<Enter>", lambda _e: label.configure(bg=hover))
        label.bind("<Leave>", lambda _e: label.configure(bg=bg))
        label.bind("<Button-1>", lambda _e: command())
        return label

    def _round_button(self, parent, text: str, command, bg: str):
        label = tk.Label(parent, text=text, bg=bg, fg=self.palette["text2"],
                         font=(visual.FONT, 10), padx=10, pady=4, cursor="hand2")
        label._keep_bg = True
        label._command = command
        if not hasattr(self, "_glass_pills"):
            self._glass_pills = []
        self._glass_pills.append(label)
        hover = self._blend_color(bg, (0, 0, 0, 18))
        label.bind("<Enter>", lambda _e: label.configure(bg=hover))
        label.bind("<Leave>", lambda _e: label.configure(bg=bg))
        label.bind("<Button-1>", lambda _e: command())
        return label

    def _minimize(self) -> None:
        try:
            from . import win_frame

            if win_frame.minimize(self.root):
                return
        except Exception:
            pass
        self.root.iconify()

    def _start_move(self, event) -> None:
        """标题栏按下：记录偏移，由定时器跟着鼠标移动窗口。

        落在窗口边缘时改成缩放。
        """
        edge = self._edge_of(event)
        if edge:
            self._start_resize(edge, event)
            return
        self._drag = (event.x_root - self.root.winfo_x(),
                      event.y_root - self.root.winfo_y())
        self._drag_pos = None
        # 记住拖动开始时的尺寸：拖动过程中把尺寸钉死。
        # 不钉的话 Tk 会在每次移动后按它记着的边框高度把窗口改高 39px，
        # 一改高就整窗重排重绘——这就是肉眼看到的"内容拖在后面"。
        self._drag_size = (self.root.winfo_width(), self.root.winfo_height())
        try:
            from . import win_frame

            win_frame.pin_size(self.root)
        except Exception:
            pass
        self._drag_active = True
        self.root.after(16, self._drag_tick)

    def _drag_tick(self) -> None:
        if not getattr(self, "_drag_active", False):
            return
        try:
            from . import win_frame

            if not win_frame.left_button_down():
                self._end_move()
                return
            x, y = win_frame.cursor_pos()
            offset = getattr(self, "_drag", None)
            if offset:
                target = (x - offset[0], y - offset[1])
                # 位置没变就别再动窗口：重复 SetWindowPos 会让界面反复重绘
                if target != getattr(self, "_drag_pos", None):
                    self._drag_pos = target
                    # 只移动、不改尺寸。走 Tk 的 geometry 会触发整窗重排，
                    # 每帧重排一次就是肉眼看到的拖影。
                    win_frame.move_window(self.root, target[0], target[1])
        except Exception:
            self._end_move()
            return
        try:
            self.root.after(16, self._drag_tick)
        except tk.TclError:
            self._drag_active = False

    def _do_move(self, _event=None) -> None:
        """移动完全交给定时器，这里什么都不做（保持接口兼容）。"""

    # ------------------------------------------------------------ 贴边缩放

    RESIZE_EDGE = 7

    def _edge_of(self, event) -> str:
        """判断按下的位置是否落在窗口边缘，返回 n/s/e/w 的组合。"""
        try:
            x = event.x_root - self.root.winfo_rootx()
            y = event.y_root - self.root.winfo_rooty()
            w, h = self.root.winfo_width(), self.root.winfo_height()
        except tk.TclError:
            return ""
        b = self.RESIZE_EDGE
        left, right = x < b, x >= w - b
        top, bottom = y < b, y >= h - b
        edge = ""
        if top:
            edge += "n"
        elif bottom:
            edge += "s"
        if left:
            edge += "w"
        elif right:
            edge += "e"
        return edge

    def _start_resize(self, edge: str, event) -> None:
        self._resize_info = {
            "edge": edge,
            "x0": event.x_root, "y0": event.y_root,
            "w0": self.root.winfo_width(), "h0": self.root.winfo_height(),
            "px": self.root.winfo_x(), "py": self.root.winfo_y(),
            "pos": None,
        }
        self._resize_active = True
        self.root.after(16, self._resize_tick)

    def _resize_tick(self) -> None:
        if not getattr(self, "_resize_active", False):
            return
        try:
            from . import win_frame

            if not win_frame.left_button_down():
                self._end_resize()
                return
            info = getattr(self, "_resize_info", None)
            if not info:
                self._resize_active = False
                return
            cx, cy = win_frame.cursor_pos()
            dx = cx - info["x0"]
            dy = cy - info["y0"]
            edge = info["edge"]
            min_w, min_h = 960, 620
            w = max(min_w, info["w0"] + dx) if "e" in edge else info["w0"]
            h = max(min_h, info["h0"] + dy) if "s" in edge else info["h0"]
            x, y = info["px"], info["py"]
            if "w" in edge:
                w = max(min_w, info["w0"] - dx)
                x = info["px"] + (info["w0"] - w)
            if "n" in edge:
                h = max(min_h, info["h0"] - dy)
                y = info["py"] + (info["h0"] - h)
            target = (x, y, w, h)
            if target != info.get("pos"):
                info["pos"] = target
                win_frame.resize_window(self.root, x, y, w, h)
        except Exception:
            self._end_resize()
            return
        try:
            self.root.after(16, self._resize_tick)
        except tk.TclError:
            self._resize_active = False

    def _end_resize(self) -> None:
        self._resize_active = False
        self._resize_info = None
        try:
            self._apply_size()
        except Exception:
            pass

    def _on_root_motion(self, event) -> None:
        """鼠标靠近边缘时给出缩放光标。"""
        if getattr(self, "_drag_active", False) or getattr(self, "_resize_active", False):
            return
        edge = self._edge_of(event)
        cursor = {"n": "size_ns", "s": "size_ns", "e": "size_we", "w": "size_we",
                  "ne": "size_ne_sw", "sw": "size_ne_sw",
                  "nw": "size_nw_se", "se": "size_nw_se"}.get(edge, "")
        try:
            self.root.configure(cursor=cursor)
        except tk.TclError:
            pass

    def _end_move(self, _event=None) -> None:
        self._drag_active = False
        self._drag = None
        try:
            from . import win_frame

            win_frame.unpin_size(self.root)
            win_frame.redraw_now(self.root)
        except Exception:
            pass

    # ------------------------------------------------------------ 尺寸变化

    def _on_resize(self, event) -> None:
        if event.widget is not self.root:
            return
        width, height = event.width, event.height
        if width <= 1 or height <= 1:
            return
        if (width, height) == (self.width, self.height):
            return
        self.width, self.height = width, height
        # 先把位置排好（便宜），背景重画延后合并（贵）
        self._relayout_now()
        if getattr(self, "_resize_job", None):
            try:
                self.root.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self.root.after(90, self._apply_size)

    def _apply_size(self) -> None:
        """窗口尺寸变了之后，重排面板与背景。"""
        self._resize_job = None
        try:
            self.canvas.configure(width=self.width, height=self.height)
            self.canvas.place(x=0, y=0)
            self._render_background()
            self._place_titlebar()
            self._place_panels()
            self._place_statusbar()
            self._sync_colors()
        except tk.TclError:
            pass

    def _relayout_now(self) -> None:
        """只重排位置，不重画背景图（拖拽缩放时用，便宜得多）。"""
        try:
            self.canvas.configure(width=self.width, height=self.height)
            self.canvas.place(x=0, y=0)
            self._place_titlebar()
            self._place_panels()
            self._place_statusbar()
        except tk.TclError:
            pass

    def _place_titlebar(self) -> None:
        box = self._panel_boxes()["title"]
        self.title_bar.place(x=box[0], y=box[1],
                             width=box[2] - box[0], height=box[3] - box[1])

    def _place_statusbar(self) -> None:
        box = self._panel_boxes()["status"]
        self.status_label.place(
            x=box[0] + 12, y=box[1] + 4,
            width=max(80, box[2] - box[0] - 24),
            height=max(16, box[3] - box[1] - 8),
        )

    def _place_panels(self) -> None:
        boxes = self._panel_boxes()
        left, right = boxes["left"], boxes["right"]
        self.left.place(x=left[0] + 8, y=left[1] + 10,
                        width=left[2] - left[0] - 16, height=left[3] - left[1] - 20)
        self.right.place(x=right[0] + 22, y=right[1] + 18,
                         width=right[2] - right[0] - 44, height=right[3] - right[1] - 34)

    # ------------------------------------------------------------ 左栏

    def _build_left(self) -> None:
        bg = self._panel_color("left")
        box = self._panel_boxes()["left"]
        self.left = tk.Frame(self.root, bg=bg)
        self.left.place(x=box[0] + 8, y=box[1] + 10,
                        width=box[2] - box[0] - 16, height=box[3] - box[1] - 20)

        header = tk.Frame(self.left, bg=bg)
        header.pack(fill="x")
        tk.Label(header, text="音乐库", bg=bg, fg=self.palette["text"],
                 font=(visual.FONT, 11, "bold")).pack(side="left")
        self.count_label = tk.Label(header, text="0 首", bg=bg,
                                    fg=self.palette["text3"], font=(visual.FONT, 9))
        self.count_label.pack(side="right")

        self.search_var = tk.StringVar()
        entry_bg = self._blend_color(bg, (0, 0, 0, 18))
        self.search = tk.Entry(
            self.left, textvariable=self.search_var, bd=0, bg=entry_bg,
            fg=self.palette["text"], insertbackground=self.palette["text"],
            font=(visual.FONT, 9),
        )
        self.search.pack(fill="x", pady=(8, 8), ipady=6)
        self.search.bind("<KeyRelease>", lambda _e: self._draw_rows())
        self._placeholder()

        filter_row = tk.Frame(self.left, bg=bg)
        filter_row.pack(fill="x", pady=(0, 6))
        self._filter_buttons = {}
        for key, label in (("all", "全部"), ("no_cover", "缺封面"),
                           ("no_lyrics", "缺歌词"), ("empty", "空标签")):
            chip = self._rounded_button(filter_row, label,
                                        lambda k=key: self.set_filter(k),
                                        height=24, padx=10, bg=bg)
            chip.pack(side="left", padx=(0, 5))
            chip._filter_key = key
            self._filter_buttons[key] = chip
        self._filter = "all"
        self._paint_filters()

        self.list_canvas = tk.Canvas(self.left, bg=bg, highlightthickness=0, bd=0)
        self.list_canvas.pack(fill="both", expand=True)
        self.list_canvas.bind("<MouseWheel>", self._on_wheel)
        self.list_canvas.bind("<Button-1>", self._on_list_click)
        self.list_canvas.bind("<Motion>", self._on_list_motion)
        self.list_canvas.bind("<Leave>", lambda _e: self._set_hover(-1))

    def _placeholder(self) -> None:
        if not self.search.get():
            self.search.insert(0, "搜索文件名")
            self.search.configure(fg=self.palette["text3"])
            self._is_placeholder = True
        else:
            self._is_placeholder = False
        self.search.bind("<FocusIn>", self._clear_placeholder)
        self.search.bind("<FocusOut>", self._restore_placeholder)

    def _clear_placeholder(self, _event=None) -> None:
        if getattr(self, "_is_placeholder", False):
            self.search.delete(0, "end")
            self.search.configure(fg=self.palette["text"])
            self._is_placeholder = False

    def _restore_placeholder(self, _event=None) -> None:
        if not self.search.get():
            self._placeholder()

    def _query_text(self) -> str:
        if getattr(self, "_is_placeholder", False):
            return ""
        return self.search.get().strip().lower()

    # ------------------------------------------------------------ 右栏

    def _build_right(self) -> None:
        bg = self._panel_color("right")
        self.right_bg = bg
        box = self._panel_boxes()["right"]
        self.right = tk.Frame(self.root, bg=bg)
        self.right.place(x=box[0] + 22, y=box[1] + 18,
                         width=box[2] - box[0] - 44, height=box[3] - box[1] - 34)

        top = tk.Frame(self.right, bg=bg)
        top.pack(fill="x")
        self.cover_label = tk.Label(top, bg=bg, bd=0)
        self.cover_label.pack(side="left")

        info = tk.Frame(top, bg=bg)
        info.pack(side="left", fill="both", expand=True, padx=(18, 0))
        self.song_title = tk.Label(info, text="选择一首歌", bg=bg,
                                   fg=self.palette["text"],
                                   font=(visual.FONT, 15, "bold"),
                                   anchor="w", justify="left", wraplength=520)
        self.song_title.pack(fill="x", pady=(6, 2))
        self.song_meta = tk.Label(info, text="", bg=bg, fg=self.palette["text2"],
                                  font=(visual.FONT, 9), anchor="w")
        self.song_meta.pack(fill="x")
        self.song_meta.configure(text="从左边列表选一首；没有歌就先点头部的「文件夹」")

        self.fields: dict[str, tk.Entry] = {}
        grid = tk.Frame(self.right, bg=bg)
        grid.pack(fill="x", pady=(16, 4))
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        def add_field(key: str, label: str, row: int, column: int = 0,
                      span: int = 2, padx=(0, 0)) -> None:
            cell = tk.Frame(grid, bg=bg)
            cell.grid(row=row, column=column, columnspan=span, sticky="ew",
                      pady=4, padx=padx)
            tk.Label(cell, text=label, bg=bg, fg=self.palette["text3"],
                     font=(visual.FONT, 8), anchor="w").pack(fill="x", pady=(0, 2))
            self.fields[key] = self._make_rounded_entry(cell)

        add_field("title", "歌名", 0)
        add_field("artist", "歌手", 1, column=0, span=1, padx=(0, 7))
        add_field("album", "专辑", 1, column=1, span=1, padx=(7, 0))
        add_field("year", "年份", 2, column=0, span=1, padx=(0, 7))
        add_field("track", "音轨", 2, column=1, span=1, padx=(7, 0))

        self._divider(self.right)

        # 先固定底部两块（按钮、歌词），中间留给候选列表，避免被 expand 挤没
        actions = tk.Frame(self.right, bg=bg)
        actions.pack(fill="x", pady=(8, 0), side="bottom")
        self.save_btn = self._rounded_button(actions, "保存", self.save_current,
                                             accent=True, padx=24)
        self.save_btn.pack(side="left")
        self.next_btn = self._rounded_button(actions, "保存并下一首", self.save_and_next)
        self.next_btn.pack(side="left", padx=(10, 0))
        self.skip_btn = self._rounded_button(actions, "跳过", self.skip_current)
        self.skip_btn.pack(side="left", padx=(8, 0))
        self.rematch_btn = self._rounded_button(actions, "重新搜索",
                                                lambda: self.start_match(force=True))
        self.rematch_btn.pack(side="left", padx=(8, 0))

        lyric_section = tk.Frame(self.right, bg=bg)
        lyric_section.pack(fill="x", side="bottom")

        lyric_head = tk.Frame(lyric_section, bg=bg)
        lyric_head.pack(fill="x", pady=(6, 2))
        tk.Label(lyric_head, text="歌词", bg=bg, fg=self.palette["text2"],
                 font=(visual.FONT, 9, "bold")).pack(side="left")
        self.lyric_hint = tk.Label(lyric_head, text="", bg=bg,
                                   fg=self.palette["text3"], font=(visual.FONT, 8))
        self.lyric_hint.pack(side="right")
        self.lyric_file_btn = self._rounded_button(
            lyric_head, "存为 .lrc", self.save_lyrics_file, height=26, padx=12)
        self.lyric_file_btn.pack(side="right", padx=(6, 8))
        self.lyric_fetch_btn = self._rounded_button(
            lyric_head, "抓取歌词", self.fetch_lyrics, height=26, padx=12)
        self.lyric_fetch_btn.pack(side="right")

        lyric_box = tk.Frame(lyric_section, bg=bg, height=64)
        lyric_box.pack(fill="x", pady=(2, 2))
        lyric_box.pack_propagate(False)
        self.lyrics_text = tk.Text(lyric_box, bd=0, highlightthickness=0,
                                   wrap="word", font=(visual.FONT, 9))
        self.lyrics_text.pack(fill="both", expand=True)
        self.lyrics_text.bind("<MouseWheel>", lambda e: (
            self.lyrics_text.yview_scroll(-1 * (e.delta // 120), "units"), "break")[1])
        self._lyric_box = lyric_box
        # 没歌词时收起，把高度让给候选列表
        lyric_box.pack_forget()
        lyric_box._shown = False
        lyric_section._box = lyric_box

        self._divider(self.right, side="bottom")

        cand_head = tk.Frame(self.right, bg=bg)
        cand_head.pack(fill="x", pady=(10, 4))
        tk.Label(cand_head, text="匹配结果", bg=bg, fg=self.palette["text2"],
                 font=(visual.FONT, 9, "bold")).pack(side="left")

        self.cand_canvas = tk.Canvas(self.right, bg=bg, highlightthickness=0, bd=0,
                                     height=168)
        self.cand_canvas.pack(fill="both", expand=True)
        self.cand_canvas.bind("<Button-1>", self._on_cand_click)
        self.cand_canvas.bind("<Motion>", self._on_cand_motion)
        self.cand_canvas.bind("<Leave>", lambda _e: self._set_cand_hover(-1))
        self.cand_canvas.bind("<MouseWheel>", self._on_cand_wheel)

    def _divider(self, parent, side: str = "top") -> None:
        """一条极淡的分隔线，替代生硬的边框。"""
        line = tk.Frame(parent, bg=self._blend_color(self.right_bg, (0, 0, 0, 16)
                                                     if self.mode != "dark"
                                                     else (255, 255, 255, 22)),
                        height=1)
        line.pack(fill="x", pady=(8, 2), side=side)

    def _make_rounded_entry(self, parent) -> tk.Entry:
        """圆角输入框：底下垫一张圆角图，输入框本身与底色同色，看不出方角。"""
        fill = self._blend_color(self.right_bg, (255, 255, 255, 150)
                                 if self.mode != "dark" else (255, 255, 255, 24))
        shell = tk.Frame(parent, bg=self.right_bg, height=30)
        shell.pack(fill="x")
        shell.pack_propagate(False)

        back = tk.Label(shell, bd=0, bg=self.right_bg)
        back.place(x=0, y=0, relwidth=1.0, relheight=1.0)

        entry = tk.Entry(shell, bd=0, relief="flat", bg=fill,
                         fg=self.palette["text"],
                         insertbackground=self.palette["text"],
                         font=(visual.FONT, 10))
        entry.place(x=11, y=5, relwidth=1.0, width=-22, relheight=1.0, height=-10)
        entry._keep_bg = True  # 别被全局配色同步刷掉

        def redraw(_event=None) -> None:
            width = max(20, shell.winfo_width())
            height = max(12, shell.winfo_height())
            key = f"fieldbg:{width}x{height}:{fill}"
            photo = self._photos.get(key)
            if photo is None:
                image = visual.rounded_image(
                    (width, height), 9, visual.hex_to_rgba(fill)
                )
                photo = ImageTk.PhotoImage(image)
                self._photos[key] = photo
            back.configure(image=photo)

        shell.bind("<Configure>", redraw)
        shell.bind("<Map>", redraw)
        entry._shell = shell
        entry._redraw = redraw
        return entry

    def _rounded_button(self, parent, text: str, command,
                        accent: bool = False, height: int = 32, padx: int = 20,
                        bg: str | None = None):
        """圆角按钮：用图片做底，hover 换一张图。"""
        import tkinter.font as tkfont

        panel_bg = bg or self.right_bg
        font = (visual.FONT, 9, "bold" if accent else "normal")
        measure = tkfont.Font(font=font)
        width = measure.measure(text) + padx * 2
        if accent:
            base = visual.to_hex(self.palette["accent"])
        else:
            base = self._blend_color(panel_bg, (255, 255, 255, 92)
                                     if self.mode != "dark" else (255, 255, 255, 20))
        hover = self._blend_color(base, (255, 255, 255, 46) if accent
                                  else ((0, 0, 0, 18) if self.mode != "dark"
                                        else (255, 255, 255, 26)))

        def render(color: str):
            key = f"btn:{text}:{width}:{height}:{color}:{panel_bg}"
            if key not in self._photos:
                image = visual.rounded_image(
                    (width, height), max(6, height // 2),
                    visual.hex_to_rgba(color),
                )
                self._photos[key] = ImageTk.PhotoImage(image)
            return self._photos[key]

        label = tk.Label(
            parent, text=text, image=render(base), compound="center",
            bg=panel_bg, fg="#FFFFFF" if accent else self.palette["text"],
            font=font, bd=0, padx=0, pady=0, cursor="hand2",
        )
        label._keep_bg = True
        label._command = command
        label._render = render
        label._base = base
        label._hover = hover
        label.bind("<Enter>", lambda _e: label.configure(image=render(hover)))
        label.bind("<Leave>", lambda _e: label.configure(image=render(base)))
        label.bind("<Button-1>", lambda _e: command())
        return label

    def _primary_button(self, parent, text: str, command):
        bg = self.palette["accent"]
        label = tk.Label(parent, text=text, bg=bg, fg="#FFFFFF",
                         font=(visual.FONT, 10, "bold"), padx=26, pady=7,
                         cursor="hand2")
        label._keep_bg = True
        hover = self._blend_color(bg, (255, 255, 255, 30))
        label.bind("<Enter>", lambda _e: label.configure(bg=hover))
        label.bind("<Leave>", lambda _e: label.configure(bg=bg))
        label.bind("<Button-1>", lambda _e: command())
        return label

    # ------------------------------------------------------------ 列表绘制

    def refresh_list(self) -> None:
        keyword = self._query_text()
        self.tracks = db.list_tracks(
            self.ctx.conn,
            statuses=None if self._filter == "all" else [self._filter],
            keyword=keyword,
        )
        total = db.count_by_status(self.ctx.conn).get("all", 0)
        self.count_label.configure(text=f"{len(self.tracks)} / {total} 首")
        self._row_hover = -1
        self._draw_rows()
        if self.tracks and self.current is None:
            self._select_track(0)

    def set_filter(self, key: str) -> None:
        self._filter = key
        self.current = None
        self.selected_index = -1
        self._paint_filters()
        self.refresh_list()

    def _paint_filters(self) -> None:
        """选中的筛选标签有底色，一眼看得出当前在哪个视图。"""
        panel = getattr(self, "_left_bg", None) or self._panel_color("left")
        for key, chip in getattr(self, "_filter_buttons", {}).items():
            active = key == getattr(self, "_filter", "all")
            render = getattr(chip, "_render", None)
            if render is None:
                continue
            if active:
                color = self._blend_color(panel, (10, 132, 255, 46))
                chip.configure(fg=self.palette["accent"])
            else:
                color = self._blend_color(panel, (0, 0, 0, 10))
                chip.configure(fg=self.palette["text2"])
            try:
                chip.configure(image=render(color))
                chip._base = color
                chip._hover = self._blend_color(color, (0, 0, 0, 16))
            except Exception:
                pass

    def _visible_tracks(self) -> list[tuple[int, Track]]:
        needle = self._query_text()
        items = []
        for index, track in enumerate(self.tracks):
            if needle and needle not in track.name.lower():
                continue
            items.append((index, track))
        return items

    def _row_bg(self, width: int, height: int, color: str):
        """行高亮用的圆角底图（同色同尺寸只渲染一次）。"""
        key = f"rowbg:{width}x{height}:{color}"
        if key in self._photos:
            return self._photos[key]
        image = visual.rounded_image((max(1, width), max(1, height)), 12,
                                     visual.hex_to_rgba(color))
        photo = ImageTk.PhotoImage(image)
        self._photos[key] = photo
        return photo

    def _draw_rows(self) -> None:
        canvas = self.list_canvas
        canvas.delete("all")
        items = self._visible_tracks()
        row_h = 60
        width = canvas.winfo_width() or (LEFT_W - 30)
        top = -self._scroll_offset
        panel_color = self._panel_color("left")

        self._row_boxes = []
        for slot, (index, track) in enumerate(items):
            y = top + slot * row_h
            if y + row_h < 0 or y > canvas.winfo_height():
                continue
            selected = index == self.selected_index
            hover = index == getattr(self, "_row_hover", -1)

            if selected:
                fill = self._blend_color(panel_color, self.palette["select"])
            elif hover:
                fill = self._blend_color(panel_color, self.palette["hover"])
            else:
                fill = ""
            if fill:
                image = self._row_bg(width - 6, row_h - 6, fill)
                canvas.create_image(3, y + 3, anchor="nw", image=image)

            if selected:
                # 选中：左侧一道圆角高亮条（iOS 列表的常见处理）
                bar = self._row_bg(3, 26, self.palette["accent"])
                canvas.create_image(9, y + row_h / 2, anchor="w", image=bar)

            thumb = self._thumb(track)
            if thumb is not None:
                canvas.create_image(12, y + row_h / 2, anchor="w", image=thumb)

            status_text = {
                "no_cover": "缺封面",
                "no_lyrics": "缺歌词",
                "empty": "标签空",
                "conflict": "有冲突",
            }.get(track.status, "")
            text_width = max(60, width - (120 if status_text else 76))

            title_font = (visual.FONT, 10)
            sub_font = (visual.FONT, 8)
            canvas.create_text(62, y + 20, anchor="w",
                               text=_ellipsis(track.name, title_font, text_width),
                               fill=self.palette["text"], font=title_font)
            canvas.create_text(62, y + 40, anchor="w",
                               text=_ellipsis(self._subtitle(track), sub_font, text_width),
                               fill=self.palette["text3"], font=sub_font)
            if status_text:
                canvas.create_text(width - 10, y + row_h / 2, anchor="e",
                                   text=status_text, fill=self.palette["text3"],
                                   font=sub_font)
            self._row_boxes.append((index, y, row_h))

    def _thumb(self, track: Track):
        key = f"thumb:{track.path}"
        if key in self._photos:
            return self._photos[key]
        data = self._cover_bytes(track)
        image = None
        if data:
            image = visual.cover_from_bytes(data, 40, radius=9)
        if image is None:
            return None
        photo = ImageTk.PhotoImage(image)
        self._photos[key] = photo
        return photo

    def _cover_bytes(self, track: Track) -> bytes | None:
        key = track.path
        cache = getattr(self, "_cover_cache", None)
        if cache is None:
            cache = self._cover_cache = {}
        if key in cache:
            return cache[key]

        from ..core import covercache

        data: bytes | None = None
        # 优先：已经匹配到的候选封面
        candidates = db.load_candidates(self.ctx.conn, track.id or 0)
        if candidates:
            data = covercache.get(self.ctx.data_dir, candidates[0])
        # 其次：文件本身内嵌的封面
        if not data:
            from ..core import probe

            data = probe.extract_cover(track.path)
        cache[key] = data
        return data

    def _backdrop_cover(self) -> Image.Image | None:
        """只有拿到真实封面才拿它做背景，否则用色块（避免占位图染色）。"""
        if self.current is None:
            return None
        data = self._cover_bytes(self.current)
        if not data:
            return None
        return visual.cover_from_bytes(data, 512, radius=0)

    def _cover_image(self, size: int) -> Image.Image | None:
        if self.current is None:
            return None
        data = self._cover_bytes(self.current)
        image = visual.cover_from_bytes(data, size, radius=14) if data else None
        if image is None:
            self._using_placeholder = True
            image = visual.placeholder_cover(size, self.current.name)
        else:
            self._using_placeholder = False
        return image

    def _subtitle(self, track: Track) -> str:
        parts = [track.fmt or "?", track.quality_label()]
        if track.duration:
            total = int(track.duration)
            parts.append(f"{total // 60}:{total % 60:02d}")
        return " · ".join(part for part in parts if part)

    def _on_list_click(self, event) -> None:
        slot = int((event.y + self._scroll_offset) // 60)
        items = self._visible_tracks()
        if 0 <= slot < len(items):
            self._select_track(items[slot][0])

    def _on_list_motion(self, event) -> None:
        slot = int((event.y + self._scroll_offset) // 60)
        items = self._visible_tracks()
        self._set_hover(items[slot][0] if 0 <= slot < len(items) else -1)

    def _set_hover(self, index: int) -> None:
        if getattr(self, "_row_hover", -1) != index:
            self._row_hover = index
            self._draw_rows()

    def _on_wheel(self, event) -> None:
        items = self._visible_tracks()
        max_offset = max(0, len(items) * 60 - self.list_canvas.winfo_height())
        self._scroll_offset = max(0, min(max_offset,
                                         self._scroll_offset - event.delta // 3))
        self._draw_rows()

    # ------------------------------------------------------------ 选中与匹配

    def _select_track(self, index: int) -> None:
        if not (0 <= index < len(self.tracks)):
            return
        self.selected_index = index
        track = self.tracks[index]
        self.current = track
        self.ctx.set_current(track)
        self._draw_rows()
        self._fill_detail(track)
        self.start_match()

    def _fill_detail(self, track: Track) -> None:
        # 连点好几首歌时跳过动画和配色重采样，否则每次都多花一两百毫秒
        now = time.monotonic()
        rapid = (now - getattr(self, "_last_select_ts", 0.0)) < 0.25
        self._last_select_ts = now
        parsed = parse_name(track.name)
        self._pending_cover = None
        self.lyrics_text.delete("1.0", "end")
        self.lyric_hint.configure(text="")
        self._show_lyrics_box(False)
        self.song_title.configure(text=track.name)
        self.song_meta.configure(
            text=" · ".join(part for part in (
                track.fmt, track.quality_label(),
                f"{int(track.duration) // 60}:{int(track.duration) % 60:02d}"
                if track.duration else "",
            ) if part)
        )
        values = {
            "title": track.tag_title or parsed.title,
            "artist": track.tag_artist or parsed.artist,
            "album": track.tag_album,
            "year": track.tag_year,
            "track": track.tag_track or parsed.track,
        }
        for key, value in values.items():
            entry = self.fields[key]
            entry.delete(0, "end")
            if value:
                entry.insert(0, str(value))

        self._render_cover(animate=not rapid)
        if not rapid:
            self._slide_in()

    def _render_cover(self, animate: bool = False) -> None:
        target = self._cover_image(132)
        if target is None:
            return
        photo = ImageTk.PhotoImage(target)
        self._photos["cover"] = photo
        self.cover_label.configure(image=photo)
        # 背景（毛玻璃）重算一次要 160ms 左右，不能每换一首歌就重算；
        # 这里合并成一次，等选择停下来再做。
        self._schedule_background()

    def _schedule_background(self, delay: int = 180) -> None:
        job = getattr(self, "_bg_job", None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        try:
            self._bg_job = self.root.after(delay, self._run_background_job)
        except tk.TclError:
            self._bg_job = None

    def _run_background_job(self) -> None:
        self._bg_job = None
        try:
            self._render_background()
        except tk.TclError:
            pass

    def _slide_in(self) -> None:
        """右侧内容轻微上滑 + 淡入效果（用位移模拟）。"""
        base_y = self._panel_boxes()["right"][1] + 18
        box = self._panel_boxes()["right"]
        steps = 6

        def step(i: int) -> None:
            offset = int(10 * (1 - i / steps))
            self.right.place_configure(y=base_y + offset)
            if i < steps:
                self.root.after(16, lambda: step(i + 1))

        step(0)

    def start_match(self, force: bool = False) -> None:
        track = self.current
        if track is None:
            return
        if not self.ctx.providers:
            self.status_label.configure(text="没有可用的数据源")
            return
        # 网络库在这台机器上加载失败时，别让界面一直"搜索中…"
        try:
            from ..providers import http as http_mod

            if not http_mod.network_available():
                self._set_status(
                    "网络模块不可用：可以整理本地文件，但搜索/封面取不到"
                    f"（{http_mod.network_error()[:60]}）", sticky=True)
                return
        except Exception:
            pass
        # 一次只跑一个搜索：连点很多首歌时，旧的那次立刻取消，
        # 只记一个"待办"，等它收手后查最后选中的那首。
        # 否则每点一次就多八个并发请求，越点越慢，最后像卡死。
        if getattr(self, "_match_running", False):
            self._match_pending = True
            # 立刻作废正在跑的那次：否则它被取消后仍会把"半截结果"
            # 写进界面和数据库（只回来一两个源就算完成）
            self._match_token += 1
            if self._match_cancel is not None:
                self._match_cancel.set()
            return

        self._match_running = True
        cancel = threading.Event()
        self._match_cancel = cancel
        self._match_token += 1
        token = self._match_token

        # 最近查过的歌直接用结果：来回对比候选时不用每次都等网络
        cache = getattr(self, "_match_cache", None)
        if cache is None:
            cache = self._match_cache = {}
        cached = cache.get(track.path)
        if cached and not force and time.monotonic() - cached[0] < 120:
            self._match_running = False
            self._match_cancel = None
            self.candidates = list(cached[1])
            self._draw_candidates()
            self._set_status(self._source_summary())
            if self.candidates:
                self._apply_thumbnail(self.candidates[0])
                self._load_candidate_thumbs()
            return

        self.status_label.configure(text="搜索中…")
        self._source_results: dict[str, list[Candidate]] = {}
        self._pending_sources = len(self.ctx.providers)
        providers = self.ctx.providers
        folder = db.folder_tracks(self.ctx.conn, os.path.dirname(track.path))
        evidence_ctx = album_context(track, folder)

        def work() -> tuple[int, list[Candidate]]:
            from ..core import evidence
            from ..providers import search_all
            from ..providers.base import SearchQuery

            def on_result(name: str, items, error: str) -> None:
                def apply() -> None:
                    if token != self._match_token:
                        return
                    self._source_results[name] = list(items)
                    self._pending_sources = max(0, self._pending_sources - 1)
                    merged = [c for lst in self._source_results.values() for c in lst]
                    evidence.rank(track, merged, evidence_ctx)
                    self.candidates = merged
                    self._draw_candidates()
                    suffix = f" · 还在查 {self._pending_sources} 个源" if self._pending_sources else ""
                    self._set_status(f"{len(merged)} 条候选{suffix}")

                self.worker.post(apply)

            query = matcher.build_query(track)
            results, _errors = search_all(query, providers, on_result=on_result,
                                          cancel=cancel)
            found = [c for lst in results.values() for c in lst]
            evidence.rank(track, found, evidence_ctx)
            return token, found

        def resume_pending() -> None:
            self._match_running = False
            self._match_cancel = None
            if self._match_pending:
                self._match_pending = False
                try:
                    self.root.after(10, self.start_match)
                except tk.TclError:
                    pass

        def finish(payload) -> None:
            got_token, found = payload
            resume_pending()
            if got_token != self._match_token:
                return
            self.candidates = found
            if found:
                self._match_cache[track.path] = (time.monotonic(), list(found))
            if track.id:
                db.replace_candidates(self.ctx.conn, track.id, found)
                self.ctx.conn.commit()
            self.selected_cand = -1
            self._draw_candidates()
            self._set_status(self._source_summary())
            if found:
                self._apply_thumbnail(found[0])
                self._load_candidate_thumbs()

        def failed(_exc) -> None:
            resume_pending()
            if self.current is track:
                self.status_label.configure(text="搜索失败，可点「重新搜索」重试")

        self._run_async(work, finish, failed)

    def _apply_thumbnail(self, candidate: Candidate, fetch_if_missing: bool = True) -> None:
        from ..core import covercache

        data = covercache.get(self.ctx.data_dir, candidate)
        if not data:
            # 缓存里没有就后台抓一次，抓到再回填；否则大封面一直是占位图
            if fetch_if_missing:
                self._fetch_cover_async(candidate)
            return
        image = visual.cover_from_bytes(data, 132, 14)
        if image is None:
            return
        photo = ImageTk.PhotoImage(image)
        self._photos["cover"] = photo
        self.cover_label.configure(image=photo)
        self._using_placeholder = False
        self._schedule_background()

    def _fetch_cover_async(self, candidate: Candidate) -> None:
        key = f"{candidate.source}|{candidate.title}|{candidate.artist}"
        pending = getattr(self, "_cover_fetching", None)
        if pending is None:
            pending = self._cover_fetching = set()
        if key in pending:
            return
        pending.add(key)
        providers = self.ctx.providers
        data_dir = self.ctx.data_dir

        def work():
            from ..core import covercache

            provider = next((p for p in providers if p.name == candidate.source), None)
            if provider is None:
                return None
            try:
                data = provider.fetch_cover(candidate)
            except Exception:
                return None
            if data and _looks_like_image(data):
                covercache.put(data_dir, candidate, data)
                return candidate
            return None

        def finish(saved) -> None:
            pending.discard(key)
            if saved is None:
                return
            current = self.candidates[:1]
            still_same = (
                self.current is not None
                and current
                and current[0].source == saved.source
                and current[0].title == saved.title
            )
            if still_same:
                self._apply_thumbnail(saved, fetch_if_missing=False)
            self._draw_candidates()

        self._run_async(work, finish)

    def _run_async(self, work, on_done, on_error=None) -> None:
        """后台线程跑任务，结果通过队列回到主线程。

        不能用 root.after 从子线程回调：Tk 不是线程安全的，
        那样写偶尔会静默丢结果（尤其是主循环没在跑的时候）。
        """
        self.worker.submit(work, on_done, on_error)

    # ------------------------------------------------------------ 候选绘制

    def _chip_bg(self, width: int, height: int, color: str):
        key = f"chip:{width}x{height}:{color}"
        photo = self._photos.get(key)
        if photo is None:
            image = visual.rounded_image((width, height), height // 2,
                                         visual.hex_to_rgba(color))
            photo = ImageTk.PhotoImage(image)
            self._photos[key] = photo
        return photo

    def _show_lyrics_box(self, visible: bool) -> None:
        """歌词区按需展开：没内容时收起，把高度还给候选列表。"""
        box = getattr(self, "_lyric_box", None)
        if box is None:
            return
        if visible and not getattr(box, "_shown", False):
            box.pack(fill="x", pady=(2, 2))
            box._shown = True
        elif not visible and getattr(box, "_shown", False):
            box.pack_forget()
            box._shown = False

    def _build_display(self, limit: int = 12) -> list[Candidate]:
        """按源轮流取前 N 条。

        只按分数排的话，返回条数多的源（比如网易云）会把前 12 位占满，
        看起来"只有这一个源"。这里让每个源都有机会露头，源内仍按分数排序。
        """
        buckets: dict[str, list[Candidate]] = {}
        order: list[str] = []
        for cand in self.candidates:
            if cand.source not in buckets:
                buckets[cand.source] = []
                order.append(cand.source)
            buckets[cand.source].append(cand)

        out: list[Candidate] = []
        while len(out) < limit:
            progressed = False
            for source in order:
                if buckets[source] and len(out) < limit:
                    out.append(buckets[source].pop(0))
                    progressed = True
            if not progressed:
                break
        return out

    def _source_summary(self) -> str:
        counts: dict[str, int] = {}
        for cand in self.candidates:
            counts[cand.source] = counts.get(cand.source, 0) + 1
        if not counts:
            return "0 条候选"
        parts = "、".join(f"{k}{v}" for k, v in counts.items())
        return f"{len(self.candidates)} 条候选 · {len(counts)} 个源（{parts}）"

    def _draw_candidates(self) -> None:
        canvas = self.cand_canvas
        canvas.delete("all")
        width = canvas.winfo_width() or 600
        row_h = 54
        self._display = self._build_display(12)
        total = len(self._display)
        canvas.configure(scrollregion=(0, 0, width, max(1, total * row_h)))
        for index, cand in enumerate(self._display):
            y = index * row_h
            selected = index == self.selected_cand
            hover = index == getattr(self, "_cand_hover", -1)
            if selected or hover:
                tint = (self.palette["select"] if selected else self.palette["hover"])
                fill = self._blend_color(self.right_bg, tint)
                image = self._row_bg(width - 4, row_h - 6, fill)
                canvas.create_image(2, y + 3, anchor="nw", image=image)
            if selected:
                bar = self._row_bg(3, 24, self.palette["accent"])
                canvas.create_image(9, y + row_h / 2, anchor="w", image=bar)

            hits = sum(1 for v in cand.evidence.values() if v >= 0.75)
            badge_color = (self.palette["ok"] if hits >= 5 else
                           self.palette["accent"] if hits >= 3 else
                           self.palette["text3"])
            thumb = self._cand_thumb(cand)
            text_x = 14
            if thumb is not None:
                canvas.create_image(12, y + row_h / 2, anchor="w", image=thumb)
                text_x = 58

            # 命中数做成胶囊徽章
            chip_bg = self._blend_color(
                self.right_bg, visual.hex_to_rgba(badge_color, 40)
            )
            canvas.create_image(text_x, y + row_h / 2 - 1, anchor="w",
                                image=self._chip_bg(42, 22, chip_bg))
            canvas.create_text(text_x + 21, y + row_h / 2 - 1,
                               text=f"{hits}/6", fill=badge_color,
                               font=(visual.FONT, 9, "bold"))
            text_x += 52

            cand_font = (visual.FONT, 10)
            sub_font = (visual.FONT, 8)
            canvas.create_text(text_x, y + 18, anchor="w",
                               text=_ellipsis(cand.title or "—", cand_font,
                                              max(80, width - text_x - 110)),
                               fill=self.palette["text"], font=cand_font)
            sub = " · ".join(part for part in (
                cand.artist or "", self._mmss(cand.duration)) if part)
            canvas.create_text(text_x, y + 37, anchor="w",
                               text=_ellipsis(sub, sub_font,
                                              max(80, width - text_x - 110)),
                               fill=self.palette["text2"], font=sub_font)
            canvas.create_text(width - 10, y + row_h / 2, anchor="e",
                               text=cand.source, fill=self.palette["text3"],
                               font=(visual.FONT, 8))

    def _on_cand_click(self, event) -> None:
        index = int(event.y // 54)
        display = getattr(self, "_display", [])
        if 0 <= index < len(display):
            self.selected_cand = index
            cand = display[index]
            self._set_field("title", cand.title)
            self._set_field("artist", cand.artist)
            self._set_field("album", cand.album)
            self._set_field("year", cand.year)
            self._set_field("track", cand.track)
            self._applied = cand
            cover = self._candidate_cover_bytes(cand)
            self._pending_cover = cover
            if cover is None:
                # 缓存里还没有就后台抓，抓到再补上，免得保存时没有封面
                self._fetch_applied_cover(cand)
            self._draw_candidates()
            self.status_label.configure(text=f"已套用 {cand.source}，确认后保存")

    def _fetch_applied_cover(self, candidate: Candidate) -> None:
        providers = self.ctx.providers
        data_dir = self.ctx.data_dir
        self._cover_waiting = True

        def work():
            from ..core import covercache

            provider = next((p for p in providers if p.name == candidate.source), None)
            if provider is None:
                return None
            try:
                data = provider.fetch_cover(candidate)
            except Exception:
                return None
            if data and _looks_like_image(data):
                covercache.put(data_dir, candidate, data)
                return data
            return None

        def finish(data) -> None:
            self._cover_waiting = False
            if not data:
                return
            applied = getattr(self, "_applied", None)
            if applied is not None and applied.source == candidate.source \
                    and applied.title == candidate.title \
                    and applied.artist == candidate.artist:
                self._pending_cover = data
                self.status_label.configure(
                    text=f"已套用 {candidate.source}（含封面），确认后保存")
            self._draw_candidates()

        self._run_async(work, finish)

    def _candidate_cover_bytes(self, candidate: Candidate) -> bytes | None:
        from ..core import covercache

        return covercache.get(self.ctx.data_dir, candidate)

    def _cand_thumb(self, candidate: Candidate):
        key = f"cthumb:{candidate.source}:{candidate.title}:{candidate.artist}"
        if key in self._photos:
            return self._photos[key]
        from ..core import covercache

        data = covercache.get(self.ctx.data_dir, candidate)
        if not data:
            return None
        image = visual.cover_from_bytes(data, 32, radius=8)
        if image is None:
            return None
        photo = ImageTk.PhotoImage(image)
        self._photos[key] = photo
        return photo

    def _load_candidate_thumbs(self) -> None:
        """后台补候选缩略图（按当前展示的列表抓，保证各源都覆盖到）。"""
        display = list(getattr(self, "_display", None) or self.candidates[:12])
        candidates = display[:12]
        if not candidates:
            return
        providers = self.ctx.providers
        data_dir = self.ctx.data_dir
        from ..core import evidence as evidence_mod

        def work():
            from ..core import covercache
            from concurrent.futures import ThreadPoolExecutor, as_completed

            def grab(cand):
                provider = next((p for p in providers if p.name == cand.source), None)
                if provider is None:
                    return None
                try:
                    data = provider.fetch_cover(cand)
                except Exception:
                    return None
                return data if _looks_like_image(data) else None

            def similar_title(first: Candidate, second: Candidate) -> bool:
                a = evidence_mod.normalize(first.title)
                b = evidence_mod.normalize(second.title)
                if not a or not b:
                    return False
                if a == b:
                    # 标题完全一致就别再纠结歌手写法（周杰伦 / 周杰倫）
                    return True
                if not (a in b or b in a):
                    return False
                art_a = evidence_mod.normalize(first.artist)
                art_b = evidence_mod.normalize(second.artist)
                if not art_a or not art_b:
                    return True
                return art_a == art_b or art_a in art_b or art_b in art_a

            def one(cand):
                if covercache.get(data_dir, cand):
                    return False
                data = grab(cand)
                if data:
                    covercache.put(data_dir, cand, data)
                    return True
                # 这个源没有专辑图（酷我常见）：借同名候选的封面，
                # 标题带 (伴奏)/(Live) 后缀的也算同名（歌手要一致）
                for other in display:
                    if other is cand:
                        continue
                    if not similar_title(cand, other):
                        continue
                    data = covercache.get(data_dir, other) or grab(other)
                    if data:
                        covercache.put(data_dir, cand, data)
                        return True
                return False

            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {pool.submit(one, cand): cand for cand in candidates}
                for future in as_completed(futures):
                    try:
                        changed_this = future.result()
                    except Exception:
                        continue
                    if changed_this:
                        # 谁的封面先回来就先补上，别等 12 个都抓完
                        cand = futures[future]
                        try:
                            self.worker.post(lambda c=cand: self._try_apply_cover(c))
                        except Exception:
                            pass

            # 第二轮：并行跑的时候，失败的源可能还没等到兄弟行的封面落盘，
            # 这里再从缓存里补一次（纯本地，不发请求）
            for cand in candidates:
                if covercache.get(data_dir, cand):
                    continue
                for other in display:
                    if other is cand or not similar_title(cand, other):
                        continue
                    data = covercache.get(data_dir, other)
                    if data:
                        covercache.put(data_dir, cand, data)
                        try:
                            self.worker.post(lambda c=cand: self._try_apply_cover(c))
                        except Exception:
                            pass
                        break
            return True

        def finish(changed: bool) -> None:
            if changed:
                self._draw_candidates()
            if getattr(self, "_using_placeholder", False):
                from ..core import covercache

                for cand in candidates:
                    if covercache.get(data_dir, cand):
                        self._apply_thumbnail(cand, fetch_if_missing=False)
                        break

        self._run_async(work, finish)

    def _try_apply_cover(self, candidate: Candidate) -> None:
        """封面一到就换上——只在当前还是占位图时替换，避免打断用户选择。"""
        if not getattr(self, "_using_placeholder", True):
            return
        self._apply_thumbnail(candidate, fetch_if_missing=False)
        self._draw_candidates()

    def _on_cand_motion(self, event) -> None:
        index = int(event.y // 54)
        total = min(12, len(self.candidates))
        value = index if 0 <= index < total else -1
        if value != getattr(self, "_cand_hover", -1):
            self._cand_hover = value
            self._draw_candidates()

    def _set_cand_hover(self, index: int) -> None:
        if index != getattr(self, "_cand_hover", -1):
            self._cand_hover = index
            self._draw_candidates()

    def _on_cand_wheel(self, event) -> None:
        self.cand_canvas.yview_scroll(-1 * (event.delta // 120), "units")

    def _set_field(self, key: str, value) -> None:
        entry = self.fields[key]
        entry.delete(0, "end")
        if value:
            entry.insert(0, str(value))

    def _mmss(self, seconds) -> str:
        total = int(seconds or 0)
        return f"{total // 60}:{total % 60:02d}"

    # ------------------------------------------------------------ 保存

    def _set_status(self, text: str, sticky: bool = False) -> None:
        """更新底部状态。

        sticky=True 表示这条要留住一会儿（比如"已保存"），
        期间后台搜索回来的结果不要把它顶掉。
        """
        if not sticky and time.monotonic() < getattr(self, "_status_lock", 0.0):
            return
        if sticky:
            self._status_lock = time.monotonic() + 2.5
        try:
            self.status_label.configure(text=text)
        except tk.TclError:
            pass

    def save_current(self) -> None:
        track = self.current
        if track is None:
            return
        fields = {}
        for key, entry in self.fields.items():
            value = entry.get().strip()
            if value:
                fields[key] = value
        lyrics = self.lyrics_text.get("1.0", "end").strip() or None
        cover = self._pending_cover
        if cover is None and getattr(self, "_cover_waiting", False):
            # 封面还在下载：最多等 3 秒，别让用户以为没封面
            waited = getattr(self, "_cover_wait_ticks", 0)
            if waited < 20:
                self._cover_wait_ticks = waited + 1
                self._set_status("正在获取封面…")
                self.root.after(150, self.save_current)
                return
        self._cover_wait_ticks = 0
        if not fields and not lyrics and not cover:
            self.status_label.configure(text="没有可保存的内容")
            return
        self.status_label.configure(text="正在写入…")
        self._advance = getattr(self, "_advance", False)

        item = _pending(track, fields, cover, lyrics)

        def work():
            from ..write import service
            from ..write.writer import WriteOptions

            opts = WriteOptions(
                write_cover=bool(self.ctx.settings.get("write_cover", True)),
                replace_cover=bool(self.ctx.settings.get("replace_cover", True)),
                write_cover_file=bool(
                    self.ctx.settings.get("write_cover_file", True)),
            )
            return service.write_batch(
                self.ctx.conn, [item],
                self.ctx.library_root or os.path.dirname(track.path),
                opts=opts,
                fallback_dir=self.ctx.data_dir,
            )

        def finish(summary) -> None:
            if summary.get("ok"):
                note = "（经系统程序写入）" if summary.get("via_helper") else ""
                self._set_status(f"已保存{note}", sticky=True)
                refreshed = scan.refresh_one(self.ctx.conn, track.path)
                if refreshed is not None:
                    self.current = refreshed
                    for index, item_track in enumerate(self.tracks):
                        if item_track.path == track.path:
                            self.tracks[index] = refreshed
                            break
                self.refresh_list()
                if getattr(self, "_advance", False):
                    self._advance = False
                    self._move(1)
            else:
                self._advance = False
                error = summary.get("error") or (
                    summary["items"][0].error if summary.get("items") else "未知错误")
                self._set_status(f"保存失败：{str(error).splitlines()[0]}", sticky=True)

        self._run_async(work, finish,
                        lambda exc: self._set_status(f"保存失败：{exc}", sticky=True))

    def save_and_next(self) -> None:
        self._advance = True
        self.save_current()

    def skip_current(self) -> None:
        track = self.current
        if track is None:
            return
        db.set_status(self.ctx.conn, track.id or 0, "ignored")
        self.ctx.conn.commit()
        self._move(1)

    def _move(self, step: int) -> None:
        if not self.tracks:
            return
        target = max(0, min(len(self.tracks) - 1, self.selected_index + step))
        self._select_track(target)

    # ------------------------------------------------------------ 歌词

    def fetch_lyrics(self) -> None:
        track = self.current
        if track is None or not self.candidates:
            self.lyric_hint.configure(text="先有候选结果")
            return
        providers = self.ctx.providers
        from ..core import evidence as evidence_mod

        # 文件名有「歌手 - 歌名」也有「歌名 - 歌手」，两种都算同名
        wanted = {
            evidence_mod.normalize(getattr(track, "parsed_title", "") or ""),
            evidence_mod.normalize(getattr(track, "parsed_artist", "") or ""),
            evidence_mod.normalize(track.tag_title or ""),
        }
        wanted.discard("")

        def quality(cand: Candidate):
            same = 1 if evidence_mod.normalize(cand.title) in wanted else 0
            hits = sum(1 for value in (cand.evidence or {}).values()
                       if value >= 0.75)
            return (same, hits, cand.score or 0.0)

        # 优先同名、命中多的候选，避免拿到演唱会串烧版或翻唱的歌词
        candidates = sorted(self.candidates, key=quality, reverse=True)[:8]
        self.lyric_hint.configure(text="抓取中…")

        def work():
            from ..core import lyrics as lyrics_mod

            best = None
            for cand in candidates:
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

        def finish(payload) -> None:
            text, source = payload
            if not text:
                self.lyric_hint.configure(text="没有取到歌词")
                return
            self.lyrics_text.delete("1.0", "end")
            self.lyrics_text.insert("1.0", text)
            self._show_lyrics_box(True)
            self.lyric_hint.configure(text=f"来自 {source}")

        self._run_async(work, finish,
                        lambda exc: self.lyric_hint.configure(text=f"失败：{exc}"))

    def save_lyrics_file(self) -> None:
        track = self.current
        if track is None:
            return
        text = self.lyrics_text.get("1.0", "end").strip()
        if not text:
            self.lyric_hint.configure(text="没有歌词可存")
            return
        from ..core import lyrics as lyrics_mod

        try:
            written = lyrics_mod.save_lrc(track.path, text, overwrite=True)
        except Exception as exc:
            self.lyric_hint.configure(text=f"保存失败：{exc}")
            return
        self.lyric_hint.configure(text=f"已存为 {os.path.basename(written)}")

    # ------------------------------------------------------------ 动作

    def choose_folder(self) -> None:
        """选择音乐文件夹（旧版的「更改」按钮）。"""
        from tkinter import filedialog

        folder = filedialog.askdirectory(
            title="选择音乐文件夹",
            initialdir=self.ctx.library_root or "",
            parent=self.root,
        )
        if not folder:
            return
        self.ctx.library_root = folder
        self.ctx.settings["library_root"] = folder
        self.ctx.save_settings()
        self.current = None
        self.selected_index = -1
        self.status_label.configure(text=f"已选择 {folder}")
        self.scan_library()

    def scan_library(self) -> None:
        root = self.ctx.library_root
        if not root or not os.path.isdir(root):
            # 没配目录时直接让用户选，而不是弹一句"还没选择"
            self.choose_folder()
            return
        self.status_label.configure(text="扫描中…")

        def work():
            return scan.scan(self.ctx.conn, root)

        def finish(_result) -> None:
            self.current = None
            self.selected_index = -1
            self.refresh_list()
            self.status_label.configure(text="扫描完成")

        self._run_async(work, finish,
                        lambda exc: self.status_label.configure(text=f"扫描失败：{exc}"))

    def open_batch(self) -> None:
        from .batch_glass import GlassBatchDialog

        if not self.tracks:
            messagebox.showinfo("Cadence", "先扫描音乐库")
            return
        if not self.ctx.providers:
            messagebox.showwarning("Cadence", "数据源全部关闭了，先去设置里打开")
            return

        dialog = GlassBatchDialog(self.root, self.ctx, self.tracks, self)
        self._show_dialog(dialog)
        self.root.wait_window(dialog)

    def open_settings(self) -> None:
        from .settings_glass import GlassSettingsDialog

        old_mode = self.mode
        dialog = GlassSettingsDialog(self.root, self.ctx, self)
        self._show_dialog(dialog)
        self.root.wait_window(dialog)
        self.mode = self.ctx.settings.get("theme", "light")
        # 只有真的换了主题才整窗重绘，否则关掉设置会"闪"一下
        if self.mode != old_mode:
            self.palette = visual.theme(self.mode)
            self._photos.clear()
            self._render_background()
            self._sync_colors()

    def _show_dialog(self, dialog) -> None:
        """把对话框摆到窗口中央并抢到最前，避免闪屏。"""
        dialog.withdraw()
        if not getattr(dialog, "_self_positioned", False):
            dialog.update_idletasks()
            width = max(480, dialog.winfo_reqwidth())
            height = max(420, dialog.winfo_reqheight())
            x = self.root.winfo_rootx() + (self.root.winfo_width() - width) // 2
            y = self.root.winfo_rooty() + (self.root.winfo_height() - height) // 2
            dialog.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
        # 无边框窗口再设 transient 会被系统改成工具窗口、丢掉 WS_POPUP，
        # 拖动时又会开始补边框（拖影），所以这类窗口不设 owner。
        try:
            if not dialog.overrideredirect():
                dialog.transient(self.root)
        except Exception:
            pass
        dialog.deiconify()
        dialog.lift()
        try:
            dialog.focus_force()
            dialog.grab_set()
        except tk.TclError:
            pass

    # ------------------------------------------------------------ 动画

    def _fade_in(self) -> None:
        """不再做透明度淡入。

        -alpha 会把窗口标成 WS_EX_LAYERED，移动大内容时合成跟不上，
        屏幕上就是错位残影 + 边缘黑块。这里保留函数名，只做收尾处理。
        """
        self._after_fade()

    def _after_fade(self) -> None:
        """淡入结束后撤掉分层样式，否则拖动会残影、边缘发黑。"""
        try:
            from . import win_frame

            win_frame.drop_layered(self.root)
            win_frame.set_border_color(
                self.root, visual.to_hex(self.palette["window"])
            )
        except Exception:
            pass

    # ------------------------------------------------------------ 截图辅助

    def prepare_for_shot(self, library: str) -> None:
        """截图用：同步扫描 + 造几条候选，不联网。"""
        if library and os.path.isdir(library):
            self.ctx.library_root = library
            scan.scan(self.ctx.conn, library)
        self.current = None
        self.selected_index = -1
        self.refresh_list()
        if not self.tracks:
            return
        self._select_track(0)
        self.candidates = [
            _demo_candidate("网易云", "晴天", "周杰伦", "叶惠美", 269.0, "1",
                            {"title": 1.0, "artist": 1.0, "album": 1.0,
                             "duration": 1.0, "track": 1.0, "context": 0.5}),
            _demo_candidate("QQ音乐", "晴天", "周杰伦", "叶惠美", 269.5, "1",
                            {"title": 1.0, "artist": 1.0, "album": 1.0,
                             "duration": 1.0, "track": 0.0, "context": 0.5}),
            _demo_candidate("MusicBrainz", "Qing Tian", "Jay Chou", "Ye Hui Mei",
                            268.0, "", {"title": 0.4, "artist": 1.0, "album": 0.6,
                                        "duration": 1.0, "track": 0.0, "context": 0.0}),
        ]
        self._draw_candidates()
        self.status_label.configure(text="3 条候选")
        self.root.update_idletasks()


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _demo_candidate(source: str, title: str, artist: str, album: str,
                    duration: float, track: str, evidence: dict) -> Candidate:
    cand = Candidate(source=source, title=title, artist=artist, album=album,
                     duration=duration, track=track)
    cand.evidence = evidence
    return cand


def _pending(track, fields: dict, cover, lyrics):
    from ..write.service import PendingWrite

    return PendingWrite(track=track, fields=fields, cover=cover, lyrics=lyrics)


"""毛玻璃对话框基类：圆角、无边框、可拖动，和主窗口同一套视觉。"""
from __future__ import annotations

import tkinter as tk
from PIL import ImageTk

from . import visual

PAD = 14
TITLE_H = 46
TITLE_H_SUB = 58


class GlassDialog(tk.Toplevel):
    """自带圆角背景与标题栏的对话框。

    子类往 self.body 里放控件即可；配色用 self.bg / self.palette。
    """

    def __init__(self, parent, title: str, width: int = 520, height: int = 640,
                 subtitle: str = ""):
        super().__init__(parent)
        self.parent = parent
        self.mode = "light"
        try:
            ctx = getattr(parent, "ctx", None)
            if ctx is not None:
                self.mode = ctx.settings.get("theme", "light")
        except Exception:
            self.mode = "light"
        self.palette = visual.theme(self.mode)
        self.width = width
        self.height = height
        self._photos: dict[str, ImageTk.PhotoImage] = {}

        self._self_positioned = True  # 自己算好尺寸和位置，外层不要再改
        self._has_subtitle = bool(subtitle)
        self.configure(bg=visual.to_hex(self.palette["window"]))
        # 和主窗口一样做成无边框窗口：窗口矩形=客户区，
        # 拖动时不会因为「系统补一圈边框 / Tk 再扣一圈」而反复重绘（拖影）
        try:
            self.overrideredirect(True)
        except tk.TclError:
            pass
        self._place_near_parent(width, height)
        try:
            from . import win_frame

            win_frame.prepare(self, rounded=True, taskbar=False, borderless=True)
        except Exception:
            pass

        self._render_background()
        title_height = TITLE_H_SUB if subtitle else TITLE_H
        self._build_title(title, subtitle)
        self.body = tk.Frame(self, bg=self.bg)
        self.body.place(x=PAD + 14, y=title_height + 6,
                        width=width - (PAD + 14) * 2,
                        height=height - title_height - PAD - 18)

        self.bind("<Escape>", lambda _e: self.close())
        self.after(30, self._sync_colors)
        self.after(10, self._apply_frame)

    def _apply_frame(self) -> None:
        """和主窗口一样：去掉系统标题栏、用系统圆角。"""
        try:
            from . import win_frame

            self.update_idletasks()
            win_frame.prepare(self, rounded=True, taskbar=False, borderless=True)
            win_frame.set_border_color(self, visual.to_hex(self.palette["window"]))
            width, height = self.winfo_width(), self.winfo_height()
            if width > 100 and height > 100 and (width, height) != (self.width, self.height):
                self.width, self.height = width, height
                self._render_background()
                self._relayout()
        except Exception:
            pass

    def _relayout(self) -> None:
        title_height = TITLE_H_SUB if self._has_subtitle else TITLE_H
        self.canvas.configure(width=self.width, height=self.height)
        self.canvas.place(x=0, y=0)
        self.body.place(x=PAD + 14, y=title_height + 6,
                        width=self.width - (PAD + 14) * 2,
                        height=self.height - title_height - PAD - 18)

    # ------------------------------------------------------------ 位置

    def _place_near_parent(self, width: int, height: int) -> None:
        try:
            px, py = self.parent.winfo_rootx(), self.parent.winfo_rooty()
            pw, ph = self.parent.winfo_width(), self.parent.winfo_height()
        except tk.TclError:
            px = py = 0
            pw, ph = 1440, 900
        x = max(0, px + (pw - width) // 2)
        y = max(0, py + (ph - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    # ------------------------------------------------------------ 背景

    def _render_background(self) -> None:
        backdrop = visual.compose_backdrop(None, self.width, self.height, self.mode)
        image = backdrop.copy()
        box = (PAD, PAD, self.width - PAD, self.height - PAD)
        panel = visual.glass_panel(backdrop, box, visual.RADIUS_CARD,
                                   self.mode, strong=True)
        image.alpha_composite(panel, (box[0], box[1]))

        # 圆角交给系统（和主窗口一致），图片只负责铺满
        self._bg_image = image.convert("RGB")

        photo = ImageTk.PhotoImage(self._bg_image)
        self._photos["bg"] = photo
        if getattr(self, "canvas", None) is None:
            self.canvas = tk.Canvas(self, width=self.width, height=self.height,
                                    highlightthickness=0, bd=0,
                                    bg=visual.to_hex(self.palette["window"]))
            self.canvas.place(x=0, y=0)
            self._canvas_item = self.canvas.create_image(0, 0, anchor="nw", image=photo)
            self.canvas.bind("<ButtonPress-1>", self._start_move)
            self.canvas.bind("<B1-Motion>", self._do_move)
        else:
            self.canvas.configure(width=self.width, height=self.height,
                                  bg=visual.to_hex(self.palette["window"]))
            self.canvas.place(x=0, y=0)
            self.canvas.itemconfigure(self._canvas_item, image=photo)
        # 背景永远在最底层，别盖住内容
        try:
            self.canvas.tk.call("lower", self.canvas._w)
        except tk.TclError:
            pass
        body = getattr(self, "body", None)
        if body is not None:
            try:
                body.tkraise()
            except tk.TclError:
                pass

        self.bg = visual.sample_color(self._bg_image,
                                      (PAD + 30, PAD + 30,
                                       self.width - PAD - 30, self.height - PAD - 30))

    def _build_title(self, title: str, subtitle: str) -> None:
        title_height = TITLE_H_SUB if subtitle else TITLE_H
        bar = tk.Frame(self, bg=self.bg, height=title_height)
        bar.place(x=PAD, y=PAD, width=self.width - PAD * 2, height=title_height)
        bar.bind("<ButtonPress-1>", self._start_move)
        bar.bind("<B1-Motion>", self._do_move)

        label = tk.Label(bar, text=title, bg=self.bg, fg=self.palette["text"],
                         font=(visual.FONT, 12, "bold"))
        if subtitle:
            label.place(x=14, y=8, anchor="nw")
            tk.Label(bar, text=subtitle, bg=self.bg, fg=self.palette["text3"],
                     font=(visual.FONT, 8)).place(x=15, y=30, anchor="nw")
        else:
            label.place(x=14, rely=0.5, anchor="w")
        label.bind("<ButtonPress-1>", self._start_move)
        label.bind("<B1-Motion>", self._do_move)

        close = tk.Label(bar, text="✕", bg=self.bg, fg=self.palette["text2"],
                         font=(visual.FONT, 10), padx=8, pady=2, cursor="hand2")
        close._keep_bg = True
        close.place(relx=1.0, x=-10, rely=0.5, anchor="e")
        hover = _blend(self.bg, (0, 0, 0, 18))
        close.bind("<Enter>", lambda _e: close.configure(bg=hover))
        close.bind("<Leave>", lambda _e: close.configure(bg=self.bg))
        close.bind("<Button-1>", lambda _e: self.close())

    # ------------------------------------------------------------ 通用件

    def section(self, parent, text: str, pady=(10, 3)) -> tk.Label:
        label = tk.Label(parent, text=text, bg=self.bg, fg=self.palette["text3"],
                         font=(visual.FONT, 8, "bold"), anchor="w")
        label.pack(fill="x", pady=pady)
        return label

    def check(self, parent, text: str, value: bool, command=None) -> tk.BooleanVar:
        """圆角勾选框（自绘，和整体风格一致）。"""
        from .controls import Toggle

        toggle = Toggle(parent, self.palette, self.bg, text,
                        value=value, command=command)
        toggle.pack(fill="x", pady=2)
        return toggle.var

    def radio(self, parent, text: str, value: str, variable: tk.StringVar,
              command=None):
        """圆角单选钮：点一下把 variable 设成 value。"""
        from .controls import Toggle

        toggle = Toggle(parent, self.palette, self.bg, text,
                        value=(variable.get() == value), radio=True,
                        command=lambda: _pick(variable, value, command))
        toggle.pack(fill="x", pady=2)
        variable.trace_add("write", lambda *_a: toggle.set(variable.get() == value))
        return toggle

    def button(self, parent, text: str, command, primary: bool = False,
               height: int = 30, padx: int = 16):
        """圆角胶囊按钮（用图片做底，和主窗口一致）。"""
        import tkinter.font as tkfont

        font = (visual.FONT, 9, "bold" if primary else "normal")
        width = tkfont.Font(font=font).measure(text) + padx * 2
        if primary:
            base = self.palette["accent"]
            hover = _blend(base, (255, 255, 255, 40))
            fg = "#FFFFFF"
        else:
            base = _blend(self.bg, (0, 0, 0, 16))
            hover = _blend(base, (0, 0, 0, 20))
            fg = self.palette["text"]

        def render(color: str):
            key = f"btn:{text}:{width}:{height}:{color}"
            photo = self._photos.get(key)
            if photo is None:
                image = visual.rounded_image(
                    (width, height), max(6, height // 2),
                    visual.hex_to_rgba(color),
                )
                photo = ImageTk.PhotoImage(image)
                self._photos[key] = photo
            return photo

        label = tk.Label(parent, text=text, image=render(base), compound="center",
                         bg=self.bg, fg=fg, font=font, bd=0, padx=0, pady=0,
                         cursor="hand2")
        label._keep_bg = True
        label._render = render
        label._base = base
        label._hover = hover
        label.bind("<Enter>", lambda _e: label.configure(image=render(hover)))
        label.bind("<Leave>", lambda _e: label.configure(image=render(base)))
        label.bind("<Button-1>", lambda _e: command())
        return label

    # ------------------------------------------------------------ 交互

    def _sync_colors(self) -> None:
        try:
            self.update_idletasks()
        except tk.TclError:
            return
        root_x, root_y = self.winfo_rootx(), self.winfo_rooty()
        image = getattr(self, "_bg_image", None)
        if image is None:
            return

        def walk(widget) -> None:
            for child in widget.winfo_children():
                if getattr(child, "_keep_bg", False):
                    continue
                if isinstance(child, (tk.Frame, tk.Label)):
                    x = child.winfo_rootx() - root_x
                    y = child.winfo_rooty() - root_y
                    w, h = child.winfo_width(), child.winfo_height()
                    if w > 2 and h > 2:
                        child.configure(bg=visual.sample_color(
                            image, (x, y, x + w - 1, y + h - 1)))
                walk(child)

        walk(self)

    def _start_move(self, event) -> None:
        self._drag = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())
        self._drag_pos = None
        self._drag_active = True
        self.after(16, self._drag_tick)

    def _drag_tick(self) -> None:
        """和主窗口一样用定时器移动，避免逐事件移动造成的拖影。"""
        if not getattr(self, "_drag_active", False):
            return
        try:
            from . import win_frame

            if not win_frame.left_button_down():
                self._drag_active = False
                self._drag = None
                return
            x, y = win_frame.cursor_pos()
            offset = getattr(self, "_drag", None)
            if offset:
                target = (x - offset[0], y - offset[1])
                # 位置没变就别再动窗口，否则每 16ms 一次无效移动会反复重绘
                if target != getattr(self, "_drag_pos", None):
                    self._drag_pos = target
                    win_frame.move_window(self, target[0], target[1])
        except Exception:
            self._drag_active = False
            return
        try:
            self.after(16, self._drag_tick)
        except tk.TclError:
            self._drag_active = False

    def _do_move(self, _event=None) -> None:
        """移动交给定时器。"""

    def close(self) -> None:
        try:
            from . import win_frame

            win_frame.uninstall_nc_handler(self)
        except Exception:
            pass
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


def _blend(base: str, rgba) -> str:
    base = base.lstrip("#")
    r, g, b = (int(base[i:i + 2], 16) for i in (0, 2, 4))
    ratio = rgba[3] / 255
    return "#%02x%02x%02x" % (
        int(r + (rgba[0] - r) * ratio),
        int(g + (rgba[1] - g) * ratio),
        int(b + (rgba[2] - b) * ratio),
    )


def _pick(variable, value, command) -> None:
    """单选：点中哪个就把变量设成哪个。"""
    variable.set(value)
    if callable(command):
        command()


def _solid(width: int, height: int, hex_color: str):
    from PIL import Image

    hex_color = hex_color.lstrip("#")
    rgb = tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    return Image.new("RGBA", (width, height), rgb + (255,))

"""自绘的勾选框与单选钮：圆角、带抗锯齿，和整体风格一致。"""
from __future__ import annotations

import tkinter as tk
from PIL import Image, ImageDraw, ImageTk

from . import visual

BOX = 20
_RADIUS = 6
_SCALE = 4


def _check_icon(color, size: int = BOX) -> Image.Image:
    """对勾：两条线段。"""
    s = size * _SCALE
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.line([(s * 0.24, s * 0.52), (s * 0.43, s * 0.71)],
              fill=color, width=int(s * 0.11), joint="curve")
    draw.line([(s * 0.43, s * 0.71), (s * 0.77, s * 0.30)],
              fill=color, width=int(s * 0.11), joint="curve")
    return image.resize((size, size), Image.LANCZOS)


def _ring(ring_color, accent, checked: bool, size: int = BOX) -> Image.Image:
    """单选钮：细圆环 + 选中时中心实心点。"""
    s = size * _SCALE
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    pad = int(s * 0.07)
    width = max(2, int(s * 0.075))
    draw.ellipse((pad, pad, s - 1 - pad, s - 1 - pad),
                 outline=ring_color, width=width)
    if checked:
        inner = int(s * 0.17)
        cx = cy = s // 2
        draw.ellipse((cx - inner, cy - inner, cx + inner, cy + inner), fill=accent)
    return image.resize((size, size), Image.LANCZOS)


class Toggle(tk.Frame):
    """一个可点击的圆角勾选框 / 圆形单选钮 + 文字。"""

    def __init__(self, parent, palette: dict, bg: str, text: str,
                 value: bool = False, command=None, radio: bool = False,
                 font=None):
        super().__init__(parent, bg=bg)
        self.var = tk.BooleanVar(value=bool(value))
        self._palette = palette
        self._bg = bg
        self._radio = radio
        self._command = command
        self._hover = False
        self._photos: dict[str, ImageTk.PhotoImage] = {}

        self._box = tk.Label(self, bd=0, bg=bg, cursor="hand2")
        self._box.pack(side="left", padx=(2, 9), pady=2)

        self._label = tk.Label(self, text=text, bg=bg, fg=palette["text"],
                               font=font or (visual.FONT, 9), anchor="w",
                               cursor="hand2")
        self._label.pack(side="left")

        for widget in (self, self._box, self._label):
            widget.bind("<Button-1>", self._on_click)
            widget.bind("<Enter>", lambda _e: self._set_hover(True))
            widget.bind("<Leave>", lambda _e: self._set_hover(False))
        # 外部代码（比如「全选」「全不选」）直接改 BooleanVar 时也要重画，
        # 否则框里的勾还在，实际值已经变了。
        self.var.trace_add("write", self._on_var_changed)
        self._redraw()

    def _on_var_changed(self, *_args) -> None:
        self._redraw()

    # ------------------------------------------------------------ 状态

    def get(self) -> bool:
        return bool(self.var.get())

    def set(self, value: bool) -> None:
        self.var.set(bool(value))

    def _on_click(self, _event=None) -> None:
        self.var.set(not self.var.get())
        if callable(self._command):
            self._command()

    def _set_hover(self, value: bool) -> None:
        if self._hover != value:
            self._hover = value
            self._redraw()

    def apply_bg(self, bg: str) -> None:
        self._bg = bg
        self.configure(bg=bg)
        self._box.configure(bg=bg)
        self._label.configure(bg=bg)
        self._redraw()

    # ------------------------------------------------------------ 绘制

    def _key(self, checked: bool, hovered: bool) -> str:
        return f"{'on' if checked else 'off'}{'h' if hovered else ''}"

    def _build(self, checked: bool, hovered: bool) -> Image.Image:
        palette = self._palette
        accent = palette["accent"]
        base = Image.new("RGBA", (BOX, BOX), (0, 0, 0, 0))

        if self._radio:
            ring = accent if (checked or hovered) else palette["text3"]
            return _ring(ring, accent, checked)

        if checked:
            fill = Image.new("RGBA", (BOX, BOX), (0, 0, 0, 0))
            box = visual.rounded_image((BOX, BOX), _RADIUS,
                                       visual.hex_to_rgba(accent))
            fill = Image.alpha_composite(fill, box)
            fill = Image.alpha_composite(fill, _check_icon((255, 255, 255, 245)))
            return fill

        outline = accent if hovered else palette["text3"]
        return visual.rounded_image((BOX, BOX), _RADIUS,
                                    fill=(0, 0, 0, 0), outline=outline, width=2)

    def _redraw(self) -> None:
        checked = self.var.get()
        key = self._key(checked, self._hover)
        photo = self._photos.get(key)
        if photo is None:
            try:
                photo = ImageTk.PhotoImage(self._build(checked, self._hover))
            except Exception:
                return
            self._photos[key] = photo
        self._box.configure(image=photo, bg=self._bg)
        self._label.configure(
            fg=self._palette["text"] if not self._hover else self._palette["text"],
            bg=self._bg,
        )

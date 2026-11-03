"""新界面的视觉与控件测试：文字截断、LOGO、自绘勾选框、窗口框架。"""
from __future__ import annotations

import os

import pytest
from PIL import Image

from app.ui_tk import controls, logo, visual, win_frame


def test_ellipsis_short_text_unchanged(tk_root):
    from app.ui_tk.main_window_v2 import _ellipsis

    assert _ellipsis("晴天", ("Microsoft YaHei UI", 10), 200) == "晴天"


def test_ellipsis_adds_dots_when_too_long(tk_root):
    from app.ui_tk.main_window_v2 import _ellipsis

    text = "2Lson - Begging You (Guide Ver.) (Bonus Track).flac"
    out = _ellipsis(text, ("Microsoft YaHei UI", 10), 120)
    assert out.endswith("…")
    assert len(out) < len(text)


def test_ellipsis_handles_empty(tk_root):
    from app.ui_tk.main_window_v2 import _ellipsis

    assert _ellipsis("", ("Microsoft YaHei UI", 10), 100) == ""


def test_logo_render_size_and_corners():
    image = logo.render(128)
    assert image.size == (128, 128)
    assert image.mode == "RGBA"
    assert image.getpixel((64, 64))[3] == 255   # 中心不透明
    assert image.getpixel((1, 1))[3] == 0       # 圆角外透明


def test_logo_files_created(tmp_path):
    png, ico = logo.ensure_files()
    assert os.path.exists(png) and os.path.getsize(png) > 1000
    assert os.path.exists(ico) and os.path.getsize(ico) > 1000
    assert ico.lower().endswith(".ico")


def test_check_icon_shape():
    image = controls._check_icon((255, 255, 255, 255))
    assert image.size == (controls.BOX, controls.BOX)
    assert image.mode == "RGBA"
    assert image.getpixel((controls.BOX // 2, controls.BOX // 2))[3] > 0


def test_radio_ring_has_hole_and_dot():
    off = controls._ring((0, 0, 0, 255), (10, 132, 255, 255), checked=False)
    on = controls._ring((10, 132, 255, 255), (10, 132, 255, 255), checked=True)
    centre = controls.BOX // 2
    assert off.getpixel((centre, centre))[3] == 0      # 未选中：中心空
    assert on.getpixel((centre, centre))[3] > 0        # 选中：中心实心


def test_rounded_mask_is_antialiased():
    mask = visual.rounded_mask((40, 40), 10)
    # 圆角边缘应该有中间值（不是纯 0/255 的台阶）
    values = {mask.getpixel((x, 1)) for x in range(0, 14)}
    assert any(0 < v < 255 for v in values), values


def test_rounded_image_corner_transparent():
    image = visual.rounded_image((30, 30), 8, (255, 0, 0, 255))
    assert image.getpixel((0, 0))[3] == 0
    assert image.getpixel((15, 15))[3] == 255


def test_hwnd_of_returns_handle(tk_root):
    handle = win_frame.hwnd_of(tk_root)
    assert isinstance(handle, int) and handle > 0


def test_toggle_widget_roundtrip(tk_root):
    import tkinter as tk

    frame = tk.Frame(tk_root)
    toggle = controls.Toggle(frame, visual.LIGHT, "#ffffff", "测试", value=False)
    assert toggle.get() is False
    toggle.set(True)
    assert toggle.get() is True
    toggle._on_click()
    assert toggle.get() is False
    frame.destroy()

"""视觉合成层：模糊、圆角、玻璃面板、取色。"""
from __future__ import annotations

from PIL import Image

from app.ui_tk import visual


def test_placeholder_cover_size_and_alpha():
    image = visual.placeholder_cover(96, "晴天")
    assert image.size == (96, 96)
    assert image.mode == "RGBA"
    # 中心不透明，四角被裁圆角
    assert image.getpixel((48, 48))[3] == 255
    assert image.getpixel((0, 0))[3] == 0


def test_cover_from_bytes_rejects_junk():
    assert visual.cover_from_bytes(b"not an image", 64) is None


def test_cover_from_bytes_rounds_corners():
    from io import BytesIO

    buffer = BytesIO()
    Image.new("RGB", (200, 120), (30, 120, 220)).save(buffer, "PNG")
    image = visual.cover_from_bytes(buffer.getvalue(), 80, radius=12)
    assert image is not None
    assert image.size == (80, 80)
    assert image.getpixel((0, 0))[3] == 0
    assert image.getpixel((40, 40))[3] == 255


def test_compose_backdrop_without_cover():
    backdrop = visual.compose_backdrop(None, 320, 200, "light")
    assert backdrop.size == (320, 200)
    assert backdrop.mode == "RGBA"


def test_compose_backdrop_with_cover():
    cover = Image.new("RGBA", (100, 100), (200, 40, 40, 255))
    backdrop = visual.compose_backdrop(cover, 200, 120, "light")
    assert backdrop.size == (200, 120)


def test_glass_panel_has_transparent_corners():
    backdrop = visual.compose_backdrop(None, 300, 200, "light")
    panel = visual.glass_panel(backdrop, (20, 20, 220, 160), 14, "light")
    assert panel.size == (200, 140)
    assert panel.getpixel((0, 0))[3] == 0
    assert panel.getpixel((100, 70))[3] == 255


def test_sample_color_returns_hex():
    image = Image.new("RGB", (10, 10), (18, 52, 86))
    assert visual.sample_color(image, (0, 0, 9, 9)) == "#123456"


def test_blur_keeps_size():
    image = Image.new("RGB", (64, 64), (255, 255, 255))
    assert visual.blur(image, 8).size == (64, 64)


def test_lerp_image_midpoint():
    first = Image.new("RGB", (4, 4), (0, 0, 0))
    second = Image.new("RGB", (4, 4), (200, 200, 200))
    middle = visual.lerp_image(first, second, 0.5)
    assert 80 < middle.getpixel((0, 0))[0] < 120


def test_theme_switch():
    assert visual.theme("dark")["text"] == "#F5F5F7"
    assert visual.theme("light")["text"] == "#1C1C1E"

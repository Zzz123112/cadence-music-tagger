"""用 Pillow 手工合成 iOS 风格视觉元素。

Tkinter 没有原生模糊/半透明，所以这里全部预渲染成图片：
背景模糊、玻璃面板、圆角封面、阴影、占位图。
"""
from __future__ import annotations

import io
import os
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter

# ---------------------------------------------------------------- 配色

LIGHT = {
    "window": (242, 242, 247),
    "tint": (255, 255, 255, 170),
    "tint_strong": (255, 255, 255, 214),
    "border": (255, 255, 255, 230),
    "border_sub": (0, 0, 0, 26),
    "text": "#1C1C1E",
    "text2": "#55555C",
    "text3": "#84848A",
    "accent": "#0A84FF",
    "accent_rgb": (10, 132, 255),
    "danger": "#FF3B30",
    "ok": "#34C759",
    "hover": (0, 0, 0, 12),
    "select": (10, 132, 255, 30),
}

DARK = {
    "window": (20, 20, 22),
    "tint": (255, 255, 255, 24),
    "tint_strong": (255, 255, 255, 38),
    "border": (255, 255, 255, 40),
    "border_sub": (255, 255, 255, 20),
    "text": "#F5F5F7",
    "text2": "#A1A1A6",
    "text3": "#6E6E73",
    "accent": "#0A84FF",
    "accent_rgb": (10, 132, 255),
    "danger": "#FF453A",
    "ok": "#32D74B",
    "hover": (255, 255, 255, 18),
    "select": (10, 132, 255, 52),
}

RADIUS_WINDOW = 24
RADIUS_CARD = 18
TRANSPARENT_KEY = "#FE01FE"  # 用作圆角透明色，正常界面不会出现

FONT = "Microsoft YaHei UI"


def theme(name: str) -> dict:
    return DARK if name == "dark" else LIGHT


def to_hex(value) -> str:
    """把配色常量（字符串或 RGB 元组）统一成 #rrggbb。"""
    if isinstance(value, str):
        return value
    return "#%02x%02x%02x" % tuple(int(v) for v in value[:3])


# ---------------------------------------------------------------- 基础绘制

def _blend(color_a, color_b, ratio: float):
    ratio = max(0.0, min(1.0, ratio))
    return tuple(int(a + (b - a) * ratio) for a, b in zip(color_a[:3], color_b[:3]))


def rounded_mask(size: tuple[int, int], radius: int, scale: int = 2) -> Image.Image:
    """抗锯齿圆角蒙版：放大绘制再缩小，边缘没有台阶。"""
    width, height = max(1, size[0]), max(1, size[1])
    big = Image.new("L", (width * scale, height * scale), 0)
    ImageDraw.Draw(big).rounded_rectangle(
        (0, 0, width * scale - 1, height * scale - 1),
        radius=max(1, radius * scale), fill=255,
    )
    return big.resize((width, height), Image.LANCZOS)


def hex_to_rgba(value: str, alpha: int = 255):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4)) + (alpha,)


def rounded_image(size, radius, fill, outline=None, width: int = 1,
                  scale: int = 2) -> Image.Image:
    """圆角矩形（RGBA，抗锯齿）。"""
    w, h = max(1, size[0]), max(1, size[1])
    big = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
    ImageDraw.Draw(big).rounded_rectangle(
        (0, 0, w * scale - 1, h * scale - 1),
        radius=max(1, radius * scale),
        fill=fill,
        outline=outline,
        width=width * scale if outline else 0,
    )
    return big.resize((w, h), Image.LANCZOS)


def with_shadow(image: Image.Image, blur: int = 12, offset: int = 4,
                alpha: int = 60) -> Image.Image:
    """给图片加柔和投影，四周留出空间。"""
    pad = blur * 2
    canvas = Image.new("RGBA", (image.width + pad * 2, image.height + pad * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow.paste((0, 0, 0, alpha), (0, 0), image.split()[-1])
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    canvas.alpha_composite(shadow, (pad, pad + offset))
    canvas.alpha_composite(image, (pad, pad))
    return canvas


def blur(image: Image.Image, radius: int = 40) -> Image.Image:
    return image.filter(ImageFilter.GaussianBlur(radius))


def cover_from_bytes(data: bytes, size: int, radius: int = 14) -> Image.Image | None:
    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None
    return _fit_cover(image, size, radius)


def _fit_cover(image: Image.Image, size: int, radius: int) -> Image.Image:
    """等比裁切填充成正方形，再裁圆角。"""
    ratio = max(size / image.width, size / image.height)
    resized = image.resize(
        (max(1, int(image.width * ratio)), max(1, int(image.height * ratio))),
        Image.LANCZOS,
    )
    left = (resized.width - size) // 2
    top = (resized.height - size) // 2
    cropped = resized.crop((left, top, left + size, top + size)).convert("RGBA")
    mask = rounded_mask((size, size), radius)
    cropped.putalpha(mask)
    return cropped


@lru_cache(maxsize=64)
def placeholder_cover(size: int, seed: str = "") -> Image.Image:
    """没有封面时的渐变占位图（低饱和，避免和背景打架）。"""
    hue = (215 + (sum(ord(ch) for ch in seed) % 40) - 20) % 360 if seed else 215
    top = _hsv(hue, 0.22, 0.96)
    bottom = _hsv((hue + 24) % 360, 0.34, 0.80)
    image = Image.new("RGB", (size, size), top)
    draw = ImageDraw.Draw(image)
    for y in range(size):
        draw.line([(0, y), (size, y)], fill=_blend(top, bottom, y / max(1, size - 1)))

    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse(
        (-size * 0.25, -size * 0.3, size * 0.8, size * 0.55),
        fill=(255, 255, 255, 70),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(size * 0.18))
    image = Image.alpha_composite(image.convert("RGBA"), glow)

    # 音符轮廓
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pen = ImageDraw.Draw(layer)
    unit = size / 6
    cx, cy = size / 2, size / 2
    pen.ellipse((cx - unit * 1.15, cy + unit * 0.45, cx - unit * 0.25, cy + unit * 1.1),
                fill=(255, 255, 255, 120))
    pen.rounded_rectangle((cx - unit * 0.42, cy - unit * 1.0, cx - unit * 0.22, cy + unit * 0.95),
                          radius=unit * 0.1, fill=(255, 255, 255, 120))
    pen.rounded_rectangle((cx + unit * 0.55, cy - unit * 1.15, cx + unit * 0.78, cy + unit * 0.6),
                          radius=unit * 0.1, fill=(255, 255, 255, 120))
    pen.rounded_rectangle((cx - unit * 0.42, cy - unit * 1.0, cx + unit * 0.78, cy - unit * 0.72),
                          radius=unit * 0.12, fill=(255, 255, 255, 120))
    image = Image.alpha_composite(image, layer)

    mask = rounded_mask((size, size), 14)
    image.putalpha(mask)
    return image


def _hsv(h: float, s: float, v: float):
    import colorsys

    r, g, b = colorsys.hsv_to_rgb(h / 360.0, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


@lru_cache(maxsize=8)
def mesh_backdrop(width: int, height: int, mode: str) -> Image.Image:
    """没有封面时的背景：柔和色块。"""
    palette = theme(mode)
    image = Image.new("RGB", (width, height), palette["window"])
    blobs = (
        ((20, 120, 255), 0.30, 0.05),
        ((150, 120, 255), 0.72, 0.18),
        ((255, 150, 190), 0.12, 0.72),
        ((120, 230, 220), 0.85, 0.85),
    ) if mode != "dark" else (
        ((30, 80, 200), 0.5, 0.15),
        ((200, 90, 60), 0.2, 0.6),
        ((60, 180, 220), 0.15, 0.85),
    )
    layer = Image.new("RGB", (width, height), palette["window"])
    draw = ImageDraw.Draw(layer)
    for color, fx, fy in blobs:
        radius = int(max(width, height) * 0.55)
        cx, cy = int(width * fx), int(height * fy)
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=color)
    layer = layer.filter(ImageFilter.GaussianBlur(int(max(width, height) * 0.12)))
    image = Image.blend(image, layer, 0.55)
    return image


def compose_backdrop(cover: Image.Image | None, width: int, height: int,
                     mode: str) -> Image.Image:
    """背景：封面铺满并重度模糊，或退回色块。"""
    if cover is None:
        base = mesh_backdrop(max(8, width // 4), max(8, height // 4), mode)
        base = base.resize((width, height), Image.LANCZOS)
    else:
        ratio = max(width / cover.width, height / cover.height)
        base = cover.convert("RGB").resize(
            (max(1, int(cover.width * ratio)), max(1, int(cover.height * ratio))),
            Image.LANCZOS,
        )
        left = (base.width - width) // 2
        top = (base.height - height) // 2
        base = base.crop((left, top, left + width, top + height))
        base = base.filter(ImageFilter.GaussianBlur(max(18, min(width, height) // 12)))

    palette = theme(mode)
    veil = Image.new("RGB", (width, height), palette["window"])
    base = Image.blend(base, veil, 0.58 if mode != "dark" else 0.72)
    return base.convert("RGBA")


def glass_panel(backdrop: Image.Image, box: tuple[int, int, int, int],
                radius: int, mode: str, strong: bool = False) -> Image.Image:
    """从背景里裁一块，模糊后叠白色做成毛玻璃。"""
    left, top, right, bottom = box
    width, height = max(1, right - left), max(1, bottom - top)
    palette = theme(mode)

    crop = backdrop.crop((max(0, left), max(0, top),
                          max(1, min(backdrop.width, right)),
                          max(1, min(backdrop.height, bottom))))
    if crop.size != (width, height):
        crop = crop.resize((width, height), Image.LANCZOS)
    crop = crop.filter(ImageFilter.GaussianBlur(12))

    tint = palette["tint_strong"] if strong else palette["tint"]
    overlay = Image.new("RGBA", (width, height), tint)
    panel = Image.alpha_composite(crop.convert("RGBA"), overlay)

    mask = rounded_mask((width, height), radius)
    panel.putalpha(mask)

    # 1px 高光边：用同样抗锯齿的圆角轮廓叠上去，避免硬线
    border = rounded_image((width, height), radius,
                           fill=(0, 0, 0, 0), outline=palette["border"], width=1)
    panel = Image.alpha_composite(panel, border)
    return panel


def sample_color(image: Image.Image, box: tuple[int, int, int, int]) -> str:
    """取区域平均色，用于给无法透明的小控件上底色。

    坐标会先夹到图片范围内：控件还没排好版时可能算出越界或反了的矩形，
    以前会直接抛 ValueError 把界面搞崩。
    """
    width, height = image.size
    left, top, right, bottom = (int(v) for v in box)
    left = max(0, min(left, width - 1))
    top = max(0, min(top, height - 1))
    right = max(left + 1, min(right, width))
    bottom = max(top + 1, min(bottom, height))
    region = image.crop((left, top, right, bottom)).convert("RGB")
    small = region.resize((1, 1), Image.LANCZOS)
    return "#%02x%02x%02x" % small.getpixel((0, 0))


def lerp_image(first: Image.Image, second: Image.Image, ratio: float) -> Image.Image:
    return Image.blend(first, second, max(0.0, min(1.0, ratio)))


def row_highlight(size, radius, rgba) -> Image.Image:
    return rounded_image(size, radius, rgba)

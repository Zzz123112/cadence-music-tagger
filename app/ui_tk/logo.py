"""Cadence 的 LOGO。

设计：蓝紫渐变的圆角方块 + 白色八分音符，
音符用粗笔画保证 16px 也认得出来。
所有尺寸都先 4 倍绘制再缩小，边缘才平滑。
"""
from __future__ import annotations

import os
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter

ASSET_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
PNG_PATH = os.path.join(ASSET_DIR, "logo.png")
ICO_PATH = os.path.join(ASSET_DIR, "logo.ico")

TOP = (86, 148, 255)      # 蓝
BOTTOM = (139, 92, 246)   # 紫
NOTE = (255, 255, 255)


def _lerp(a, b, t):
    return tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def render(size: int = 512, scale: int = 4) -> Image.Image:
    """画一张 size×size 的 LOGO（RGBA，圆角外透明）。"""
    s = size * scale
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # 渐变底
    gradient = Image.new("RGB", (1, s))
    pixels = gradient.load()
    for y in range(s):
        pixels[0, y] = _lerp(TOP, BOTTOM, y / max(1, s - 1))
    gradient = gradient.resize((s, s), Image.BILINEAR)

    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, s - 1, s - 1), radius=int(s * 0.235), fill=255
    )
    image.paste(gradient, (0, 0), mask)

    # 左上柔光
    glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(
        (-s * 0.25, -s * 0.35, s * 0.75, s * 0.42), fill=(255, 255, 255, 90)
    )
    glow = glow.filter(ImageFilter.GaussianBlur(s * 0.06))
    image = Image.alpha_composite(image, Image.composite(glow, Image.new("RGBA", (s, s), (0, 0, 0, 0)), mask))

    # 音符
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    pen = ImageDraw.Draw(layer)
    color = NOTE + (245,)

    head_w, head_h = s * 0.29, s * 0.21
    head_cx, head_cy = s * 0.425, s * 0.715
    pen.ellipse(
        (head_cx - head_w / 2, head_cy - head_h / 2,
         head_cx + head_w / 2, head_cy + head_h / 2),
        fill=color,
    )

    stem_x = s * 0.535
    stem_w = s * 0.062
    pen.rounded_rectangle(
        (stem_x, s * 0.275, stem_x + stem_w, s * 0.715),
        radius=stem_w / 2, fill=color,
    )

    # 符尾：沿一条弧线叠一串递减的圆，得到自然的收尾
    import math

    top_y = s * 0.275
    steps = 26
    for i in range(steps):
        t = i / (steps - 1)
        cx = stem_x + stem_w * 0.55 + s * 0.205 * math.sin(t * math.pi * 0.62)
        cy = top_y + s * 0.175 * (t ** 1.15)
        r = s * 0.055 * (1 - 0.82 * t)
        pen.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)

    image = Image.alpha_composite(image, layer)
    return image.resize((size, size), Image.LANCZOS)


@lru_cache(maxsize=16)
def window_logo(size: int = 28) -> Image.Image | None:
    """标题栏用的小图标（带圆角、透明底）。"""
    try:
        return render(size, scale=8)
    except Exception:
        return None


def _writable_dir() -> str:
    """优先用打包内的资源目录；只读时退到临时目录。"""
    for candidate in (ASSET_DIR, os.path.join(os.environ.get("TEMP", "."), "CadenceAssets")):
        try:
            os.makedirs(candidate, exist_ok=True)
            probe = os.path.join(candidate, "_w.tmp")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("x")
            os.remove(probe)
            return candidate
        except OSError:
            continue
    return ASSET_DIR


def ensure_files() -> tuple[str, str]:
    """生成 logo.png 与多尺寸 logo.ico，返回路径。"""
    folder = _writable_dir()
    png = os.path.join(folder, "logo.png")
    ico = os.path.join(folder, "logo.ico")
    big = render(512)
    big.save(png)
    big.save(
        ico,
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)],
    )
    return png, ico


def apply_window_icon(root) -> None:
    """把 ICO 设成窗口图标（类图标 + 窗口图标都设，任务栏才稳）。"""
    try:
        png, ico = ensure_files()
        root.iconbitmap(default=ico)
    except Exception:
        return
    try:
        from . import win_frame

        win_frame.set_window_icon(root, ico)
    except Exception:
        pass

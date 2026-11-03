"""视觉基础：iOS 风格的配色、圆角、毛玻璃与模糊工具。

不依赖任何图片资源，所有质感都是画出来的。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
    QRadialGradient,
)

# ---------------------------------------------------------------- 配色

LIGHT = {
    "window": "#F2F2F7",
    "glass": (255, 255, 255, 150),
    "glass_strong": (255, 255, 255, 205),
    "stroke": (255, 255, 255, 210),
    "stroke_sub": (0, 0, 0, 18),
    "text": "#1C1C1E",
    "text2": "#6E6E73",
    "text3": "#A1A1A6",
    "accent": "#0A84FF",
    "accent_soft": (10, 132, 255, 30),
    "hover": (0, 0, 0, 10),
    "select": (10, 132, 255, 26),
    "danger": "#FF3B30",
    "ok": "#34C759",
    "shadow": (0, 0, 0, 40),
}

DARK = {
    "window": "#141416",
    "glass": (255, 255, 255, 22),
    "glass_strong": (255, 255, 255, 34),
    "stroke": (255, 255, 255, 34),
    "stroke_sub": (255, 255, 255, 16),
    "text": "#F5F5F7",
    "text2": "#A1A1A6",
    "text3": "#6E6E73",
    "accent": "#0A84FF",
    "accent_soft": (10, 132, 255, 46),
    "hover": (255, 255, 255, 16),
    "select": (10, 132, 255, 60),
    "danger": "#FF453A",
    "ok": "#32D74B",
    "shadow": (0, 0, 0, 120),
}

RADIUS_WINDOW = 18
RADIUS_CARD = 16
RADIUS_SMALL = 10

_FONT_CANDIDATES = (
    "Microsoft YaHei UI",
    "PingFang SC",
    "Segoe UI",
    "Arial",
)


def font(size: int, weight: int = QFont.Weight.Normal) -> QFont:
    """统一字体：优先系统中文字体，避免方框。"""
    families = set(QFontDatabase.families())
    family = next((f for f in _FONT_CANDIDATES if f in families), "Segoe UI")
    result = QFont(family, size)
    result.setWeight(weight)
    result.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return result


def color(value) -> QColor:
    if isinstance(value, QColor):
        return value
    if isinstance(value, str):
        return QColor(value)
    return QColor(*value)


# ---------------------------------------------------------------- 模糊与圆角

def blur_pixmap(source: QPixmap, strength: int = 28) -> QPixmap:
    """低成本高斯模糊：缩小 → 平滑放大。

    比 QGraphicsBlurEffect 快得多，做背景够了。
    """
    if source.isNull():
        return source
    w, h = source.width(), source.height()
    factor = max(4, strength // 3)
    small = source.scaled(
        max(1, w // factor), max(1, h // factor),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    return small.scaled(
        w, h,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def rounded_pixmap(source: QPixmap, radius: int) -> QPixmap:
    """把图片裁成圆角。"""
    if source.isNull():
        return source
    size = source.size()
    out = QPixmap(size)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size.width(), size.height()), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, source)
    painter.end()
    return out


def placeholder_cover(w: int, h: int, seed: str = "") -> QPixmap:
    """没有封面时画一个有质感的占位图（渐变 + 音符轮廓）。"""
    pix = QPixmap(w, h)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    hue = sum(ord(ch) for ch in seed) % 360 if seed else 210
    start = QColor.fromHsv(hue, 90, 220, 255)
    end = QColor.fromHsv((hue + 48) % 360, 120, 150, 255)
    gradient = QLinearGradient(0, 0, w, h)
    gradient.setColorAt(0.0, start)
    gradient.setColorAt(1.0, end)

    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), RADIUS_CARD, RADIUS_CARD)
    painter.setClipPath(path)
    painter.fillPath(path, gradient)

    glow = QRadialGradient(QPointF(w * 0.25, h * 0.2), w * 0.9)
    glow.setColorAt(0.0, QColor(255, 255, 255, 90))
    glow.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.fillPath(path, glow)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 255, 255, 120))
    unit = min(w, h) / 6
    cx, cy = w / 2, h / 2
    painter.drawEllipse(QPointF(cx - unit * 0.7, cy + unit * 0.8), unit * 0.55, unit * 0.42)
    painter.drawRoundedRect(
        QRectF(cx - unit * 0.25, cy - unit * 0.9, unit * 0.22, unit * 1.75),
        unit * 0.1, unit * 0.1,
    )
    painter.drawRoundedRect(
        QRectF(cx + unit * 0.75, cy - unit * 1.1, unit * 0.2, unit * 1.6),
        unit * 0.1, unit * 0.1,
    )
    painter.drawRoundedRect(
        QRectF(cx - unit * 0.25, cy - unit * 0.95, unit * 1.2, unit * 0.28),
        unit * 0.12, unit * 0.12,
    )
    painter.end()
    return pix


def mesh_backdrop(w: int, h: int, theme: dict) -> QPixmap:
    """没有封面时的背景：柔和的色块渐变，模拟 iOS 的模糊壁纸。"""
    pix = QPixmap(w, h)
    pix.fill(color(theme["window"]))
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    if theme is DARK:
        blobs = ((40, 60, 200, 90), (250, 120, 60, 60), (90, 200, 255, 70))
    else:
        blobs = ((10, 132, 255, 110), (255, 120, 190, 80), (120, 220, 255, 90))

    for cx, cy, radius, alpha in blobs:
        gradient = QRadialGradient(QPointF(cx * w / 400, cy * h / 400), radius * max(w, h) / 700)
        gradient.setColorAt(0.0, QColor(cx % 255, cy % 255, radius % 255, alpha))
        gradient.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(0, 0, w, h, gradient)
    painter.end()
    return blur_pixmap(pix, 40)


def paint_glass(painter: QPainter, rect: QRectF, radius: int,
                fill: QColor, stroke: QColor, shadow: QColor | None = None) -> None:
    """画一块毛玻璃：圆角填充 + 1px 高光描边 + 可选投影。"""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)

    if shadow is not None and shadow.alpha() > 0:
        for offset, spread, alpha in ((6, 2, shadow.alpha()), (12, 6, shadow.alpha() // 2)):
            soft = QPainterPath()
            soft.addRoundedRect(
                rect.adjusted(-spread, -spread + offset, spread, spread + offset),
                radius + spread, radius + spread,
            )
            painter.fillPath(soft, QColor(shadow.red(), shadow.green(), shadow.blue(), alpha // 3))

    painter.fillPath(path, fill)
    painter.setPen(QColor(stroke))
    painter.drawPath(path)


def paint_gradient_border(painter: QPainter, rect: QRectF, radius: int) -> None:
    """顶部一道极淡的高光，模拟玻璃边缘反光。"""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    gradient.setColorAt(0.0, QColor(255, 255, 255, 150))
    gradient.setColorAt(0.35, QColor(255, 255, 255, 0))
    pen = painter.pen()
    pen.setBrush(gradient)
    pen.setWidthF(1.0)
    painter.setPen(pen)
    painter.drawPath(path.adjusted(0.5, 0.5, -0.5, -0.5))

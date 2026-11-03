"""iOS 风格的基础控件：毛玻璃面板、胶囊按钮、歌曲行、候选行、封面。"""
from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QWidget

from . import theme as T


class GlassPanel(QWidget):
    """半透明毛玻璃容器，子控件正常叠在上面。"""

    def __init__(self, parent=None, radius: int = T.RADIUS_CARD,
                 strong: bool = False, shadow: bool = True):
        super().__init__(parent)
        self.radius = radius
        self.strong = strong
        self.shadow = shadow
        self._theme = T.LIGHT
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def use_theme(self, palette: dict) -> None:
        self._theme = palette
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        palette = self._theme
        fill = T.color(palette["glass_strong"] if self.strong else palette["glass"])
        stroke = T.color(palette["stroke"] if self.strong else palette["stroke_sub"])
        shadow = T.color(palette["shadow"]) if self.shadow else None
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        T.paint_glass(painter, rect, self.radius, fill, stroke, shadow)
        painter.end()


class PillButton(QWidget):
    """胶囊按钮：悬停变亮，按下轻微回弹。"""

    clicked = Signal()

    def __init__(self, text: str, parent=None, accent: bool = False, small: bool = False):
        super().__init__(parent)
        self._text = text
        self._accent = accent
        self._small = small
        self._theme = T.LIGHT
        self._hover = 0.0
        self._press = 0.0
        self._enabled = True
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setMinimumHeight(30 if small else 36)

        self._hover_anim = QPropertyAnimation(self, b"hover", self)
        self._hover_anim.setDuration(140)
        self._hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def use_theme(self, palette: dict) -> None:
        self._theme = palette
        self.update()

    def sizeHint(self):  # noqa: N802
        metrics = self.fontMetrics()
        width = metrics.horizontalAdvance(self._text) + (40 if self._small else 56)
        return QSize(width, 30 if self._small else 36)

    def setText(self, text: str) -> None:  # noqa: N802
        self._text = text
        self.updateGeometry()
        self.update()

    def text(self) -> str:
        return self._text

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802
        self._enabled = bool(enabled)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor
        )
        self.update()

    def get_hover(self) -> float:
        return self._hover

    def set_hover(self, value: float) -> None:
        self._hover = value
        self.update()

    hover = Property(float, get_hover, set_hover)

    def enterEvent(self, event):  # noqa: N802
        self._animate(1.0)

    def leaveEvent(self, event):  # noqa: N802
        self._animate(0.0)

    def mousePressEvent(self, event):  # noqa: N802
        if self._enabled and event.button() == Qt.MouseButton.LeftButton:
            self._press = 1.0
            self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802
        was_pressed = self._press > 0
        self._press = 0.0
        self.update()
        if was_pressed and self._enabled and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()

    def _animate(self, target: float) -> None:
        if not self._enabled:
            return
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover)
        self._hover_anim.setEndValue(target)
        self._hover_anim.start()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self._theme
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = rect.height() / 2

        if self._accent:
            base = T.color(palette["accent"])
            base.setAlphaF(min(1.0, 0.82 + 0.18 * self._hover) * (0.45 if not self._enabled else 1.0))
        else:
            base = T.color(palette["glass_strong"])
            base.setAlphaF(min(1.0, (base.alphaF() / 255 + 0.10 * self._hover)))

        if self._press:
            rect = rect.adjusted(1, 1, -1, -1)

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.fillPath(path, base)
        painter.setPen(T.color(palette["stroke_sub"]))
        painter.drawPath(path)

        text_color = QColor("#FFFFFF") if self._accent else T.color(palette["text"])
        if not self._enabled:
            text_color.setAlpha(120)
        painter.setPen(text_color)
        painter.setFont(T.font(10, QFont.Weight.DemiBold if self._accent else QFont.Weight.Medium))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)
        painter.end()


class CoverView(QWidget):
    """圆角封面，带柔和阴影；换图时做淡入。"""

    def __init__(self, parent=None, radius: int = 14):
        super().__init__(parent)
        self.radius = radius
        self._pixmap: QPixmap | None = None
        self._next: QPixmap | None = None
        self._fade = 1.0
        self._theme = T.LIGHT
        self._anim = QPropertyAnimation(self, b"fade", self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def use_theme(self, palette: dict) -> None:
        self._theme = palette
        self.update()

    def set_cover(self, pixmap: QPixmap | None, animate: bool = True) -> None:
        target = pixmap
        if target is not None:
            size = self.size()
            if size.width() > 4 and size.height() > 4:
                target = target.scaled(
                    size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
        if animate and self._pixmap is not None:
            self._next = target
            self._anim.stop()
            self._anim.setStartValue(0.0)
            self._anim.setEndValue(1.0)
            self._anim.start()
        else:
            self._pixmap = target
            self._next = None
            self.update()

    def get_fade(self) -> float:
        return self._fade

    def set_fade(self, value: float) -> None:
        self._fade = value
        if value >= 1.0 and self._next is not None:
            self._pixmap = self._next
            self._next = None
        self.update()

    fade = Property(float, get_fade, set_fade)

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        radius = self.radius

        shadow = T.color(self._theme["shadow"])
        T.paint_glass(painter, rect, radius, QColor(0, 0, 0, 0),
                      QColor(0, 0, 0, 0), shadow)

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.setClipPath(path)

        if self._pixmap is None and self._next is None:
            painter.fillPath(path, QColor(255, 255, 255, 18))
        if self._pixmap is not None:
            painter.setOpacity(1.0 if self._next is None else 1.0 - self._fade)
            painter.drawPixmap(self.rect(), self._pixmap)
        if self._next is not None:
            painter.setOpacity(self._fade)
            painter.drawPixmap(self.rect(), self._next)
        painter.setOpacity(1.0)
        painter.end()


class SongRow(QWidget):
    """歌曲列表里的一行：封面缩略图 + 名称 + 副标题 + 状态点。"""

    clicked = Signal(int)

    def __init__(self, index: int, title: str, subtitle: str, parent=None):
        super().__init__(parent)
        self.index = index
        self._title = title
        self._subtitle = subtitle
        self._selected = False
        self._hover = 0.0
        self._thumb: QPixmap | None = None
        self._theme = T.LIGHT
        self._status = ""
        self.setFixedHeight(62)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        self._anim = QPropertyAnimation(self, b"hover", self)
        self._anim.setDuration(130)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def use_theme(self, palette: dict) -> None:
        self._theme = palette
        self.update()

    def set_selected(self, value: bool) -> None:
        self._selected = bool(value)
        self.update()

    def set_thumb(self, pixmap: QPixmap | None) -> None:
        self._thumb = pixmap
        self.update()

    def set_subtitle(self, text: str) -> None:
        self._subtitle = text
        self.update()

    def get_hover(self) -> float:
        return self._hover

    def set_hover(self, value: float) -> None:
        self._hover = value
        self.update()

    hover = Property(float, get_hover, set_hover)

    def enterEvent(self, event):  # noqa: N802
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def leaveEvent(self, event):  # noqa: N802
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(0.0)
        self._anim.start()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self._theme
        rect = QRectF(self.rect()).adjusted(6, 2, -6, -2)
        path = QPainterPath()
        path.addRoundedRect(rect, 12, 12)

        if self._selected:
            painter.fillPath(path, T.color(palette["select"]))
        elif self._hover > 0.01:
            tint = T.color(palette["hover"])
            tint.setAlphaF(tint.alphaF() / 255 * self._hover)
            painter.fillPath(path, tint)

        # 封面缩略图
        thumb_size = 40
        thumb_rect = QRectF(rect.left() + 12, rect.center().y() - thumb_size / 2,
                            thumb_size, thumb_size)
        thumb_path = QPainterPath()
        thumb_path.addRoundedRect(thumb_rect, 8, 8)
        painter.setClipPath(thumb_path)
        if self._thumb is not None:
            painter.drawPixmap(thumb_rect, self._thumb, QRectF(self._thumb.rect()))
        else:
            painter.fillPath(thumb_path, T.color(palette["hover"]))
        painter.setClipping(False)

        text_left = thumb_rect.right() + 12
        available = rect.right() - text_left - 12

        painter.setPen(T.color(palette["text"]))
        painter.setFont(T.font(10, QFont.Weight.Medium))
        metrics = painter.fontMetrics()
        painter.drawText(
            QRectF(text_left, rect.top() + 11, available, 20),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            metrics.elidedText(self._title, Qt.TextElideMode.ElideRight, int(available)),
        )

        painter.setPen(T.color(palette["text2"]))
        painter.setFont(T.font(9))
        metrics2 = painter.fontMetrics()
        painter.drawText(
            QRectF(text_left, rect.top() + 30, available, 18),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            metrics2.elidedText(self._subtitle, Qt.TextElideMode.ElideRight, int(available)),
        )
        painter.end()


class CandidateRow(QWidget):
    """候选结果卡片：命中数徽章 + 歌名/歌手 + 来源。"""

    clicked = Signal(int)

    def __init__(self, index: int, hits: int, title: str, artist: str,
                 source: str, duration: str, parent=None):
        super().__init__(parent)
        self.index = index
        self._hits = hits
        self._title = title
        self._artist = artist
        self._source = source
        self._duration = duration
        self._selected = False
        self._hover = 0.0
        self._theme = T.LIGHT
        self.setFixedHeight(56)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        self._anim = QPropertyAnimation(self, b"hover", self)
        self._anim.setDuration(130)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def use_theme(self, palette: dict) -> None:
        self._theme = palette
        self.update()

    def set_selected(self, value: bool) -> None:
        self._selected = bool(value)
        self.update()

    def get_hover(self) -> float:
        return self._hover

    def set_hover(self, value: float) -> None:
        self._hover = value
        self.update()

    hover = Property(float, get_hover, set_hover)

    def enterEvent(self, event):  # noqa: N802
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def leaveEvent(self, event):  # noqa: N802
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(0.0)
        self._anim.start()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self._theme
        rect = QRectF(self.rect()).adjusted(4, 2, -4, -2)
        path = QPainterPath()
        path.addRoundedRect(rect, 12, 12)

        if self._selected:
            painter.fillPath(path, T.color(palette["select"]))
        elif self._hover > 0.01:
            tint = T.color(palette["hover"])
            tint.setAlphaF(tint.alphaF() / 255 * self._hover)
            painter.fillPath(path, tint)
        painter.setPen(T.color(palette["stroke_sub"]))
        painter.drawPath(path)

        # 命中徽章
        badge = QRectF(rect.left() + 10, rect.center().y() - 12, 38, 24)
        badge_path = QPainterPath()
        badge_path.addRoundedRect(badge, 8, 8)
        if self._hits >= 5:
            badge_color = T.color(palette["ok"])
        elif self._hits >= 3:
            badge_color = T.color(palette["accent"])
        else:
            badge_color = T.color(palette["text3"])
        badge_color.setAlpha(46)
        painter.fillPath(badge_path, badge_color)
        painter.setPen(T.color(palette["text"]))
        painter.setFont(T.font(9, QFont.Weight.DemiBold))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, f"{self._hits}/6")

        text_left = badge.right() + 12
        available = rect.right() - text_left - 90
        painter.setFont(T.font(10, QFont.Weight.Medium))
        metrics = painter.fontMetrics()
        painter.drawText(
            QRectF(text_left, rect.top() + 8, available, 18),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            metrics.elidedText(self._title, Qt.TextElideMode.ElideRight, int(available)),
        )
        painter.setPen(T.color(palette["text2"]))
        painter.setFont(T.font(9))
        metrics2 = painter.fontMetrics()
        sub = " · ".join(part for part in (self._artist, self._duration) if part)
        painter.drawText(
            QRectF(text_left, rect.top() + 28, available, 18),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            metrics2.elidedText(sub, Qt.TextElideMode.ElideRight, int(available)),
        )

        painter.setPen(T.color(palette["text3"]))
        painter.setFont(T.font(9))
        painter.drawText(
            QRectF(rect.right() - 84, rect.top(), 74, rect.height()),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            self._source,
        )
        painter.end()

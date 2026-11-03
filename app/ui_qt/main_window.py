"""Cadence 主窗口：iOS 风格的单窗口界面。"""
from __future__ import annotations

import os

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizeGrip,
    QVBoxLayout,
    QWidget,
)

from ..core import db, matcher, parse_name, probe, scan
from ..core.context import album_context
from ..core.models import Candidate, Track
from . import theme as T
from .widgets import CandidateRow, CoverView, GlassPanel, PillButton, SongRow
from .worker import run_async


class Backdrop(QWidget):
    """窗口背景：当前封面模糊版，或柔和色块。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._theme = T.LIGHT
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_theme(self, palette: dict) -> None:
        self._theme = palette
        self._pixmap = None
        self.update()

    def set_cover(self, pixmap: QPixmap | None) -> None:
        if pixmap is None or pixmap.isNull():
            self._pixmap = None
        else:
            scaled = pixmap.scaled(
                self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._pixmap = T.blur_pixmap(scaled, 42)
        self.update()

    def resizeEvent(self, event):  # noqa: N802
        if self._pixmap is not None:
            self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())

        if self._pixmap is not None:
            painter.drawPixmap(self.rect(), self._pixmap)
            veil = QColor("#F2F2F7" if self._theme is T.LIGHT else "#141416")
            veil.setAlpha(205 if self._theme is T.LIGHT else 215)
            path = QPainterPath()
            path.addRoundedRect(rect, T.RADIUS_WINDOW, T.RADIUS_WINDOW)
            painter.fillPath(path, veil)
        else:
            gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
            if self._theme is T.LIGHT:
                gradient.setColorAt(0.0, QColor("#EAF2FF"))
                gradient.setColorAt(0.5, QColor("#F7F2FF"))
                gradient.setColorAt(1.0, QColor("#EFFBF6"))
            else:
                gradient.setColorAt(0.0, QColor("#101426"))
                gradient.setColorAt(0.5, QColor("#16121F"))
                gradient.setColorAt(1.0, QColor("#0F1A1C"))
            path = QPainterPath()
            path.addRoundedRect(rect, T.RADIUS_WINDOW, T.RADIUS_WINDOW)
            painter.fillPath(path, gradient)
        painter.end()


class TitleBar(QWidget):
    """顶部标题区：应用名 + 操作按钮 + 窗口按钮，可拖动窗口。"""

    def __init__(self, window: "CadenceWindow"):
        super().__init__(window)
        self._window = window
        self._drag_offset: QPoint | None = None
        self.setFixedHeight(64)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(22, 0, 14, 0)
        layout.setSpacing(8)

        self.brand = QLabel("Cadence")
        layout.addWidget(self.brand)
        self.tagline = QLabel("音乐标签")
        layout.addWidget(self.tagline)
        layout.addStretch(1)

        self.scan_btn = PillButton("扫描", small=True)
        self.batch_btn = PillButton("批量", small=True)
        self.settings_btn = PillButton("设置", small=True)
        for btn in (self.scan_btn, self.batch_btn, self.settings_btn):
            layout.addWidget(btn)
        layout.addSpacing(12)

        self.min_btn = PillButton("—", small=True)
        self.close_btn = PillButton("✕", small=True)
        for btn in (self.min_btn, self.close_btn):
            btn.setFixedWidth(34)
            layout.addWidget(btn)

    def apply_theme(self, palette: dict) -> None:
        self.brand.setStyleSheet(
            f"color: {palette['text']}; font-size: 17px; font-weight: 700;"
            f" letter-spacing: 0.5px; background: transparent;"
        )
        self.tagline.setStyleSheet(
            f"color: {palette['text3']}; font-size: 11px; background: transparent;"
        )

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self._window.pos()

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._window.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event):  # noqa: N802
        self._drag_offset = None


class CadenceWindow(QWidget):
    """主窗口。"""

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.palette_theme = T.DARK if ctx.settings.get("theme") == "dark" else T.LIGHT
        self.tracks: list[Track] = []
        self.current: Track | None = None
        self.candidates: list[Candidate] = []
        self._rows: list[SongRow] = []
        self._cand_rows: list[CandidateRow] = []
        self._match_job = None
        self._jobs: list = []

        self.setWindowTitle("Cadence")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(QSize(1040, 680))
        self.resize(1180, 760)

        self._build()
        self.apply_theme()
        self.refresh_list()
        QTimer.singleShot(120, self._fade_in)

    # ------------------------------------------------------------ 布局

    def _build(self) -> None:
        self.backdrop = Backdrop(self)
        self.backdrop.lower()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.title_bar = TitleBar(self)
        outer.addWidget(self.title_bar)
        self.title_bar.scan_btn.clicked.connect(self.scan_library)
        self.title_bar.batch_btn.clicked.connect(self.open_batch)
        self.title_bar.settings_btn.clicked.connect(self.open_settings)
        self.title_bar.min_btn.clicked.connect(self.showMinimized)
        self.title_bar.close_btn.clicked.connect(self.close)

        body = QWidget(self)
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(18, 0, 18, 12)
        body_layout.setSpacing(14)
        outer.addWidget(body, 1)

        # 左侧：歌曲列表
        left = GlassPanel(body, radius=T.RADIUS_CARD)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(10, 14, 10, 10)
        left_layout.setSpacing(8)

        header = QHBoxLayout()
        self.list_title = QLabel("音乐库")
        header.addWidget(self.list_title)
        header.addStretch(1)
        self.count_label = QLabel("0 首")
        header.addWidget(self.count_label)
        left_layout.addLayout(header)

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索文件名")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_rows)
        left_layout.addWidget(self.search)

        self.list_scroll = QScrollArea()
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(0)
        self.list_layout.addStretch(1)
        self.list_scroll.setWidget(self.list_host)
        left_layout.addWidget(self.list_scroll, 1)
        body_layout.addWidget(left, 0)

        self.left_panel = left
        left.setFixedWidth(330)

        # 右侧：详情
        right = GlassPanel(body, radius=T.RADIUS_CARD, strong=True)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(24, 22, 24, 18)
        right_layout.setSpacing(14)

        top = QHBoxLayout()
        top.setSpacing(18)
        self.cover = CoverView(right, radius=16)
        self.cover.setFixedSize(132, 132)
        top.addWidget(self.cover)

        info = QVBoxLayout()
        info.setSpacing(6)
        self.song_title = QLabel("选择一首歌")
        self.song_title.setWordWrap(True)
        info.addWidget(self.song_title)
        self.song_meta = QLabel("")
        info.addWidget(self.song_meta)
        info.addStretch(1)
        self.save_btn = PillButton("保存", accent=True)
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save_current)
        info.addWidget(self.save_btn, 0, Qt.AlignmentFlag.AlignLeft)
        top.addLayout(info, 1)
        right_layout.addLayout(top)

        self.field_title = self._field(right_layout, "歌名")
        row = QHBoxLayout()
        row.setSpacing(12)
        self.field_artist = self._field(row, "歌手")
        right_layout.addLayout(row)
        row2 = QHBoxLayout()
        row2.setSpacing(12)
        self.field_album = self._field(row2, "专辑")
        right_layout.addLayout(row2)
        row3 = QHBoxLayout()
        row3.setSpacing(12)
        self.field_year = self._field(row3, "年份", 1)
        self.field_track = self._field(row3, "音轨", 1)
        right_layout.addLayout(row3)

        cand_header = QHBoxLayout()
        self.cand_title = QLabel("匹配结果")
        cand_header.addWidget(self.cand_title)
        cand_header.addStretch(1)
        self.rematch_btn = PillButton("重新搜索", small=True)
        self.rematch_btn.clicked.connect(lambda: self.start_match(force=True))
        cand_header.addWidget(self.rematch_btn)
        right_layout.addLayout(cand_header)

        self.cand_scroll = QScrollArea()
        self.cand_scroll.setWidgetResizable(True)
        self.cand_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cand_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.cand_host = QWidget()
        self.cand_layout = QVBoxLayout(self.cand_host)
        self.cand_layout.setContentsMargins(0, 0, 0, 0)
        self.cand_layout.setSpacing(0)
        self.cand_layout.addStretch(1)
        self.cand_scroll.setWidget(self.cand_host)
        right_layout.addWidget(self.cand_scroll, 1)

        self.status = QLabel("选择左侧歌曲开始")
        right_layout.addWidget(self.status)

        self.detail_panel = right
        self.detail_effect = QGraphicsOpacityEffect(right)
        self.detail_effect.setOpacity(1.0)
        right.setGraphicsEffect(self.detail_effect)
        body_layout.addWidget(right, 1)

        grip = QSizeGrip(self)
        grip.setFixedSize(16, 16)

    def _field(self, layout, label: str, span: int = 2) -> QLineEdit:
        box = QVBoxLayout()
        box.setSpacing(3)
        cap = QLabel(label)
        cap.setObjectName("fieldCaption")
        box.addWidget(cap)
        edit = QLineEdit()
        edit.setObjectName("fieldInput")
        box.addWidget(edit)
        if span == 2:
            layout.addLayout(box)
        else:
            layout.addLayout(box)
        edit._caption = cap  # type: ignore[attr-defined]
        return edit

    # ------------------------------------------------------------ 主题

    def apply_theme(self) -> None:
        palette = self.palette_theme
        self.backdrop.set_theme(palette)
        self.title_bar.apply_theme(palette)
        for widget in (self.left_panel, self.detail_panel):
            widget.use_theme(palette)
        for btn in self.findChildren(PillButton):
            btn.use_theme(palette)
        self.cover.use_theme(palette)
        for row in self._rows + self._cand_rows:
            row.use_theme(palette)

        self.list_title.setStyleSheet(
            f"color:{palette['text']}; font-size:13px; font-weight:600; background:transparent;")
        self.count_label.setStyleSheet(
            f"color:{palette['text3']}; font-size:11px; background:transparent;")
        self.song_title.setStyleSheet(
            f"color:{palette['text']}; font-size:20px; font-weight:650; background:transparent;")
        self.song_meta.setStyleSheet(
            f"color:{palette['text2']}; font-size:11px; background:transparent;")
        self.cand_title.setStyleSheet(
            f"color:{palette['text2']}; font-size:11px; font-weight:600;"
            f" letter-spacing:0.6px; background:transparent;")
        self.status.setStyleSheet(
            f"color:{palette['text3']}; font-size:11px; background:transparent;")

        for edit in (self.field_title, self.field_artist, self.field_album,
                     self.field_year, self.field_track):
            edit._caption.setStyleSheet(  # type: ignore[attr-defined]
                f"color:{palette['text3']}; font-size:10px; background:transparent;")
            edit.setStyleSheet(
                "QLineEdit {"
                f" background: rgba(255,255,255,{'0.55' if palette is T.LIGHT else '0.08'});"
                f" color: {palette['text']};"
                " border: 1px solid rgba(0,0,0,0.06); border-radius: 9px;"
                " padding: 6px 10px; font-size: 12px; }"
                "QLineEdit:focus {"
                f" border: 1px solid {palette['accent']};"
                f" background: rgba(255,255,255,{'0.8' if palette is T.LIGHT else '0.14'}); }}"
            )
        self.search.setStyleSheet(
            "QLineEdit {"
            f" background: rgba(255,255,255,{'0.55' if palette is T.LIGHT else '0.08'});"
            f" color: {palette['text']};"
            " border: 1px solid rgba(0,0,0,0.05); border-radius: 9px;"
            " padding: 6px 10px; font-size: 12px; }"
        )
        for area in (self.list_scroll, self.cand_scroll):
            area.setStyleSheet("QScrollArea { background: transparent; border: none; }")
            area.viewport().setStyleSheet("background: transparent;")
        self.update()

    # ------------------------------------------------------------ 数据

    def refresh_list(self) -> None:
        for row in self._rows:
            row.deleteLater()
        self._rows.clear()
        self.tracks = db.list_tracks(self.ctx.conn, mode="all")
        self.count_label.setText(f"{len(self.tracks)} 首")

        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for index, track in enumerate(self.tracks):
            row = SongRow(index, track.name, self._subtitle(track))
            row.use_theme(self.palette_theme)
            row.clicked.connect(self._on_row_clicked)
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)
            self._rows.append(row)
        self._filter_rows(self.search.text())
        if self.tracks and self.current is None:
            self._on_row_clicked(0)

    def _subtitle(self, track: Track) -> str:
        parts = [track.fmt or "?", track.quality_label()]
        if track.duration:
            parts.append(self._mmss(track.duration))
        return " · ".join(part for part in parts if part)

    def _status_text(self, track: Track) -> str:
        return {
            "all": "完整",
            "no_lyrics": "缺歌词",
            "no_cover": "缺封面",
            "partial": "字段不全",
            "ignored": "已忽略",
        }.get(track.status, track.status or "")

    def _mmss(self, seconds: float) -> str:
        total = int(seconds or 0)
        return f"{total // 60}:{total % 60:02d}"

    def _filter_rows(self, text: str) -> None:
        needle = (text or "").strip().lower()
        for row in self._rows:
            visible = not needle or needle in row._title.lower()
            row.setVisible(visible)

    def _on_row_clicked(self, index: int) -> None:
        if index < 0 or index >= len(self.tracks):
            return
        for i, row in enumerate(self._rows):
            row.set_selected(i == index)
        track = self.tracks[index]
        self.current = track
        self.ctx.set_current(track)
        self._fill_detail(track)
        self.start_match()

    def _fill_detail(self, track: Track) -> None:
        parsed = parse_name(track.name)
        self.song_title.setText(track.name)
        meta = " · ".join(part for part in (
            track.fmt, track.quality_label(), self._mmss(track.duration),
            self._status_text(track),
        ) if part)
        self.song_meta.setText(meta)
        self.field_title.setText(track.tag_title or parsed.title)
        self.field_artist.setText(track.tag_artist or parsed.artist)
        self.field_album.setText(track.tag_album)
        self.field_year.setText(track.tag_year)
        self.field_track.setText(track.tag_track or parsed.track)
        self.save_btn.setEnabled(True)

        pix = self._cover_for(track)
        self.cover.set_cover(pix)
        self.backdrop.set_cover(pix)
        self._animate_detail()

    def _cover_for(self, track: Track) -> QPixmap:
        from ..core import covercache

        data = covercache.get(self.ctx.data_dir, self._cache_candidate(track))
        if data:
            pix = QPixmap()
            if pix.loadFromData(data):
                return pix
        return T.placeholder_cover(132, 132, track.name)

    def _cache_candidate(self, track: Track) -> Candidate:
        candidates = db.load_candidates(self.ctx.conn, track.id or 0)
        if candidates:
            return candidates[0]
        return Candidate(source="", title=track.name)

    def _animate_detail(self) -> None:
        self.detail_effect.setOpacity(0.35)
        anim = QPropertyAnimation(self.detail_effect, b"opacity", self)
        anim.setDuration(220)
        anim.setStartValue(0.35)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        self._jobs.append(anim)

    def _fade_in(self) -> None:
        self.setWindowOpacity(0.0)
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(260)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        self._jobs.append(anim)

    # ------------------------------------------------------------ 匹配

    def start_match(self, force: bool = False) -> None:
        track = self.current
        if track is None:
            return
        if not self.ctx.providers:
            self.status.setText("没有可用的数据源，请到设置里开启")
            return
        token = getattr(self, "_match_token", 0) + 1
        self._match_token = token
        self.status.setText("正在搜索…")

        def work():
            candidates, _errors = matcher.query_track(track, self.ctx.providers)
            folder = db.folder_tracks(self.ctx.conn, os.path.dirname(track.path))
            evidence_ctx = album_context(track, folder)
            from ..core import evidence

            evidence.rank(track, candidates, evidence_ctx)
            return candidates

        self._match_job = run_async(
            self, work,
            lambda result: self._on_match_done(token, result),
            lambda message: self.status.setText(f"查询失败：{message.splitlines()[-1]}"),
        )

    def _on_match_done(self, token: int, candidates: list[Candidate]) -> None:
        if token != getattr(self, "_match_token", 0):
            return
        self.candidates = candidates
        if self.current and self.current.id:
            db.replace_candidates(self.ctx.conn, self.current.id, candidates)
            self.ctx.conn.commit()
        self._render_candidates()
        self.status.setText(f"{len(candidates)} 条候选")
        if candidates:
            self.cover.set_cover(self._cover_for_candidate(candidates[0]))
            self.backdrop.set_cover(self._cover_for_candidate(candidates[0]))

    def _cover_for_candidate(self, candidate: Candidate) -> QPixmap:
        from ..core import covercache

        data = covercache.get(self.ctx.data_dir, candidate)
        if data:
            pix = QPixmap()
            if pix.loadFromData(data):
                return pix
        return T.placeholder_cover(132, 132, candidate.title or "")

    def _render_candidates(self) -> None:
        for row in self._cand_rows:
            row.deleteLater()
        self._cand_rows.clear()
        while self.cand_layout.count() > 1:
            item = self.cand_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for index, cand in enumerate(self.candidates[:12]):
            hits = sum(1 for v in cand.evidence.values() if v >= 0.75)
            row = CandidateRow(
                index, hits, cand.title or "—", cand.artist or "",
                cand.source, self._mmss(cand.duration),
            )
            row.use_theme(self.palette_theme)
            row.clicked.connect(self._apply_candidate)
            self.cand_layout.insertWidget(self.cand_layout.count() - 1, row)
            self._cand_rows.append(row)

    def _apply_candidate(self, index: int) -> None:
        if index >= len(self.candidates):
            return
        for i, row in enumerate(self._cand_rows):
            row.set_selected(i == index)
        cand = self.candidates[index]
        self.field_title.setText(cand.title or "")
        self.field_artist.setText(cand.artist or "")
        self.field_album.setText(cand.album or "")
        self.field_year.setText(cand.year or "")
        self.field_track.setText(cand.track or "")
        self.status.setText(f"已套用 {cand.source} 的结果，确认后点保存")

    # ------------------------------------------------------------ 保存

    def save_current(self) -> None:
        track = self.current
        if track is None:
            return
        fields = {
            "title": self.field_title.text().strip(),
            "artist": self.field_artist.text().strip(),
            "album": self.field_album.text().strip(),
            "year": self.field_year.text().strip(),
            "track": self.field_track.text().strip(),
        }
        fields = {k: v for k, v in fields.items() if v}
        if not fields:
            self.status.setText("没有可写的字段")
            return
        self.status.setText("正在写入…")
        self.save_btn.setEnabled(False)

        def work():
            from ..write import service
            from ..write.service import PendingWrite

            return service.write_batch(
                self.ctx.conn, [PendingWrite(track=track, fields=fields)],
                os.path.dirname(track.path), fallback_dir=self.ctx.data_dir,
            )

        run_async(self, work, self._on_saved,
                  lambda message: (self.status.setText(f"保存失败：{message.splitlines()[-1]}"),
                                   self.save_btn.setEnabled(True)))

    def _on_saved(self, summary: dict) -> None:
        self.save_btn.setEnabled(True)
        if summary.get("ok"):
            note = "（经系统程序写入）" if summary.get("via_helper") else ""
            self.status.setText(f"已保存{note}")
            if self.current:
                refreshed = probe.probe(self.current.path)
                self.current.tag_title = refreshed.title
                self.current.status = "all"
                self.refresh_list()
        else:
            error = summary.get("error") or (
                summary["items"][0].error if summary.get("items") else "未知错误"
            )
            self.status.setText(f"保存失败：{str(error).splitlines()[0]}")

    # ------------------------------------------------------------ 动作

    def scan_library(self) -> None:
        root = self.ctx.library_root
        if not root or not os.path.isdir(root):
            self.status.setText("还没选择音乐文件夹")
            return
        self.status.setText("正在扫描…")

        def work():
            return scan.scan(self.ctx.conn, root)

        run_async(self, work, lambda _result: self._on_scanned(),
                  lambda message: self.status.setText(f"扫描失败：{message.splitlines()[-1]}"))

    def _on_scanned(self) -> None:
        self.current = None
        self.refresh_list()
        self.status.setText("扫描完成")

    def open_batch(self) -> None:
        from .dialogs import BatchDialog

        dialog = BatchDialog(self.ctx, self.tracks, self)
        dialog.exec()

    def open_settings(self) -> None:
        from .dialogs import SettingsDialog

        dialog = SettingsDialog(self.ctx, self)
        if dialog.exec():
            self.palette_theme = T.DARK if self.ctx.settings.get("theme") == "dark" else T.LIGHT
            self.apply_theme()

    # ------------------------------------------------------------ 绘制

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, T.RADIUS_WINDOW, T.RADIUS_WINDOW)
        painter.setClipPath(path)
        painter.fillPath(path, QColor(0, 0, 0, 0))
        painter.setPen(QColor(255, 255, 255, 40))
        painter.drawPath(path)
        painter.end()

    def resizeEvent(self, event):  # noqa: N802
        self.backdrop.setGeometry(self.rect())
        self.backdrop.lower()
        super().resizeEvent(event)

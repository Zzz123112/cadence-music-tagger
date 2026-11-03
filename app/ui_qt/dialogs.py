"""Cadence 的对话框：批量处理与设置。"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import autotag
from . import theme as T
from .worker import run_async

FIELDS = (
    ("title", "歌名"),
    ("artist", "歌手"),
    ("album", "专辑"),
    ("year", "年份"),
    ("track", "音轨"),
)


def _dialog_shell(dialog: QDialog, title: str, subtitle: str = "", width: int = 520):
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(width)
    dialog.setStyleSheet(f"QDialog {{ background: {T.LIGHT['window']}; }}")
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(24, 22, 24, 18)
    layout.setSpacing(12)

    heading = QLabel(title)
    heading.setStyleSheet(
        f"color:{T.LIGHT['text']}; font-size:17px; font-weight:650; background:transparent;")
    layout.addWidget(heading)
    if subtitle:
        note = QLabel(subtitle)
        note.setWordWrap(True)
        note.setStyleSheet(
            f"color:{T.LIGHT['text2']}; font-size:11px; background:transparent;")
        layout.addWidget(note)
    return layout


def _check(text: str, checked: bool = True) -> QCheckBox:
    box = QCheckBox(text)
    box.setChecked(checked)
    box.setStyleSheet(
        f"QCheckBox {{ color:{T.LIGHT['text']}; font-size:12px; spacing:8px; }}")
    return box


class BatchDialog(QDialog):
    """批量自动标记：选字段 → 跑 → 看结果。"""

    def __init__(self, ctx, tracks, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.tracks = list(tracks)
        self.result = None

        layout = _dialog_shell(
            self, "批量自动标记",
            f"将处理 {len(self.tracks)} 首。只写入勾选的内容，证据不足的会跳过。",
        )

        self.field_boxes: dict[str, QCheckBox] = {}
        row = QHBoxLayout()
        row.setSpacing(16)
        for key, label in FIELDS:
            box = _check(label, key in ("title", "artist", "album"))
            row.addWidget(box)
            self.field_boxes[key] = box
        row.addStretch(1)
        layout.addLayout(row)

        self.lyrics = QComboBox()
        self.lyrics.addItem("不处理歌词", autotag.LYRICS_NONE)
        self.lyrics.addItem("写进标签", autotag.LYRICS_TAG)
        self.lyrics.addItem("存成 .lrc 文件", autotag.LYRICS_FILE)
        self.lyrics.addItem("两者都做", autotag.LYRICS_BOTH)
        self.lyrics.setStyleSheet(
            f"QComboBox {{ color:{T.LIGHT['text']}; font-size:12px; padding:6px 10px;"
            f" border:1px solid rgba(0,0,0,0.08); border-radius:9px;"
            f" background:rgba(255,255,255,0.7); }}")
        layout.addWidget(self.lyrics)

        self.progress = QProgressBar()
        self.progress.setRange(0, max(1, len(self.tracks)))
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.setStyleSheet(
            "QProgressBar { background: rgba(0,0,0,0.06); border-radius:3px; }"
            f"QProgressBar::chunk {{ background:{T.LIGHT['accent']}; border-radius:3px; }}")
        layout.addWidget(self.progress)

        self.status = QLabel("准备就绪")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(
            f"color:{T.LIGHT['text2']}; font-size:11px; background:transparent;")
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel = QPushButton("关闭")
        self.cancel.clicked.connect(self.reject)
        buttons.addWidget(self.cancel)
        self.run_btn = QPushButton("开始")
        self.run_btn.setDefault(True)
        self.run_btn.clicked.connect(self.start)
        self.run_btn.setStyleSheet(
            f"QPushButton {{ background:{T.LIGHT['accent']}; color:white; border:none;"
            " border-radius:9px; padding:8px 20px; font-size:12px; font-weight:600; }}"
            "QPushButton:disabled { background: rgba(0,0,0,0.15); }")
        buttons.addWidget(self.run_btn)
        layout.addLayout(buttons)

    def start(self) -> None:
        fields = {key for key, box in self.field_boxes.items() if box.isChecked()}
        if not fields:
            self.status.setText("至少要勾一个字段")
            return
        mode = self.lyrics.currentData()
        tracks = list(self.tracks)
        self.run_btn.setEnabled(False)
        self.progress.setValue(0)

        def progress(done: int, total: int) -> None:
            self.progress.setValue(done)
            self.status.setText(f"处理中 {done}/{total}")

        def work():
            return autotag.run(
                self.ctx.conn, tracks, self.ctx.providers,
                library_root=self.ctx.library_root or "",
                progress=progress,
                fallback_dir=self.ctx.data_dir,
                fields=fields,
                lyrics_mode=mode,
            )

        run_async(self, work, self._done,
                  lambda message: (self.status.setText(f"失败：{message.splitlines()[-1]}"),
                                   self.run_btn.setEnabled(True)))

    def _done(self, outcome) -> None:
        self.result = outcome
        self.run_btn.setEnabled(True)
        self.progress.setValue(self.progress.maximum())
        parts = [f"写入 {outcome.written} 首"]
        if outcome.inferred:
            parts.append(f"推断 {outcome.inferred} 首")
        if outcome.skipped:
            parts.append(f"跳过 {outcome.skipped} 首")
        if outcome.failed:
            parts.append(f"失败 {outcome.failed} 首")
        self.status.setText(" · ".join(parts) + (
            f"，{len(outcome.review)} 首留给人工" if outcome.review else ""
        ))


class SettingsDialog(QDialog):
    """设置：数据源、写入方式、外观、权限。"""

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx

        layout = _dialog_shell(self, "设置", "改动即时保存。")

        layout.addWidget(self._section("数据源"))
        self.source_boxes: dict[str, QCheckBox] = {}
        for name in ("MusicBrainz", "网易云", "QQ音乐"):
            box = _check(name, bool(ctx.settings.get("sources", {}).get(name, True)))
            box.stateChanged.connect(self._save_sources)
            layout.addWidget(box)
            self.source_boxes[name] = box

        layout.addWidget(self._section("写入"))
        self.write_boxes = {}
        for key, label, default in (
            ("write_cover", "封面内嵌进文件", True),
            ("write_cover_file", "同时生成 cover.jpg", True),
            ("write_lrc", "同时生成 .lrc", True),
            ("replace_cover", "替换原有封面", True),
        ):
            box = _check(label, bool(ctx.settings.get(key, default)))
            box.stateChanged.connect(self._save_write)
            layout.addWidget(box)
            self.write_boxes[key] = box

        layout.addWidget(self._section("外观"))
        self.theme_box = _check("深色玻璃", ctx.settings.get("theme") == "dark")
        self.theme_box.stateChanged.connect(self._save_theme)
        layout.addWidget(self.theme_box)

        layout.addWidget(self._section("权限"))
        self.elevate_box = _check(
            "启动时自动申请管理员权限（保存不再弹窗）",
            bool(ctx.settings.get("auto_elevate", False)),
        )
        self.elevate_box.stateChanged.connect(self._save_elevate)
        layout.addWidget(self.elevate_box)

        from ..core import elevate

        self.always_box = _check(
            "让本程序始终以管理员启动（双击一次授权即可）",
            elevate.is_always_admin(),
        )
        self.always_box.stateChanged.connect(self._save_always)
        layout.addWidget(self.always_box)

        layout.addWidget(self._section("音乐文件夹"))
        row = QHBoxLayout()
        self.path_edit = QLineEdit(ctx.library_root or "")
        self.path_edit.setStyleSheet(
            f"QLineEdit {{ color:{T.LIGHT['text']}; font-size:12px; padding:6px 10px;"
            " border:1px solid rgba(0,0,0,0.08); border-radius:9px;"
            " background:rgba(255,255,255,0.7); }")
        row.addWidget(self.path_edit, 1)
        pick = QPushButton("选择…")
        pick.clicked.connect(self._pick_folder)
        row.addWidget(pick)
        layout.addLayout(row)

        layout.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = QPushButton("完成")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self._save_folder()

    def _section(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(
            f"color:{T.LIGHT['text3']}; font-size:10px; font-weight:600;"
            " letter-spacing:1px; background:transparent; margin-top:8px;")
        return label

    def _save_sources(self) -> None:
        self.ctx.settings["sources"] = {
            name: box.isChecked() for name, box in self.source_boxes.items()
        }
        self.ctx.save_settings()

    def _save_write(self) -> None:
        for key, box in self.write_boxes.items():
            self.ctx.settings[key] = box.isChecked()
        self.ctx.save_settings()

    def _save_theme(self) -> None:
        self.ctx.settings["theme"] = "dark" if self.theme_box.isChecked() else "light"
        self.ctx.save_settings()

    def _save_elevate(self) -> None:
        self.ctx.settings["auto_elevate"] = self.elevate_box.isChecked()
        self.ctx.save_settings()

    def _save_always(self) -> None:
        from ..core import elevate

        ok, error = elevate.set_always_admin(self.always_box.isChecked())
        if not ok:
            self.always_box.setChecked(False)

    def _pick_folder(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path = QFileDialog.getExistingDirectory(self, "选择音乐文件夹", self.ctx.library_root or "")
        if path:
            self.path_edit.setText(path)
            self._save_folder()

    def _save_folder(self) -> None:
        path = self.path_edit.text().strip()
        if path and path != self.ctx.library_root:
            self.ctx.settings["library_root"] = path
            self.ctx.library_root = path
            self.ctx.save_settings()

"""应用级上下文：数据库、库路径、数据源、设置、当前工作对象。"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field

from ..core import db
from ..core.models import Candidate, Track
from ..providers import build_providers
from ..write.service import PendingWrite

DEFAULT_SETTINGS = {
    "auto_prematch": True,
    "continuous": True,
    "duration_tolerance": 3,
    "sources": {"MusicBrainz": True, "网易云": True, "QQ音乐": True},
    "write_cover": True,
    "write_cover_file": True,
    "write_lrc": True,
    "replace_cover": True,

    "theme": "light",
}

_app_dir_cache: str | None = None


def _is_writable(path: str) -> bool:
    """真正建目录并写一个探针文件，避免只看路径存在与否。"""
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".mt_write_probe")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def data_dir_candidates() -> list[str]:
    """按优先级列出候选数据目录。

    APPDATA 被策略锁死、漫游配置不可写、或运行在受限账户下时，
    会依次退到 LOCALAPPDATA、用户目录、临时目录。
    """
    out: list[str] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        out.append(os.path.join(appdata, "MusicTagger"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(os.path.join(local, "MusicTagger"))
    home = os.path.expanduser("~")
    if home:
        out.append(os.path.join(home, "MusicTagger"))
    # 便携模式：用户目录不可写时，数据放在程序旁边
    exe_dir = _program_dir()
    if exe_dir:
        out.append(os.path.join(exe_dir, "MusicTaggerData"))
    out.append(os.path.join(tempfile.gettempdir(), "MusicTagger"))
    return out


def _migrate_legacy(target: str) -> None:
    """保留占位：数据目录名沿用旧版，无需迁移。"""
    return


def _program_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def app_data_dir() -> str:
    """返回第一个可写的数据目录。全部不可写时抛 RuntimeError。"""
    global _app_dir_cache
    if _app_dir_cache and _is_writable(_app_dir_cache):
        return _app_dir_cache
    errors = []
    for path in data_dir_candidates():
        if _is_writable(path):
            _migrate_legacy(path)
            _app_dir_cache = path
            return path
        errors.append(path)
    raise RuntimeError("没有可写的数据目录：" + "；".join(errors))


@dataclass
class AppContext:
    conn: sqlite3.Connection
    library_root: str = ""
    data_dir: str = field(default_factory=app_data_dir)
    settings: dict = field(default_factory=lambda: dict(DEFAULT_SETTINGS))
    queue: list[Track] = field(default_factory=list)
    queue_index: int = 0
    current_track: Track | None = None
    candidates: list[Candidate] = field(default_factory=list)
    pending: list[PendingWrite] = field(default_factory=list)
    pending_cover: bytes | None = None
    pending_lyrics: str | None = None

    @property
    def providers(self):
        return build_providers(self.settings.get("sources", {}))

    def settings_path(self) -> str:
        return os.path.join(self.data_dir, "settings.json")

    def load_settings(self) -> None:
        path = self.settings_path()
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                self.settings.update(json.load(fh))
        except (OSError, ValueError):
            pass

    def save_settings(self) -> None:
        try:
            with open(self.settings_path(), "w", encoding="utf-8") as fh:
                json.dump(self.settings, fh, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def set_current(self, track: Track) -> None:
        self.current_track = track

    def advance(self) -> Track | None:
        if not self.queue:
            return None
        self.queue_index = min(self.queue_index + 1, len(self.queue) - 1)
        self.current_track = self.queue[self.queue_index]
        return self.current_track

    def retreat(self) -> Track | None:
        if not self.queue:
            return None
        self.queue_index = max(self.queue_index - 1, 0)
        self.current_track = self.queue[self.queue_index]
        return self.current_track

    def queue_position(self) -> tuple[int, int]:
        return (self.queue_index + 1, len(self.queue)) if self.queue else (0, 0)


def open_context(data_dir: str = "", library_root: str = "") -> AppContext:
    root = data_dir or app_data_dir()
    os.makedirs(root, exist_ok=True)
    conn = db.connect(os.path.join(root, "index.db"))
    ctx = AppContext(conn=conn, data_dir=root)
    ctx.load_settings()
    ctx.library_root = library_root or db.get_meta(conn, "library_root", "")
    return ctx


"""测试公共夹具：合成音频文件、临时库。"""
from __future__ import annotations

import os
import wave

import pytest

from app.core import db


def make_wav(
    path: str,
    seconds: float = 1.0,
    rate: int = 44100,
    channels: int = 2,
    sampwidth: int = 2,
) -> str:
    """生成一段静音 WAV。真实可解析，不是伪造文件头。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(sampwidth)
        handle.setframerate(rate)
        handle.writeframes(b"\x00" * frames * channels * sampwidth)
    return str(path)


def tag_wav(path: str, title="", artist="", album="", year="", track="") -> None:
    """给 WAV 写入 ID3 标签，用于验证读取链路。"""
    from mutagen.id3 import TALB, TDRC, TIT2, TPE1, TRCK
    from mutagen.wave import WAVE

    audio = WAVE(path)
    if audio.tags is None:
        audio.add_tags()
    if title:
        audio.tags.add(TIT2(encoding=3, text=title))
    if artist:
        audio.tags.add(TPE1(encoding=3, text=artist))
    if album:
        audio.tags.add(TALB(encoding=3, text=album))
    if year:
        audio.tags.add(TDRC(encoding=3, text=year))
    if track:
        audio.tags.add(TRCK(encoding=3, text=track))
    audio.save()


@pytest.fixture
def library(tmp_path):
    """一个装了 3 首 WAV 的空库目录。"""
    root = tmp_path / "music"
    make_wav(str(root / "01 晴天.wav"), seconds=0.2)
    make_wav(str(root / "02 七里香.wav"), seconds=0.2)
    make_wav(str(root / "03 夜曲.wav"), seconds=0.2)
    return root


@pytest.fixture
def conn(tmp_path):
    database = db.connect(str(tmp_path / "index.db"))
    yield database
    database.close()


@pytest.fixture(scope="session")
def tk_root():
    """整个测试进程只建一个 Tk 根，避免反复初始化 Tcl 解释器。"""
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def ui(tk_root, tmp_path):
    """装好三首歌、建好主窗口的界面实例。"""
    from app.core import scan
    from app.ui_tk.main_window import MainWindow
    from app.ui_tk.state import open_context

    root = tmp_path / "music"
    make_wav(str(root / "01 晴天.wav"), seconds=0.2)
    make_wav(str(root / "02 七里香.wav"), seconds=0.2)
    make_wav(str(root / "03 夜曲.wav"), seconds=0.2)

    ctx = open_context(data_dir=str(tmp_path / "data"), library_root=str(root))
    # 默认关掉所有数据源（避免联网）；新增源也会自动包含进来
    from app.providers import PROVIDER_NAMES

    ctx.settings["sources"] = {name: False for name in PROVIDER_NAMES}
    scan.scan(ctx.conn, str(root))
    window = MainWindow(ctx, tk_root)
    tk_root.update_idletasks()
    yield window
    window.destroy()
    ctx.conn.close()

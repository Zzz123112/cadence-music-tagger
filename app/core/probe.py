"""读取音频参数与现有标签。只读，绝不写。"""
from __future__ import annotations

import os
from typing import Any

import mutagen

from .models import ProbeResult

EXT_FORMAT = {
    ".flac": "FLAC",
    ".ape": "APE",
    ".wv": "WV",
    ".tta": "TTA",
    ".tak": "TAK",
    ".dsf": "DSF",
    ".dff": "DFF",
    ".iso": "SACD",
    ".wav": "WAV",
    ".wave": "WAV",
    ".aif": "AIFF",
    ".aiff": "AIFF",
    ".mp3": "MP3",
    ".m4a": "M4A",
    ".mp4": "M4A",
    ".alac": "M4A",
    ".ogg": "OGG",
    ".oga": "OGG",
    ".opus": "OPUS",
    ".wma": "WMA",
    ".asf": "WMA",
}

SUPPORTED_EXTS = set(EXT_FORMAT)
# 只读格式：可以扫描展示，禁止写入（PRD §8.2）
READ_ONLY_FORMATS = {"DFF", "SACD", "TAK"}


def format_of(path: str) -> str:
    return EXT_FORMAT.get(os.path.splitext(path)[1].lower(), "")


def is_supported(path: str) -> bool:
    return format_of(path) in EXT_FORMAT.values()


def _first(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return str(value[0]).strip() if value else ""
    try:
        return str(value).strip()
    except Exception:
        return ""


def _read_easy_tags(path: str) -> dict[str, str]:
    """用 mutagen 的 easy 接口统一读常见标签，失败返回空。"""
    out: dict[str, str] = {}
    try:
        audio = mutagen.File(path, easy=True)
    except Exception:
        return out
    if audio is None or getattr(audio, "tags", None) is None:
        return out
    for key in ("title", "artist", "album", "date", "tracknumber", "albumartist"):
        try:
            val = _first(audio.tags.get(key))
        except Exception:
            val = ""
        if val:
            out[key] = val
    return out


# 各容器里同一个逻辑字段的不同键名。按顺序尝试，命中即止。
TAG_KEYS: dict[str, tuple[str, ...]] = {
    "title": ("TIT2", "title", "TITLE", "Title", "\xa9nam", "WM/Title"),
    "artist": ("TPE1", "artist", "ARTIST", "Artist", "\xa9ART", "Author"),
    "album": ("TALB", "album", "ALBUM", "Album", "\xa9alb", "WM/AlbumTitle"),
    "year": ("TDRC", "TYER", "date", "DATE", "Date", "Year", "\xa9day", "WM/Year"),
    "track": ("TRCK", "tracknumber", "TRACKNUMBER", "Track", "trkn", "WM/TrackNumber"),
    "albumartist": ("TPE2", "albumartist", "ALBUMARTIST", "Album Artist", "aART", "WM/AlbumArtist"),
}


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "text"):
        text = getattr(value, "text")
        return str(text[0]).strip() if text else ""
    if isinstance(value, (list, tuple)):
        if not value:
            return ""
        first = value[0]
        if isinstance(first, tuple) and first:
            return str(first[0])
        return str(first).strip()
    return str(value).strip()


def _read_tags(audio: Any) -> dict[str, str]:
    """直接按容器键名读标签，比 easy 接口覆盖更全。"""
    tags = getattr(audio, "tags", None)
    out: dict[str, str] = {}
    if tags is None:
        return out
    for field, keys in TAG_KEYS.items():
        for key in keys:
            try:
                value = tags.get(key)
            except Exception:
                continue
            text = _stringify(value)
            if text:
                out[field] = text
                break
    return out


def extract_cover(path: str) -> bytes | None:
    """取出文件里内嵌的第一张封面图片字节。没有则返回 None。"""
    import base64

    try:
        audio = mutagen.File(path)
    except Exception:
        return None
    if audio is None:
        return None

    # FLAC 的 pictures 列表
    pictures = getattr(audio, "pictures", None)
    if pictures:
        for picture in pictures:
            data = getattr(picture, "data", None)
            if data:
                return bytes(data)

    tags = getattr(audio, "tags", None)
    if tags is None:
        return None
    try:
        # MP3: APIC 帧
        for key in tags.keys():
            if str(key).upper().startswith("APIC"):
                frames = tags.getall(key) if hasattr(tags, "getall") else [tags[key]]
                for frame in frames:
                    data = getattr(frame, "data", None)
                    if data:
                        return bytes(data)
        # MP4: covr
        if "covr" in tags:
            covers = tags["covr"]
            if covers and isinstance(covers, list):
                return bytes(covers[0])
        # Vorbis/Opus: base64 内嵌图片
        for key in tags.keys():
            if str(key).lower() == "metadata_block_picture":
                raw = str(tags[key][0])
                try:
                    blob = base64.b64decode(raw)
                except Exception:
                    continue
                return blob
    except Exception:
        pass
    return None


def _raw_cover(audio: Any) -> bool:
    """判断是否内嵌了封面。"""
    try:
        tags = audio.tags
        if tags is None:
            return False
        if hasattr(audio, "pictures") and getattr(audio, "pictures"):
            return True
        keys = {str(k) for k in tags.keys()}
        if any(k.startswith("APIC") for k in keys):
            return True
        if "covr" in keys:
            return True
        if any(k.startswith("Cover Art") for k in keys):
            return True
        if "WM/Picture" in keys:
            return True
        # Vorbis / Opus 的封面以 base64 存在这个注释字段里
        if any(k.lower() == "metadata_block_picture" for k in keys):
            return True
    except Exception:
        return False
    return False


def _raw_lyrics(audio: Any) -> bool:
    try:
        tags = audio.tags
        if tags is None:
            return False
        keys = {str(k).lower() for k in tags.keys()}
        for k in keys:
            if k.startswith("uslt") or k in ("lyrics", "unsyncedlyrics", "unsynced lyrics"):
                return True
            if k == "\xa9lyr" or k == "wm/lyrics":
                return True
    except Exception:
        return False
    return False


def sidecar_lrc(path: str) -> str:
    """同名 .lrc 路径，存在则返回。"""
    stem, _ = os.path.splitext(path)
    for ext in (".lrc", ".LRC", ".Lrc"):
        candidate = stem + ext
        if os.path.isfile(candidate):
            return candidate
    return ""


def probe(path: str) -> ProbeResult:
    """读取一个文件。任何异常都收敛成 result.error，不向上抛。"""
    res = ProbeResult(fmt=format_of(path))
    try:
        audio = mutagen.File(path)
    except Exception as exc:  # 文件损坏 / 权限问题
        res.error = f"{type(exc).__name__}: {exc}"
        return res
    if audio is None:
        res.error = "无法识别的音频格式"
        return res

    info = getattr(audio, "info", None)
    if info is not None:
        res.duration = float(getattr(info, "length", 0.0) or 0.0)
        res.bitrate = int(getattr(info, "bitrate", 0) or 0)
        res.bit_depth = int(getattr(info, "bits_per_sample", 0) or 0)
        res.sample_rate = int(getattr(info, "sample_rate", 0) or 0)
        res.channels = int(getattr(info, "channels", 0) or 0)

    if not res.fmt:
        res.fmt = type(audio).__name__.upper()

    tags = _read_tags(audio)
    res.title = tags.get("title", "")
    res.artist = tags.get("artist", "")
    res.album = tags.get("album", "")
    res.year = tags.get("year", "")[:10]
    res.track = tags.get("track", "")
    res.album_artist = tags.get("albumartist", "")

    res.has_cover = _raw_cover(audio)
    res.has_lyrics = _raw_lyrics(audio) or bool(sidecar_lrc(path))
    return res

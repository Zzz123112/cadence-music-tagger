"""歌词解析与时间轴校验（对应验收 A-11）。"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

TIMESTAMP_RE = re.compile(r"\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
# 歌词比歌曲早结束多少秒以内算正常（前奏、间奏、尾奏都可能留白）
EARLY_TOLERANCE = 45.0
# 歌词超出歌曲时长多少秒算错位（往前超基本只能是配错了）
LATE_TOLERANCE = 5.0


@dataclass
class LyricsCheck:
    has_timeline: bool
    last_timestamp: float
    duration: float
    offset: float
    ok: bool
    reason: str

    def as_dict(self) -> dict:
        return {
            "has_timeline": self.has_timeline,
            "last_timestamp": round(self.last_timestamp, 2),
            "duration": round(self.duration, 2),
            "offset": round(self.offset, 2),
            "ok": self.ok,
            "reason": self.reason,
        }


def parse_timestamps(text: str) -> list[float]:
    """取出全部时间戳（秒），按出现顺序。"""
    if not text:
        return []
    out: list[float] = []
    for match in TIMESTAMP_RE.finditer(text):
        minutes = int(match.group(1))
        seconds = int(match.group(2))
        frac = match.group(3)
        if frac:
            fraction = float(f"0.{frac}")
        else:
            fraction = 0.0
        out.append(minutes * 60 + seconds + fraction)
    return out


def last_timestamp(text: str) -> float:
    stamps = parse_timestamps(text)
    return max(stamps) if stamps else 0.0


def has_timeline(text: str) -> bool:
    return bool(TIMESTAMP_RE.search(text or ""))


def validate(text: str, duration: float,
             early_tolerance: float = EARLY_TOLERANCE,
             late_tolerance: float = LATE_TOLERANCE) -> LyricsCheck:
    """校验歌词时间轴与歌曲时长是否匹配。

    - 歌词比歌曲早结束：留白很常见，宽松处理（默认 45 秒内算正常）
    - 歌词超过歌曲时长：基本一定配错了，严格处理（默认 5 秒）
    """
    if not text or not text.strip():
        return LyricsCheck(False, 0.0, duration, 0.0, False, "歌词为空")

    if not has_timeline(text):
        return LyricsCheck(False, 0.0, duration, 0.0, True, "无时间轴，播放器不会滚动")

    last = last_timestamp(text)
    if not duration:
        return LyricsCheck(True, last, duration, 0.0, True, "歌曲时长未知，无法校验")

    offset = duration - last
    if offset > early_tolerance:
        return LyricsCheck(True, last, duration, offset, False,
                           f"时间轴可能错位，末尾比歌曲早 {offset:.0f} 秒")
    if offset < -late_tolerance:
        return LyricsCheck(True, last, duration, offset, False,
                           f"时间轴可能错位，末尾超出歌曲 {abs(offset):.0f} 秒")
    return LyricsCheck(True, last, duration, offset, True, "时间轴正常")


def sidecar_path(audio_path: str) -> str:
    """同名 .lrc 的路径。"""
    stem, _ext = os.path.splitext(audio_path)
    return stem + ".lrc"


def save_lrc(audio_path: str, text: str, overwrite: bool = True) -> str:
    """把歌词写成同名 .lrc 文件，返回写入路径。

    歌词为空时抛 ValueError；文件已存在且不允许覆盖时抛 FileExistsError。
    直接写失败（多为安全软件拦截）时会借 PowerShell 落盘。
    """
    if not text or not text.strip():
        raise ValueError("歌词为空")
    target = sidecar_path(audio_path)
    if os.path.exists(target) and not overwrite:
        raise FileExistsError(target)
    body = text if text.endswith("\n") else text + "\n"
    from .fileio import write_text

    ok, detail = write_text(target, body)
    if not ok:
        raise PermissionError(f"歌词写入失败：{detail}")
    return target


def language_variants(text: str) -> dict[str, str]:
    """把歌词按行拆开（保留原始顺序，不做翻译对齐）。"""
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return {"original": "\n".join(lines), "translation": ""}

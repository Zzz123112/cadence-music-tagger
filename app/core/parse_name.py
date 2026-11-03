"""文件名解析：产出候选，不产出结论。"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass

# 版本词：参与"版本不符"判断，不参与噪声清理
VERSION_WORDS = {
    "live": "live",
    "现场": "live",
    "演唱会": "live",
    "伴奏": "instrumental",
    "instrumental": "instrumental",
    "off vocal": "instrumental",
    "remix": "remix",
    "混音": "remix",
    "demo": "demo",
    "acoustic": "acoustic",
    "不插电": "acoustic",
    "翻唱": "cover",
    "cover": "cover",
}

# 噪声标签：与歌曲身份无关，解析前去掉
NOISE_TOKENS = (
    "hi-res", "hires", "hi res", "dsd64", "dsd128", "dsd256", "dsd512",
    "24bit", "16bit", "32bit", "24-96", "24-192", "flac", "ape", "wav",
    "dsf", "dff", "lossless", "无损", "hq", "hires audio", "sacd",
)

_BRACKET_RE = re.compile(r"[\[\(（【][^\[\]\(\)（）【】]*[\]\)）】]")
_TRACK_PREFIX_RE = re.compile(r"^\s*(?:track\s*)?(\d{1,3})(?:\s*[\.\-_、\)]\s*|\s+)", re.IGNORECASE)
_SEP_RE = re.compile(r"\s+[-–—－]\s+")
_BOOKTITLE_RE = re.compile(r"^(?P<artist>[^《]+?)\s*《(?P<title>[^》]+)》\s*$")
_BRACKET_ARTIST_RE = re.compile(r"^\[(?P<artist>[^\]]+)\]\s*(?P<title>.+)$")


@dataclass
class ParseResult:
    artist: str = ""
    title: str = ""
    version: str = ""
    track: str = ""
    confidence: float = 0.0
    cleaned: str = ""

    def is_usable(self) -> bool:
        return bool(self.title) and self.confidence >= 0.3


def _normalize_width(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def extract_version(text: str) -> str:
    """从文本里找版本词，返回规范化标签（live / instrumental / ...）。"""
    low = _normalize_width(text).lower()
    for word, label in VERSION_WORDS.items():
        if word in low:
            return label
    return ""


def strip_noise(text: str) -> tuple[str, str]:
    """去掉方括号/圆括号里的噪声标签。返回 (清理后文本, 被去掉的内容)。"""
    removed: list[str] = []

    def _sub(match: re.Match) -> str:
        inner = match.group(0)[1:-1].strip()
        low = _normalize_width(inner).lower()
        if any(token in low for token in NOISE_TOKENS):
            removed.append(inner)
            return " "
        # 括号里是版本词时保留，交由版本检测处理
        if extract_version(inner):
            return " "
        return match.group(0)

    cleaned = _BRACKET_RE.sub(_sub, text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -_")
    return cleaned, " ".join(removed)


def parse_name(filename: str, known_artists: set[str] | None = None) -> ParseResult:
    """从文件名解析歌手/歌名/版本/曲序。

    解析只是候选，界面上允许手改，绝不直接用于写入。
    """
    stem = os.path.splitext(os.path.basename(filename))[0]
    stem = _normalize_width(stem).strip()
    original = stem

    track = ""
    m = _TRACK_PREFIX_RE.match(stem)
    if m:
        track = str(int(m.group(1)))
        stem = stem[m.end():].strip()

    cleaned, _removed = strip_noise(stem)
    version = extract_version(original)
    if not cleaned:
        cleaned = stem

    # 1) 歌手《歌名》
    m = _BOOKTITLE_RE.match(cleaned)
    if m:
        return ParseResult(
            artist=m.group("artist").strip(),
            title=m.group("title").strip(),
            version=version,
            track=track,
            confidence=0.95,
            cleaned=cleaned,
        )

    # 2) [歌手] 歌名
    m = _BRACKET_ARTIST_RE.match(cleaned)
    if m:
        return ParseResult(
            artist=m.group("artist").strip(),
            title=m.group("title").strip(),
            version=version,
            track=track,
            confidence=0.85,
            cleaned=cleaned,
        )

    # 3) A - B / A – B，用已知歌手集合判断左右
    parts = _SEP_RE.split(cleaned)
    if len(parts) == 2:
        left, right = parts[0].strip(), parts[1].strip()
        known = {a.strip().lower() for a in (known_artists or set())}
        left_known = left.lower() in known
        right_known = right.lower() in known
        if right_known and not left_known:
            artist, title, conf = right, left, 0.9
        elif left_known and not right_known:
            artist, title, conf = left, right, 0.95
        else:
            artist, title, conf = left, right, 0.7
        return ParseResult(artist=artist, title=title, version=version,
                           track=track, confidence=conf, cleaned=cleaned)

    # 4) 只有歌名（或只有歌手，无法判断）
    return ParseResult(
        artist="",
        title=cleaned.strip(),
        version=version,
        track=track,
        confidence=0.45 if cleaned else 0.0,
        cleaned=cleaned,
    )


def build_keywords(result: ParseResult) -> str:
    """把解析结果拼成检索关键词。"""
    if result.artist and result.title:
        return f"{result.title} {result.artist}"
    return result.title or result.cleaned


def variants(result: ParseResult) -> list[str]:
    """多个备选检索词，从精确到宽泛。"""
    out: list[str] = []
    if result.artist and result.title:
        out.append(f"{result.title} {result.artist}")
    if result.title:
        out.append(result.title)
    if result.cleaned and result.cleaned not in out:
        out.append(result.cleaned)
    seen, uniq = set(), []
    for v in out:
        v = v.strip()
        if v and v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq

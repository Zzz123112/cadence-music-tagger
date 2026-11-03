"""数据模型：跨越存储层、匹配层、写入层的公共结构。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

# 曲目状态（全局唯一来源，见 PRD §6.3）
STATUS_READY = "ready"
STATUS_NO_COVER = "no_cover"
STATUS_NO_LYRICS = "no_lyrics"
STATUS_EMPTY = "empty"
STATUS_CONFLICT = "conflict"
STATUS_IGNORED = "ignored"

STATUS_LABELS = {
    STATUS_READY: "就绪",
    STATUS_NO_COVER: "缺封面",
    STATUS_NO_LYRICS: "缺歌词",
    STATUS_EMPTY: "标签全空",
    STATUS_CONFLICT: "信息冲突",
    STATUS_IGNORED: "已忽略",
}

# 六项证据及其权重（PRD §5.4）
EVIDENCE_KEYS = ("title", "artist", "album", "duration", "track", "context")
EVIDENCE_LABELS = {
    "title": "歌名",
    "artist": "歌手",
    "album": "专辑",
    "duration": "时长",
    "track": "曲序",
    "context": "上下文",
}
EVIDENCE_WEIGHTS = {
    "title": 0.20,
    "artist": 0.25,
    "album": 0.15,
    "duration": 0.25,
    "track": 0.05,
    "context": 0.10,
}


@dataclass
class ProbeResult:
    """从文件里读出的一切。不涉及任何写操作。"""

    fmt: str = ""
    duration: float = 0.0
    bitrate: int = 0
    bit_depth: int = 0
    sample_rate: int = 0
    channels: int = 0
    title: str = ""
    artist: str = ""
    album: str = ""
    year: str = ""
    track: str = ""
    album_artist: str = ""
    has_cover: bool = False
    has_lyrics: bool = False
    error: str = ""

    def audio_signature(self) -> tuple:
        """用于写入前后比对：参数不变即认为音频未被改动。"""
        return (
            round(self.duration, 2),
            int(self.bit_depth),
            int(self.sample_rate),
            int(self.channels),
        )


@dataclass
class Track:
    id: Optional[int] = None
    path: str = ""
    name: str = ""
    size: int = 0
    mtime: int = 0
    fmt: str = ""
    duration: float = 0.0
    bitrate: int = 0
    bit_depth: int = 0
    sample_rate: int = 0
    channels: int = 0
    tag_title: str = ""
    tag_artist: str = ""
    tag_album: str = ""
    tag_year: str = ""
    tag_track: str = ""
    tag_albumartist: str = ""
    has_cover: bool = False
    has_lyrics: bool = False
    status: str = STATUS_EMPTY
    parsed_artist: str = ""
    parsed_title: str = ""
    parsed_version: str = ""
    parsed_conf: float = 0.0
    last_scan: int = 0

    def quality_label(self) -> str:
        if self.sample_rate and self.bit_depth:
            if self.fmt in ("DSF", "DSD"):
                return f"DSD{int(self.sample_rate / 44100)}"
            return f"{self.bit_depth} / {self.sample_rate / 1000:g}"
        if self.sample_rate:
            return f"{self.sample_rate / 1000:g} kHz"
        return "—"

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("id", None)
        d["has_cover"] = int(self.has_cover)
        d["has_lyrics"] = int(self.has_lyrics)
        return d


@dataclass
class Candidate:
    """一条线上候选。字段名与 Track 的 tag_* 对应。"""

    source: str
    title: str
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    year: str = ""
    track: str = ""
    duration: float = 0.0
    quality_hints: str = ""
    cover_urls: list[str] = field(default_factory=list)
    lyrics: Optional[str] = None
    raw_id: str = ""
    score: float = 0.0
    evidence: dict[str, float] = field(default_factory=dict)

    def to_fields(self) -> dict[str, str]:
        return {
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "year": self.year,
            "track": self.track,
        }

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Candidate":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Draft:
    """已采用但未确认的字段选择。"""

    track_id: int
    fields: dict[str, dict[str, str]] = field(default_factory=dict)
    # {"title": {"value": "七里香", "source": "MusicBrainz"}, ...}

    def is_empty(self) -> bool:
        return not self.fields

    def set_field(self, key: str, value: str, source: str) -> None:
        self.fields[key] = {"value": value, "source": source}

    def drop_field(self, key: str) -> None:
        self.fields.pop(key, None)


@dataclass
class AlbumContext:
    """同目录已确认曲目的专辑共识。"""

    album: str = ""
    artist: str = ""
    hit: int = 0
    total: int = 0

    @property
    def ratio(self) -> float:
        return (self.hit / self.total) if self.total else 0.0

    @property
    def usable(self) -> bool:
        return bool(self.album) and self.hit >= 2 and self.ratio >= 0.6

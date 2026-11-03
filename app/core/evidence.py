"""六项证据计算。分数只用于排序与展示，绝不触发写入。"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from .models import (
    EVIDENCE_KEYS,
    EVIDENCE_WEIGHTS,
    AlbumContext,
    Candidate,
    Track,
)

# 命中阈值：达到才算"这项对上了"，避免中性值被算成吻合
HIT_THRESHOLD = 0.75

_ARTIST_SEP = re.compile(r"[、,，/&;；]|\bfeat\.?\b|\bft\.?\b|\s×\s", re.IGNORECASE)
_PUNCT = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


def normalize(text: str) -> str:
    """全半角统一、去标点与空白，用于比对。"""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", str(text)).lower()
    t = _PUNCT.sub("", t)
    return t.strip()


def split_artists(text: str) -> set[str]:
    if not text:
        return set()
    parts = _ARTIST_SEP.split(str(text))
    return {normalize(p) for p in parts if normalize(p)}


def _title_score(track: Track, cand: Candidate) -> float:
    a = normalize(track.parsed_title or track.tag_title)
    b = normalize(cand.title)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.6
    return 0.0


def _artist_score(track: Track, cand: Candidate) -> float:
    known = split_artists(track.parsed_artist or track.tag_artist)
    other = split_artists(cand.artist)
    if not known or not other:
        return 0.0
    return 1.0 if known & other else 0.0


def _album_score(track: Track, cand: Candidate, ctx: AlbumContext) -> float:
    reference = ""
    if ctx.usable:
        reference = ctx.album
    elif track.tag_album:
        reference = track.tag_album
    if not reference:
        return 0.5  # 无参照，中性
    if not cand.album:
        return 0.0
    return 1.0 if normalize(cand.album) == normalize(reference) else 0.0


def _duration_score(track: Track, cand: Candidate) -> float:
    if not track.duration or not cand.duration:
        return 0.0
    diff = abs(float(track.duration) - float(cand.duration))
    if diff <= 3:
        return 1.0
    if diff <= 10:
        return 0.5
    return 0.0


def _track_score(track: Track, cand: Candidate) -> float:
    local = _local_track_number(track)
    other = _as_int(cand.track)
    if not local or other is None:
        return 0.0
    return 1.0 if int(local) == other else 0.0


@lru_cache(maxsize=4096)
def _number_from_name(name: str) -> str:
    from .parse_name import parse_name

    return parse_name(name).track


def _local_track_number(track: Track) -> str:
    """文件名解析出的序号优先，其次是已有标签里的曲序。"""
    from_name = _number_from_name(track.name or track.path)
    if from_name:
        return from_name
    digits = _as_int(track.tag_track)
    return str(digits) if digits is not None else ""


def _as_int(value) -> int | None:
    if value is None:
        return None
    m = re.search(r"\d+", str(value))
    return int(m.group()) if m else None


def _context_score(track: Track, cand: Candidate, ctx: AlbumContext) -> float:
    if not ctx.usable or not cand.album:
        return 0.0
    return 1.0 if normalize(cand.album) == normalize(ctx.album) else 0.0


def evaluate(track: Track, cand: Candidate, ctx: AlbumContext | None = None) -> dict[str, float]:
    """返回六项证据的得分（0 / 0.5 / 0.6 / 1.0）。"""
    ctx = ctx or AlbumContext()
    return {
        "title": _title_score(track, cand),
        "artist": _artist_score(track, cand),
        "album": _album_score(track, cand, ctx),
        "duration": _duration_score(track, cand),
        "track": _track_score(track, cand),
        "context": _context_score(track, cand, ctx),
    }


def weighted_score(evidence: dict[str, float]) -> float:
    return round(sum(EVIDENCE_WEIGHTS[k] * float(evidence.get(k, 0.0)) for k in EVIDENCE_KEYS), 4)


def hit_count(evidence: dict[str, float]) -> int:
    """吻合项数，用于界面上的「吻合 x / 6」。"""
    return sum(1 for k in EVIDENCE_KEYS if float(evidence.get(k, 0.0)) >= HIT_THRESHOLD)


def apply(track: Track, cand: Candidate, ctx: AlbumContext | None = None) -> Candidate:
    """就地计算证据与分数，返回同一个 candidate。"""
    cand.evidence = evaluate(track, cand, ctx)
    cand.score = weighted_score(cand.evidence)
    return cand


def rank(track: Track, cands: list[Candidate], ctx: AlbumContext | None = None) -> list[Candidate]:
    for c in cands:
        apply(track, c, ctx)
    return sorted(cands, key=lambda c: (hit_count(c.evidence), c.score), reverse=True)

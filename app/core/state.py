"""状态机：全应用唯一的状态来源（PRD P5）。"""
from __future__ import annotations

import os
from collections import Counter

from .evidence import normalize
from .models import (
    STATUS_CONFLICT,
    STATUS_EMPTY,
    STATUS_IGNORED,
    STATUS_NO_COVER,
    STATUS_NO_LYRICS,
    STATUS_READY,
    Track,
)

# 冲突判定的保守阈值，宁可漏报也不误报
CONFLICT_MIN_TRACKS = 4
CONFLICT_MIN_RATIO = 0.7


def is_label_empty(track: Track) -> bool:
    return not (track.tag_title or track.tag_artist or track.tag_album)


def _album_consensus(folder_tracks: list[Track]) -> tuple[str, int, int]:
    votes = [t for t in folder_tracks if t.tag_album]
    if not votes:
        return "", 0, len(folder_tracks)
    counter = Counter(normalize(t.tag_album) for t in votes)
    key, count = counter.most_common(1)[0]
    sample = next(t for t in votes if normalize(t.tag_album) == key)
    return sample.tag_album, count, len(folder_tracks)


def is_conflicted(track: Track, folder_tracks: list[Track]) -> bool:
    """专辑标签与同目录主流不一致，且证据足够充分时才算冲突。"""
    if not track.tag_album or len(folder_tracks) < CONFLICT_MIN_TRACKS:
        return False
    consensus, count, total = _album_consensus(folder_tracks)
    if not consensus:
        return False
    if normalize(consensus) == normalize(track.tag_album):
        return False
    return (count / total) >= CONFLICT_MIN_RATIO


def compute_status(
    track: Track,
    folder_tracks: list[Track] | None = None,
    keep_ignored: bool = True,
) -> str:
    """按优先级返回状态：忽略 > 冲突 > 全空 > 缺封面 > 缺歌词 > 就绪。"""
    if keep_ignored and track.status == STATUS_IGNORED:
        return STATUS_IGNORED
    folder_tracks = folder_tracks or [track]
    if is_conflicted(track, folder_tracks):
        return STATUS_CONFLICT
    if is_label_empty(track):
        return STATUS_EMPTY
    if not track.has_cover:
        return STATUS_NO_COVER
    if not track.has_lyrics:
        return STATUS_NO_LYRICS
    return STATUS_READY


def is_missing_category(status: str) -> bool:
    return status in (STATUS_NO_COVER, STATUS_NO_LYRICS, STATUS_EMPTY, STATUS_CONFLICT)


def folder_of(path: str) -> str:
    return os.path.dirname(os.path.abspath(path))

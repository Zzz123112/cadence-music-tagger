"""目录扫描与索引更新。"""
from __future__ import annotations

import os
import time
from typing import Callable

from . import db
from .models import Track
from .parse_name import parse_name
from .probe import format_of, probe
from .state import compute_status

SKIP_DIRS = {
    ".musictagger_backup",
    "$RECYCLE.BIN",
    "System Volume Information",
}


def iter_audio_files(root: str):
    """遍历目录，产出支持的音频文件路径。跳过备份与系统目录。"""
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
        )
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            if format_of(path):
                yield path


def _track_from_probe(path: str, stat: os.stat_result, existing: Track | None,
                      known_artists: set[str]) -> Track:
    result = probe(path)
    name = os.path.basename(path)
    parsed = parse_name(name, known_artists)

    track = Track(
        id=existing.id if existing else None,
        path=os.path.abspath(path),
        name=name,
        size=stat.st_size,
        mtime=int(stat.st_mtime),
        fmt=result.fmt,
        duration=result.duration,
        bitrate=result.bitrate,
        bit_depth=result.bit_depth,
        sample_rate=result.sample_rate,
        channels=result.channels,
        tag_title=result.title,
        tag_artist=result.artist,
        tag_album=result.album,
        tag_year=result.year,
        tag_track=result.track,
        tag_albumartist=result.album_artist,
        has_cover=result.has_cover,
        has_lyrics=result.has_lyrics,
        status=existing.status if existing else "empty",
        parsed_artist=parsed.artist,
        parsed_title=parsed.title,
        parsed_version=parsed.version,
        parsed_conf=parsed.confidence,
        last_scan=int(time.time()),
    )
    return track


def scan(
    conn,
    root: str,
    progress: Callable[[int, int], None] | None = None,
    force: bool = False,
    delete_missing: bool = True,
) -> dict:
    """扫描目录并更新索引。

    - 增量：mtime 与 size 都未变的文件直接跳过
    - 状态：受影响目录的曲目全部重算
    - 缺失：库里有、磁盘上没有的记录会被清掉
    """
    root = os.path.abspath(root)
    files = list(iter_audio_files(root))
    existing = {t.path: t for t in db.list_tracks(conn, include_ignored=True)}
    known_artists = {t.tag_artist for t in existing.values() if t.tag_artist}

    stats = {
        "total": len(files),
        "added": 0,
        "updated": 0,
        "unchanged": 0,
        "removed": 0,
        "errors": [],
    }
    affected: set[str] = set()
    seen: set[str] = set()

    for index, path in enumerate(files, start=1):
        path = os.path.abspath(path)
        seen.add(path)
        try:
            stat = os.stat(path)
        except OSError as exc:
            stats["errors"].append(f"{path}: {exc}")
            continue

        old = existing.get(path)
        if (
            old is not None
            and not force
            and old.mtime == int(stat.st_mtime)
            and old.size == stat.st_size
        ):
            stats["unchanged"] += 1
        else:
            track = _track_from_probe(path, stat, old, known_artists)
            db.upsert_track(conn, track)
            if track.tag_artist:
                known_artists.add(track.tag_artist)
            if old is None:
                stats["added"] += 1
            else:
                stats["updated"] += 1
            affected.add(os.path.dirname(path))

        if progress and (index % 20 == 0 or index == len(files)):
            progress(index, len(files))

    missing = [p for p in existing if p not in seen]
    if missing and delete_missing:
        stats["removed"] = db.delete_tracks(conn, missing)
        for p in missing:
            affected.add(os.path.dirname(p))

    for folder in affected:
        refresh_folder_status(conn, folder)

    conn.commit()
    return stats


def refresh_one(conn, path: str) -> Track | None:
    """重新读一个文件并更新索引（保存之后用）。"""
    path = os.path.abspath(path)
    existing = db.get_track_by_path(conn, path)
    try:
        stat = os.stat(path)
    except OSError:
        return None
    known = {t.tag_artist for t in db.list_tracks(conn, include_ignored=True) if t.tag_artist}
    track = _track_from_probe(path, stat, existing, known)
    db.upsert_track(conn, track)
    refresh_folder_status(conn, os.path.dirname(path))
    conn.commit()
    return db.get_track_by_path(conn, path)


def refresh_folder_status(conn, folder: str) -> int:
    """重算一个目录内所有曲目的状态。返回变化条数。"""
    tracks = db.folder_tracks(conn, folder)
    changed = 0
    for track in tracks:
        new_status = compute_status(track, tracks)
        if new_status != track.status:
            db.set_status(conn, track.id, new_status)
            changed += 1
    return changed

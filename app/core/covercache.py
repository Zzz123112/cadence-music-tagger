"""封面磁盘缓存：同一张图只下载一次。"""
from __future__ import annotations

import hashlib
import os

from .models import Candidate


def cache_dir_for(data_dir: str) -> str:
    path = os.path.join(data_dir, "covers")
    os.makedirs(path, exist_ok=True)
    return path


def key_for(cand: Candidate) -> str:
    raw = f"{cand.source}|{cand.title}|{cand.artist}|{cand.album}|{cand.raw_id}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def get(data_dir: str, cand: Candidate) -> bytes | None:
    path = os.path.join(cache_dir_for(data_dir), key_for(cand) + ".img")
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def put(data_dir: str, cand: Candidate, data: bytes) -> None:
    if not data:
        return
    try:
        path = os.path.join(cache_dir_for(data_dir), key_for(cand) + ".img")
        with open(path, "wb") as fh:
            fh.write(data)
    except OSError:
        pass


def clear(data_dir: str) -> int:
    folder = cache_dir_for(data_dir)
    removed = 0
    for name in os.listdir(folder):
        try:
            os.remove(os.path.join(folder, name))
            removed += 1
        except OSError:
            pass
    return removed

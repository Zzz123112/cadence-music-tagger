"""Apple Music：用的是 Apple 公开的 iTunes Search 接口，不需要密钥。

Apple Music 与 iTunes Store 共用同一套曲库，所以这条就是 Apple Music 的数据。
"""
from __future__ import annotations

from ..core.models import Candidate
from . import http
from .base import BaseProvider, SearchQuery, dedupe

SEARCH_URL = "https://itunes.apple.com/search"
NAME = "Apple Music"


def _big_cover(url: str) -> str:
    """artworkUrl100 是 100×100，换成 600×600。"""
    if not url:
        return ""
    return url.replace("100x100bb", "600x600bb").replace("100x100", "600x600")


class ItunesProvider(BaseProvider):
    name = NAME

    def search(self, query: SearchQuery) -> list[Candidate]:
        params = {
            "term": query.keywords,
            "media": "music",
            "entity": "song",
            "limit": 15,
        }
        data = http.fetch_json(SEARCH_URL, params=params)
        return dedupe(self.parse(data))

    @staticmethod
    def parse(data: dict) -> list[Candidate]:
        out: list[Candidate] = []
        for item in (data or {}).get("results") or []:
            if not isinstance(item, dict):
                continue
            title = (item.get("trackName") or "").strip()
            if not title:
                continue
            artist = (item.get("artistName") or "").strip()
            album = (item.get("collectionName") or "").strip()
            try:
                duration = float(item.get("trackTimeMillis") or 0) / 1000.0
            except (TypeError, ValueError):
                duration = 0.0
            cover = _big_cover(item.get("artworkUrl100") or "")
            released = str(item.get("releaseDate") or "")
            out.append(
                Candidate(
                    source=NAME,
                    title=title,
                    artist=artist,
                    album=album,
                    year=released[:4],
                    track=str(item.get("trackNumber") or ""),
                    duration=duration,
                    quality_hints=str(item.get("primaryGenreName") or ""),
                    cover_urls=[cover] if cover else [],
                    raw_id=str(item.get("trackId") or ""),
                )
            )
        return out

    def fetch_cover(self, cand: Candidate, min_size: int = 0) -> bytes | None:
        for url in cand.cover_urls:
            if not url:
                continue
            try:
                return http.fetch_bytes(url)
            except Exception:
                continue
        return None


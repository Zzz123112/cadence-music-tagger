"""TheAudioDB：公开接口（免费测试 key 123），欧美曲库较全，带封面。"""
from __future__ import annotations

from ..core.models import Candidate
from . import http
from .base import BaseProvider, SearchQuery, dedupe

SEARCH_URL = "https://www.theaudiodb.com/api/v1/json/123/searchtrack.php"
NAME = "TheAudioDB"


class AudioDbProvider(BaseProvider):
    name = NAME

    def search(self, query: SearchQuery) -> list[Candidate]:
        artist = (query.artist or "").strip()
        title = (query.title or query.keywords or "").strip()
        if not artist or not title:
            # 这个接口必须同时给歌手和曲名，缺一个就不查，交给别的源
            return []
        params = {"s": artist, "t": title}
        data = http.fetch_json(SEARCH_URL, params=params)
        return dedupe(self.parse(data))

    @staticmethod
    def parse(data: dict) -> list[Candidate]:
        out: list[Candidate] = []
        for item in (data or {}).get("track") or []:
            if not isinstance(item, dict):
                continue
            title = (item.get("strTrack") or "").strip()
            if not title:
                continue
            try:
                duration = float(item.get("intDuration") or 0) / 1000.0
            except (TypeError, ValueError):
                duration = 0.0
            cover = (item.get("strTrackThumb") or item.get("strAlbumThumb") or "").strip()
            out.append(
                Candidate(
                    source=NAME,
                    title=title,
                    artist=(item.get("strArtist") or "").strip(),
                    album=(item.get("strAlbum") or "").strip(),
                    year=str(item.get("intYearReleased") or ""),
                    track=str(item.get("intTrackNumber") or ""),
                    duration=duration,
                    cover_urls=[cover] if cover else [],
                    raw_id=str(item.get("idTrack") or ""),
                )
            )
        return out

    def fetch_cover(self, cand: Candidate, min_size: int = 0) -> bytes | None:
        for url in cand.cover_urls:
            if not url:
                continue
            try:
                data = http.fetch_bytes(url)
                if data:
                    return data
            except Exception:
                continue
        return None

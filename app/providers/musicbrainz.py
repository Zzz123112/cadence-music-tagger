"""MusicBrainz + Cover Art Archive。合法、稳定，作为基准源。"""
from __future__ import annotations

from ..core.models import Candidate
from . import http
from .base import BaseProvider, ProviderError, SearchQuery, dedupe

SEARCH_URL = "https://musicbrainz.org/ws/2/recording"
COVER_URL = "https://coverartarchive.org/release/{mbid}/front-{size}"


class MusicBrainzProvider(BaseProvider):
    name = "MusicBrainz"

    def search(self, query: SearchQuery) -> list[Candidate]:
        terms = [query.title or query.keywords]
        if query.artist:
            terms.append(f'artist:"{query.artist}"')
        params = {
            "query": " AND ".join(t for t in terms if t),
            "fmt": "json",
            "limit": 12,
        }
        try:
            data = http.fetch_json(SEARCH_URL, params=params)
        except ProviderError:
            raise
        return dedupe(self.parse(data))

    @staticmethod
    def parse(data: dict) -> list[Candidate]:
        out: list[Candidate] = []
        for rec in data.get("recordings", []) or []:
            title = (rec.get("title") or "").strip()
            if not title:
                continue
            credits = rec.get("artist-credit") or []
            artist = "".join(
                (c.get("name") or c.get("artist", {}).get("name", "")) if isinstance(c, dict) else str(c)
                for c in credits
            ).strip()
            releases = rec.get("releases") or []
            album = ""
            year = ""
            release_id = ""
            track_no = ""
            if releases:
                best = releases[0]
                album = (best.get("title") or "").strip()
                year = (best.get("date") or "")[:4]
                release_id = best.get("id") or ""
                media = best.get("media") or []
                if media and media[0].get("track"):
                    track_no = str(media[0]["track"][0].get("number") or "")
            length = rec.get("length") or 0
            out.append(
                Candidate(
                    source="MusicBrainz",
                    title=title,
                    artist=artist,
                    album=album,
                    year=year,
                    track=track_no,
                    duration=round(float(length) / 1000.0, 2) if length else 0.0,
                    cover_urls=[COVER_URL.format(mbid=release_id, size=s) for s in (500, 1200)]
                    if release_id else [],
                    raw_id=rec.get("id", ""),
                )
            )
        return out

    def fetch_cover(self, cand: Candidate, min_size: int = 0) -> bytes | None:
        # Cover Art Archive 很慢（经常十几秒），只试一张、且超时压短，
        # 拿不到就算了，别把整批缩略图拖死。
        for url in cand.cover_urls[:1]:
            try:
                return http.fetch_bytes(url, timeout=4)
            except ProviderError:
                continue
        return None

"""华语源 B：QQ 音乐（公开搜索接口）。接口非官方，失败时静默降级。"""
from __future__ import annotations

from ..core.models import Candidate
from . import http
from .base import BaseProvider, ProviderError, SearchQuery, dedupe

SEARCH_URL = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
LYRIC_URL = "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"
HEADERS = {"Referer": "https://y.qq.com/", "User-Agent": "Mozilla/5.0"}


def _cover(album_mid: str, size: int = 500) -> str:
    if not album_mid:
        return ""
    return f"https://y.gtimg.cn/music/photo_new/T002R{size}x{size}M000{album_mid}.jpg"


class QQMusicProvider(BaseProvider):
    name = "QQ音乐"

    def search(self, query: SearchQuery) -> list[Candidate]:
        params = {
            "w": query.keywords,
            "p": 1,
            "n": 20,
            "format": "json",
            "cr": 1,
            "t": 0,
        }
        data = http.fetch_json(SEARCH_URL, params=params, headers=HEADERS)
        return dedupe(self.parse(data))

    @staticmethod
    def parse(data: dict) -> list[Candidate]:
        out: list[Candidate] = []
        song_node = ((data or {}).get("data") or {}).get("song") or {}
        for song in song_node.get("list") or []:
            title = (song.get("songname") or "").strip()
            if not title:
                continue
            singers = song.get("singer") or []
            artist = "、".join(
                (s.get("name") or "").strip() for s in singers if isinstance(s, dict)
            ).strip()
            album_mid = song.get("albummid") or ""
            out.append(
                Candidate(
                    source="QQ音乐",
                    title=title,
                    artist=artist,
                    album=(song.get("albumname") or "").strip(),
                    year="",
                    duration=float(song.get("interval") or 0),
                    cover_urls=[_cover(album_mid)] if album_mid else [],
                    raw_id=str(song.get("songmid") or ""),
                )
            )
        return out

    def fetch_lyrics(self, cand: Candidate) -> str | None:
        if not cand.raw_id:
            return None
        try:
            data = http.fetch_json(
                LYRIC_URL,
                params={"songmid": cand.raw_id, "format": "json", "nobase64": 1, "g_tk": 5381},
                headers=HEADERS,
            )
        except ProviderError:
            return None
        lyric = data.get("lyric") or ""
        return lyric.strip() or None

    def fetch_cover(self, cand: Candidate, min_size: int = 0) -> bytes | None:
        for url in cand.cover_urls:
            try:
                return http.fetch_bytes(url)
            except ProviderError:
                continue
        return None

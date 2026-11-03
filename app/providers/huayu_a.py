"""华语源 A：网易云音乐（公开搜索接口）。接口非官方，失败时静默降级。"""
from __future__ import annotations

from ..core.models import Candidate
from . import http
from .base import BaseProvider, ProviderError, SearchQuery, dedupe

SEARCH_URL = "https://music.163.com/api/cloudsearch/pc"
# 旧的 /api/search/get/web 已经被官方封掉（返回 405），这里留一个备用接口
FALLBACK_SEARCH_URL = "https://music.163.com/api/search/get"
LYRIC_URL = "https://music.163.com/api/song/lyric"
DETAIL_URL = "https://music.163.com/api/song/detail"
HEADERS = {"Referer": "https://music.163.com/", "User-Agent": "Mozilla/5.0"}


def _cover(url: str, size: int = 1000) -> str:
    if not url:
        return ""
    base = url.split("?")[0]
    return f"{base}?param={size}y{size}"


def parse_detail_cover(data: dict) -> str:
    """从歌曲详情里取专辑封面地址。

    搜索接口只给 picId（数字），拿不到图，必须再查一次详情。
    """
    songs = (data or {}).get("songs") or []
    if not songs:
        return ""
    album = songs[0].get("album") or songs[0].get("al") or {}
    return album.get("picUrl") or ""


class NeteaseProvider(BaseProvider):
    name = "网易云"

    def search(self, query: SearchQuery) -> list[Candidate]:
        params = {"s": query.keywords, "type": 1, "limit": 20, "offset": 0}
        try:
            data = http.fetch_json(SEARCH_URL, params=params, headers=HEADERS)
        except ProviderError:
            data = {}
        items = dedupe(self.parse(data))
        if items:
            return items
        # 主接口没结果时再试备用接口（两边字段略有不同，parse 都能吃）
        data = http.fetch_json(FALLBACK_SEARCH_URL, params=params, headers=HEADERS)
        return dedupe(self.parse(data))

    @staticmethod
    def parse(data: dict) -> list[Candidate]:
        out: list[Candidate] = []
        songs = ((data or {}).get("result") or {}).get("songs") or []
        for song in songs:
            title = (song.get("name") or "").strip()
            if not title:
                continue
            artists = song.get("artists") or song.get("ar") or []
            artist = "、".join(
                (a.get("name") or "").strip() for a in artists if isinstance(a, dict)
            ).strip()
            album = song.get("album") or song.get("al") or {}
            album_name = (album.get("name") or "").strip()
            pic = album.get("picUrl") or ""
            duration = song.get("duration") or song.get("dt") or 0
            out.append(
                Candidate(
                    source="网易云",
                    title=title,
                    artist=artist,
                    album=album_name,
                    year="",
                    duration=round(float(duration) / 1000.0, 2) if duration else 0.0,
                    cover_urls=[_cover(pic)] if pic else [],
                    raw_id=str(song.get("id") or ""),
                )
            )
        return out

    def fetch_lyrics(self, cand: Candidate) -> str | None:
        if not cand.raw_id:
            return None
        try:
            data = http.fetch_json(
                LYRIC_URL,
                params={"id": cand.raw_id, "lv": 1, "kv": 1, "tv": -1},
                headers=HEADERS,
            )
        except ProviderError:
            return None
        lyric = (data.get("lrc") or {}).get("lyric") or ""
        return lyric.strip() or None

    def resolve_cover_url(self, cand: Candidate) -> str:
        """搜索接口不给封面地址，按歌曲 id 再查一次详情。

        查到后写回候选，避免同一首歌反复请求。
        """
        if cand.cover_urls:
            return cand.cover_urls[0]
        if not cand.raw_id:
            return ""
        try:
            data = http.fetch_json(
                DETAIL_URL, params={"ids": f"[{cand.raw_id}]"}, headers=HEADERS
            )
        except ProviderError:
            return ""
        url = parse_detail_cover(data)
        if url:
            cand.cover_urls = [_cover(url)]
            return cand.cover_urls[0]
        return ""

    def fetch_cover(self, cand: Candidate, min_size: int = 0) -> bytes | None:
        url = self.resolve_cover_url(cand)
        if not url:
            return None
        try:
            return http.fetch_bytes(url)
        except ProviderError:
            return None

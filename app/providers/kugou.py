"""酷狗音乐：公开搜索接口，华语曲库很大。封面要再查一次专辑信息。"""
from __future__ import annotations

import base64

from ..core.models import Candidate
from . import http
from .base import BaseProvider, SearchQuery, dedupe

SEARCH_URL = "http://mobilecdn.kugou.com/api/v3/search/song"
ALBUM_URL = "http://mobilecdn.kugou.com/api/v3/album/info"
LYRIC_SEARCH_URL = "http://lyrics.kugou.com/search"
LYRIC_DOWNLOAD_URL = "http://lyrics.kugou.com/download"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "http://www.kugou.com/"}


class KugouProvider(BaseProvider):
    name = "酷狗"

    def search(self, query: SearchQuery) -> list[Candidate]:
        params = {
            "format": "json",
            "keyword": query.keywords,
            "page": 1,
            "pagesize": 15,
        }
        data = http.fetch_json(SEARCH_URL, params=params, headers=HEADERS)
        return dedupe(self.parse(data))

    @staticmethod
    def parse(data: dict) -> list[Candidate]:
        out: list[Candidate] = []
        info = ((data or {}).get("data") or {}).get("info") or []
        for item in info:
            if not isinstance(item, dict):
                continue
            title = (item.get("songname") or "").strip()
            if not title:
                continue
            try:
                duration = float(item.get("duration") or 0)
            except (TypeError, ValueError):
                duration = 0.0
            album_id = str(item.get("album_id") or "")
            out.append(
                Candidate(
                    source="酷狗",
                    title=title,
                    artist=(item.get("singername") or "").strip(),
                    album=(item.get("album_name") or "").strip(),
                    duration=duration,
                    quality_hints=(item.get("extname") or "").upper(),
                    raw_id=str(item.get("hash") or album_id),
                )
            )
            if album_id:
                # 专辑 id 单独存一份，取封面时要用
                out[-1].cover_urls = [f"kugou-album:{album_id}"]
        return out

    def fetch_cover(self, cand: Candidate, min_size: int = 0) -> bytes | None:
        for marker in cand.cover_urls:
            if not str(marker).startswith("kugou-album:"):
                continue
            album_id = str(marker).split(":", 1)[1]
            try:
                data = http.fetch_json(
                    ALBUM_URL, params={"albumid": album_id, "plat": 0, "mode": 3},
                    headers=HEADERS,
                )
            except Exception:
                continue
            img = ((data or {}).get("data") or {}).get("imgurl") or ""
            if not img:
                continue
            url = img.replace("{size}", "480")
            try:
                return http.fetch_bytes(url, timeout=5)
            except Exception:
                continue
        return None

    def fetch_lyrics(self, cand: Candidate) -> str | None:
        """酷狗歌词：先按 hash 搜，再按 id + accesskey 下 LRC。"""
        if not cand.raw_id:
            return None
        try:
            found = http.fetch_json(
                LYRIC_SEARCH_URL,
                params={
                    "ver": 1, "man": "yes", "client": "pc",
                    "keyword": f"{cand.artist} - {cand.title}",
                    "duration": "", "hash": cand.raw_id,
                },
                headers=HEADERS,
            )
        except Exception:
            return None
        candidates = (found or {}).get("candidates") or []
        if not candidates:
            return None
        best = candidates[0]
        try:
            data = http.fetch_json(
                LYRIC_DOWNLOAD_URL,
                params={
                    "ver": 1, "client": "pc", "id": best.get("id"),
                    "accesskey": best.get("accesskey"),
                    "fmt": "lrc", "charset": "utf8",
                },
                headers=HEADERS,
            )
        except Exception:
            return None
        content = (data or {}).get("content") or ""
        if not content:
            return None
        try:
            text = base64.b64decode(content).decode("utf-8", "ignore")
        except Exception:
            return None
        return text.strip() or None

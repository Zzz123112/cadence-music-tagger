"""酷我音乐：公开搜索接口。返回的是非标准 JSON（单引号），用 literal_eval 解析。"""
from __future__ import annotations

import ast

from ..core.models import Candidate
from . import http
from .base import BaseProvider, SearchQuery, dedupe

SEARCH_URL = "http://search.kuwo.cn/r.s"
LYRIC_URL = "https://m.kuwo.cn/newh5/singles/songinfoandlrc"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "http://www.kuwo.cn/"}
COVER_URL = "https://img1.kuwo.cn/star/albumcover/{}.jpg"


def _parse_loose(text: str) -> dict:
    """酷我返回单引号 JSON，标准 json 解析不了。"""
    cleaned = (text or "").replace("&nbsp;", " ").strip()
    if not cleaned:
        return {}
    try:
        value = ast.literal_eval(cleaned)
    except (ValueError, SyntaxError):
        return {}
    return value if isinstance(value, dict) else {}


class KuwoProvider(BaseProvider):
    name = "酷我"

    def search(self, query: SearchQuery) -> list[Candidate]:
        params = {
            "all": query.keywords,
            "ft": "music",
            "itemset": "web_2013",
            "client": "kt",
            "pn": 0,
            "rn": 15,
            "rformat": "json",
            "encoding": "utf8",
        }
        text = http.fetch_text(SEARCH_URL, params=params, headers=HEADERS)
        return dedupe(self.parse(text))

    @staticmethod
    def parse(text: str) -> list[Candidate]:
        data = _parse_loose(text)
        out: list[Candidate] = []
        for item in data.get("abslist") or []:
            if not isinstance(item, dict):
                continue
            title = (item.get("SONGNAME") or "").strip()
            if not title:
                continue
            try:
                duration = float(item.get("DURATION") or 0)
            except (TypeError, ValueError):
                duration = 0.0
            album_id = str(item.get("ALBUMID") or "")
            covers: list[str] = []
            pic = (item.get("web_albumpic_short") or item.get("PICPATH") or "").strip()
            if pic:
                covers.append(pic if pic.startswith("http") else "https://img1.kuwo.cn" + pic)
            elif album_id and album_id != "0":
                covers.append(COVER_URL.format(album_id))
            out.append(
                Candidate(
                    source="酷我",
                    title=title,
                    artist=(item.get("ARTIST") or "").strip(),
                    album=(item.get("ALBUM") or "").strip(),
                    duration=duration,
                    quality_hints=(item.get("FORMATS") or "").strip(),
                    cover_urls=covers,
                    raw_id=str(item.get("MUSICRID") or ""),
                )
            )
        return out

    def fetch_cover(self, cand: Candidate, min_size: int = 0) -> bytes | None:
        for url in cand.cover_urls:
            if not url or not url.startswith("http"):
                continue
            try:
                data = http.fetch_bytes(url)
                if data:
                    return data
            except Exception:
                continue
        return None

    def fetch_lyrics(self, cand: Candidate) -> str | None:
        """酷我歌词：songinfoandlrc 一次返回带时间轴的逐行歌词。"""
        rid = str(cand.raw_id or "").replace("MUSIC_", "").strip()
        if not rid:
            return None
        try:
            data = http.fetch_json(LYRIC_URL, params={"musicId": rid},
                                   headers=HEADERS)
        except Exception:
            return None
        lines = ((data or {}).get("data") or {}).get("lrclist") or []
        out: list[str] = []
        for item in lines:
            if not isinstance(item, dict):
                continue
            text = (item.get("lineLyric") or "").strip()
            try:
                seconds = float(item.get("time"))
            except (TypeError, ValueError):
                continue
            minutes, rest = divmod(max(0.0, seconds), 60)
            out.append(f"[{int(minutes):02d}:{rest:05.2f}]{text}")
        return "\n".join(out).strip() or None

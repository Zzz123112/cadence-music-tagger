"""Audius：去中心化音乐平台，公开 API 不需要密钥，独立音乐与翻唱居多。"""
from __future__ import annotations

from ..core.models import Candidate
from . import http
from .base import BaseProvider, SearchQuery, dedupe

SEARCH_URL = "https://discoveryprovider.audius.co/v1/tracks/search"
NAME = "Audius"
APP_NAME = "Cadence"


class AudiusProvider(BaseProvider):
    name = NAME

    def search(self, query: SearchQuery) -> list[Candidate]:
        params = {"query": query.keywords, "app_name": APP_NAME}
        data = http.fetch_json(SEARCH_URL, params=params)
        return dedupe(self.parse(data))

    @staticmethod
    def parse(data: dict) -> list[Candidate]:
        out: list[Candidate] = []
        for item in (data or {}).get("data") or []:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or "").strip()
            if not title:
                continue
            user = item.get("user") or {}
            artwork = item.get("artwork") or {}
            cover = (artwork.get("1000x1000") or artwork.get("480x480")
                     or artwork.get("150x150") or "")
            try:
                duration = float(item.get("duration") or 0)
            except (TypeError, ValueError):
                duration = 0.0
            out.append(
                Candidate(
                    source=NAME,
                    title=title,
                    artist=(user.get("name") or user.get("handle") or "").strip(),
                    album=(item.get("album_backlink") or {}).get("title", "") if isinstance(
                        item.get("album_backlink"), dict) else "",
                    duration=duration,
                    quality_hints=(item.get("genre") or "").strip(),
                    cover_urls=[cover] if cover else [],
                    raw_id=str(item.get("id") or ""),
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

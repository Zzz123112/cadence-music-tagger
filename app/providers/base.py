"""数据源抽象。各源独立实现，结果永不合并（PRD A-05）。"""
from __future__ import annotations

from dataclasses import dataclass

from ..core.models import Candidate


class ProviderError(Exception):
    """单个源失败，不向上抛，由调用方降级。"""


@dataclass
class SearchQuery:
    keywords: str
    artist: str = ""
    title: str = ""


class BaseProvider:
    """所有数据源的基类。子类只实现 search，其余可选。"""

    name: str = "base"
    enabled: bool = True

    def search(self, query: SearchQuery) -> list[Candidate]:
        raise NotImplementedError

    def fetch_cover(self, cand: Candidate, min_size: int = 0) -> bytes | None:
        return None

    def fetch_lyrics(self, cand: Candidate) -> str | None:
        """默认实现：部分源（如 MusicBrainz）本身不提供歌词。"""
        return cand.lyrics or None

    def available(self) -> bool:
        return self.enabled


def dedupe(cands: list[Candidate]) -> list[Candidate]:
    """同源内去重：同一首歌被重复返回时只留第一条。"""
    seen: set[tuple[str, str, str]] = set()
    out: list[Candidate] = []
    for c in cands:
        key = (c.title.strip().lower(), c.artist.strip().lower(), c.album.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out

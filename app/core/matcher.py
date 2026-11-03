"""匹配编排：查源、算证据、分组、落缓存。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..providers import search_all
from ..providers.base import SearchQuery
from . import db, evidence
from .context import album_context
from .models import AlbumContext, Candidate, Track
from .parse_name import build_keywords, parse_name, variants


@dataclass
class MatchResult:
    track_id: int
    candidates: list[Candidate] = field(default_factory=list)
    groups: list[list[Candidate]] = field(default_factory=list)
    context: AlbumContext = field(default_factory=AlbumContext)
    errors: dict[str, str] = field(default_factory=dict)
    from_cache: bool = False

    @property
    def same_name_groups(self) -> list[list[Candidate]]:
        return [g for g in self.groups if len(g) >= 2]


def rank(track: Track, cands: list[Candidate], ctx: AlbumContext | None = None) -> list[Candidate]:
    """按六项证据排序。"""
    return evidence.rank(track, cands, ctx)


def group_by_title(cands: list[Candidate]) -> list[list[Candidate]]:
    """按归一化歌名分组，保持首次出现顺序。"""
    buckets: dict[str, list[Candidate]] = {}
    order: list[str] = []
    for cand in cands:
        key = evidence.normalize(cand.title)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(cand)
    return [buckets[k] for k in order]


def same_name_groups(cands: list[Candidate]) -> list[list[Candidate]]:
    return [g for g in group_by_title(cands) if len(g) >= 2]


def build_query(track: Track) -> SearchQuery:
    parsed = parse_name(track.name or track.path)
    return SearchQuery(
        keywords=build_keywords(parsed),
        artist=parsed.artist,
        title=parsed.title,
    )


def query_track(track: Track, providers) -> tuple[list[Candidate], dict[str, str]]:
    """向所有源查询。返回 (候选, 失败源)。"""
    query = build_query(track)
    results, errors = search_all(query, providers)
    cands: list[Candidate] = []
    for source, items in results.items():
        for item in items:
            item.source = item.source or source
            cands.append(item)
    return cands, errors


def match_track(
    conn,
    track: Track,
    providers,
    force: bool = False,
) -> MatchResult:
    """查一次（或读缓存），算证据，分组。"""
    folder = db.folder_tracks(conn, os.path.dirname(os.path.abspath(track.path)))
    ctx = album_context(track, folder)

    result = MatchResult(track_id=track.id or 0, context=ctx)
    cached = [] if force else db.load_candidates(conn, track.id or 0)
    if cached:
        result.candidates = cached
        result.from_cache = True
    else:
        cands, errors = query_track(track, providers)
        result.errors = errors
        evidence.rank(track, cands, ctx)
        result.candidates = cands
        if track.id:
            db.replace_candidates(conn, track.id, cands)
            conn.commit()

    evidence.rank(track, result.candidates, ctx)
    result.groups = group_by_title(result.candidates)
    return result


def prematch(
    conn,
    tracks: list[Track],
    providers,
    progress=None,
    should_stop=None,
) -> dict:
    """批量预匹配。可暂停，可只补缺失。"""
    stats = {"total": len(tracks), "matched": 0, "skipped": 0, "failed": 0}
    for index, track in enumerate(tracks, start=1):
        if should_stop and should_stop():
            break
        if not track.id:
            stats["skipped"] += 1
            continue
        if db.has_candidates(conn, track.id):
            stats["skipped"] += 1
        else:
            try:
                match_track(conn, track, providers, force=True)
                stats["matched"] += 1
            except Exception:
                stats["failed"] += 1
        if progress and (index % 5 == 0 or index == len(tracks)):
            progress(index, len(tracks))
    conn.commit()
    return stats


def remember_choice(conn, track: Track, cand: Candidate) -> None:
    """把人工选定的结果记进纠正记忆，下次同名文件优先。"""
    query = build_query(track)
    db.remember_correction(conn, query.keywords, cand)
    conn.commit()


def cached_for_correction(conn, track: Track) -> Candidate | None:
    query = build_query(track)
    return db.lookup_correction(conn, query.keywords)


def search_variants(providers, track: Track) -> tuple[list[Candidate], dict[str, str]]:
    """手动检索：按变体关键词依次尝试，直到拿到结果。"""
    parsed = parse_name(track.name or track.path)
    all_errors: dict[str, str] = {}
    for keyword in variants(parsed):
        results, errors = search_all(SearchQuery(keywords=keyword), providers)
        all_errors.update(errors)
        cands = [c for items in results.values() for c in items]
        if cands:
            evidence.rank(track, cands)
            return cands, all_errors
    return [], all_errors

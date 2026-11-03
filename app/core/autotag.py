"""批量自动打标签。

策略（按用户要求调整）：
- 只要有任何一项证据对得上（默认 ≥1/6）就直接写入，不再留给人工
- 一项都对不上（0/6）才跳过，并记在 skipped 里
- 写入仍然走「写入 → 校验」，失败会报出来
- 批量只写文本字段（歌名/歌手/专辑/年份/曲序），封面与歌词仍由人工挑
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import db, evidence, matcher
from . import lyrics as lyrics
from .context import album_context
from .models import Candidate, Track
from ..write.service import PendingWrite

TEXT_FIELDS = ("title", "artist", "album", "year", "track")

# 歌词处理方式
LYRICS_NONE = "none"      # 不处理
LYRICS_TAG = "tag"        # 只写入标签
LYRICS_FILE = "file"      # 只存成 .lrc 文件
LYRICS_BOTH = "both"      # 两者都做

# 封面处理方式
COVER_NONE = "none"       # 不处理
COVER_TAG = "tag"         # 只写进文件标签
COVER_FILE = "file"       # 只存成 cover.jpg / cover.png
COVER_BOTH = "both"       # 两者都做


def fetch_cover_for(cand: Candidate, providers, data_dir: str = "",
                    alternatives: list[Candidate] | None = None) -> bytes | None:
    """取封面字节：先看缓存，再问对应数据源。

    alternatives 是同一首歌的其它候选：主候选所在源没有封面时
    （比如酷我），借用别家同一首歌的封面，避免批量打完没有图。
    """
    from . import covercache

    def _from_cache(c: Candidate) -> bytes | None:
        if not data_dir:
            return None
        try:
            return covercache.get(data_dir, c)
        except Exception:
            return None

    def _fetch(c: Candidate) -> bytes | None:
        provider = next((p for p in providers if p.name == c.source), None)
        if provider is None:
            return None
        try:
            data = provider.fetch_cover(c)
        except Exception:
            return None
        if data and data_dir:
            try:
                covercache.put(data_dir, c, data)
            except Exception:
                pass
        return data or None

    for candidate in [cand] + list(alternatives or []):
        data = _from_cache(candidate) or _fetch(candidate)
        if data:
            return data
    return None


@dataclass
class AutoTagOutcome:
    total: int = 0
    written: int = 0
    inferred: int = 0
    similar: int = 0
    skipped: int = 0
    failed: int = 0
    review: list[tuple[str, str]] = field(default_factory=list)
    pendings: list[PendingWrite] = field(default_factory=list)
    lyrics_map: dict[str, str] = field(default_factory=dict)
    lyrics_saved: int = 0
    lyrics_tagged: int = 0
    covers: int = 0

    def summary_text(self) -> str:
        parts = [f"共 {self.total} 首", f"自动写入 {self.written}"]
        if self.inferred:
            parts.append(f"其中推断 {self.inferred}")
        if self.similar:
            parts.append(f"低证据 {self.similar}")
        if self.covers:
            parts.append(f"封面 {self.covers}")
        if self.lyrics_tagged:
            parts.append(f"歌词写入标签 {self.lyrics_tagged}")
        if self.lyrics_saved:
            parts.append(f"歌词文件 {self.lyrics_saved}")
        if self.skipped:
            parts.append(f"跳过 {self.skipped}")
        if self.review:
            parts.append(f"出错 {len(self.review)}")
        if self.failed:
            parts.append(f"失败 {self.failed}")
        return "，".join(parts)


def fetch_lyrics_for(track: Track, candidates: list[Candidate], providers) -> str:
    """从候选里找一段带时间轴的歌词，找不到就退回最长的纯文本。"""
    best = ""
    for cand in candidates[:8]:
        provider = next((p for p in providers if p.name == cand.source), None)
        if provider is None:
            continue
        try:
            text = provider.fetch_lyrics(cand)
        except Exception:
            text = None
        if not text or len(text.strip()) < 10:
            continue
        if lyrics.has_timeline(text):
            return text
        if len(text) > len(best):
            best = text
    return best


def pick_candidate(track: Track, candidates: list[Candidate], ctx_album) -> Candidate:
    """从候选里挑最适合写入的一条。

    排序优先级：歌手一致 > 歌名完全一致 > 时长一致 > 吻合项 > 分数。
    这样同一首歌有原唱和翻唱时不会把翻唱写进去，
    带「(mp3.2)」这类后缀的版本也会让位给干净的完全同名版本。
    """
    def key(cand: Candidate):
        ins = cand.evidence or evidence.evaluate(track, cand, ctx_album)
        hits = sum(1 for value in ins.values() if value >= 0.75)
        return (
            ins.get("artist", 0.0) >= 0.75,
            ins.get("title", 0.0) >= 0.99,
            ins.get("duration", 0.0) >= 0.75,
            hits,
            cand.score or 0.0,
        )

    return max(candidates, key=key)


def decide(
    track: Track,
    candidates: list[Candidate],
    ctx_album,
    min_hits: int = 1,
) -> tuple[dict[str, str] | None, str, str]:
    """返回 (可写字段, 不写的理由, 级别)。

    级别：
      exact    —— 歌名与歌手都对上
      inferred —— 本地没有歌手信息，靠「歌名完全一致」推断
      similar  —— 歌名对得上，但其它证据不全（按用户要求也直接写）
      ""       —— 不写（歌名都对不上）
    """
    if not candidates:
        return None, "没有候选", ""

    best = pick_candidate(track, candidates, ctx_album)
    insight = best.evidence or evidence.evaluate(track, best, ctx_album)
    hits = sum(1 for value in insight.values() if value >= 0.75)
    title_score = insight.get("title", 0.0)

    # 歌名对不上就是另一首歌，不能瞎写；
    # 歌名对得上（完全一致或包含）就直接采用，不再要求凑够 4 项。
    if title_score < 0.6:
        return None, "歌名对不上", ""
    if min_hits > 1 and hits < min_hits:
        return None, f"证据不足（{hits}/6）", ""

    has_artist_info = bool((track.parsed_artist or track.tag_artist).strip())
    title_exact = title_score >= 0.99
    artist_ok = insight.get("artist", 0.0) >= 0.75
    if has_artist_info and title_exact and artist_ok:
        level = "exact"
    elif not has_artist_info and title_exact:
        level = "inferred"
    else:
        level = "similar"

    fields = {
        key: str(getattr(best, key))
        for key in TEXT_FIELDS
        if getattr(best, key, "")
    }
    if not fields:
        return None, "候选没有可用字段", ""
    return fields, "", level


def plan(
    conn,
    tracks: list[Track],
    providers,
    min_hits: int = 1,
    use_cache: bool = True,
    progress=None,
    fields: set[str] | None = None,
    lyrics_mode: str = LYRICS_NONE,
    cover_mode: str = COVER_NONE,
    data_dir: str = "",
    workers: int = 6,
) -> AutoTagOutcome:
    """只做匹配与判断，不写文件。

    fields 不为空时，只保留用户勾选的字段（歌名/歌手/专辑/年份/曲序）。
    lyrics_mode 决定歌词怎么处理：不处理 / 写标签 / 存 .lrc / 两者都做。
    cover_mode 决定封面怎么处理：不处理 / 写入标签 / 存成 cover 文件 / 两者都做。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    outcome = AutoTagOutcome(total=len(tracks))
    if not tracks:
        return outcome

    # 主线程先备好上下文与缓存，线程里只做网络和打分
    prepared: list[dict] = []
    for track in tracks:
        folder = db.folder_tracks(conn, os.path.dirname(track.path))
        prepared.append({
            "track": track,
            "ctx": album_context(track, folder),
            "cached": db.load_candidates(conn, track.id or 0) if use_cache else [],
        })

    def _query(item: dict):
        track = item["track"]
        candidates = item["cached"]
        if not candidates:
            candidates, _errors = matcher.query_track(track, providers)
        evidence.rank(track, candidates, item["ctx"])
        return candidates

    done = 0
    total = len(tracks)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, total))) as pool:
        futures = {pool.submit(_query, item): item for item in prepared}
        for future in as_completed(futures):
            item = futures[future]
            track = item["track"]
            done += 1
            try:
                candidates = future.result()
            except Exception as exc:
                outcome.failed += 1
                outcome.review.append((track.name, f"出错：{exc}"))
                if progress:
                    progress(done, total)
                continue

            try:
                if track.id and not item["cached"]:
                    db.replace_candidates(conn, track.id, candidates)

                chosen, reason, level = decide(track, candidates, item["ctx"], min_hits)
                if chosen and fields:
                    chosen = {k: v for k, v in chosen.items() if k in fields}
                    if not chosen:
                        reason = "所选字段没有值"

                lyric_text = ""
                if lyrics_mode != LYRICS_NONE and candidates:
                    lyric_text = fetch_lyrics_for(track, candidates, providers)

                wants_file = lyrics_mode in (LYRICS_FILE, LYRICS_BOTH)
                wants_tag = lyrics_mode in (LYRICS_TAG, LYRICS_BOTH)

                cover_bytes = None
                if cover_mode != COVER_NONE and candidates:
                    picked = pick_candidate(track, candidates, item["ctx"])
                    # 同名的其它候选作为封面备选（酷我经常没有专辑图）
                    alts = [c for c in candidates
                            if c is not picked
                            and evidence.normalize(c.title) == evidence.normalize(picked.title)][:3]
                    cover_bytes = fetch_cover_for(picked, providers, data_dir, alts)

                if chosen or cover_bytes or (lyric_text and wants_file):
                    pending = PendingWrite(
                        track=track,
                        fields=chosen or {},
                        cover=cover_bytes,
                        lyrics=lyric_text if (wants_tag and lyric_text) else None,
                    )
                    outcome.pendings.append(pending)
                    if cover_bytes:
                        outcome.covers += 1
                    if wants_tag and lyric_text:
                        outcome.lyrics_tagged += 1
                    if level == "inferred":
                        outcome.inferred += 1
                    elif level == "similar":
                        outcome.similar += 1
                    if lyric_text and wants_file:
                        outcome.lyrics_map[track.path] = lyric_text
                else:
                    # 一项证据都对不上：直接跳过，不丢给人工
                    outcome.skipped += 1
            except Exception as exc:
                outcome.failed += 1
                outcome.review.append((track.name, f"出错：{exc}"))

            if progress:
                progress(done, total)
    conn.commit()
    return outcome


def run(
    conn,
    tracks: list[Track],
    providers,
    library_root: str,
    min_hits: int = 1,
    use_cache: bool = True,
    progress=None,
    fallback_dir: str = "",
    fields: set[str] | None = None,
    lyrics_mode: str = LYRICS_NONE,
    cover_mode: str = COVER_NONE,
    allow_elevation: bool = False,
) -> AutoTagOutcome:
    """匹配 + 判断 + 一次性写入。

    批量默认不弹 UAC（否则每首都会弹一次）；单首保存才允许申请提权。
    """
    from ..write import service
    from ..write.writer import WriteOptions

    outcome = plan(conn, tracks, providers, min_hits=min_hits,
                   use_cache=use_cache, progress=progress, fields=fields,
                   lyrics_mode=lyrics_mode, cover_mode=cover_mode,
                   data_dir=fallback_dir)
    if not outcome.pendings and not outcome.lyrics_map:
        return outcome

    if outcome.pendings:
        opts = WriteOptions(
            write_cover=cover_mode in (COVER_TAG, COVER_BOTH),
            replace_cover=True,
            write_cover_file=cover_mode in (COVER_FILE, COVER_BOTH),
        )
        summary = service.write_batch(conn, outcome.pendings, library_root,
                                      opts=opts,
                                      fallback_dir=fallback_dir,
                                      allow_elevation=allow_elevation)
        outcome.written = summary["ok"]
        outcome.failed += summary["failed"]
        outcome.covers = summary.get("cover_files", outcome.covers)
        if summary.get("error"):
            outcome.review.append(("（整批）", summary["error"]))
            return outcome

    # 单独写 .lrc（写标签那部分已经由 write_batch 处理）
    for path, text in outcome.lyrics_map.items():
        try:
            lyrics.save_lrc(path, text, overwrite=True)
            outcome.lyrics_saved += 1
        except PermissionError:
            outcome.review.append((os.path.basename(path), "歌词无法写入音乐文件夹"))
        except Exception as exc:
            outcome.review.append((os.path.basename(path), f"歌词保存失败：{exc}"))
    return outcome


"""同专辑上下文推断：利用本地库里已经确认过的曲目反推当前曲目。"""
from __future__ import annotations

import os
from collections import Counter

from .evidence import normalize, split_artists
from .models import AlbumContext, Track


def folder_of(path: str) -> str:
    return os.path.dirname(os.path.abspath(path))


def album_context(track: Track, folder_tracks: list[Track]) -> AlbumContext:
    """统计同目录其他曲目的专辑共识。

    只用有专辑标签的曲目参与投票；分母是目录内曲目总数，
    这样"8 首里 7 首属于同一张专辑"才是真实的 0.875。
    """
    others = [t for t in folder_tracks if os.path.abspath(t.path) != os.path.abspath(track.path)]
    votes = [t for t in others if t.tag_album]
    total = len(folder_tracks) if folder_tracks else 0
    if not votes:
        return AlbumContext(total=total)

    counter = Counter(normalize(t.tag_album) for t in votes)
    key, count = counter.most_common(1)[0]
    sample = next(t for t in votes if normalize(t.tag_album) == key)

    # 歌手共识：专辑相同的那批人里最常见的歌手
    artists = Counter()
    for t in votes:
        if normalize(t.tag_album) == key and t.tag_artist:
            artists[normalize(t.tag_artist)] += 1
    artist = ""
    if artists:
        artist_key = artists.most_common(1)[0][0]
        artist = next(t.tag_artist for t in votes if normalize(t.tag_artist) == artist_key)

    return AlbumContext(album=sample.tag_album, artist=artist, hit=count, total=total)


def candidate_matches_context(cand_album: str, ctx: AlbumContext) -> bool:
    return bool(ctx.usable) and normalize(cand_album) == normalize(ctx.album)


def artist_matches_context(cand_artist: str, ctx: AlbumContext) -> bool:
    if not ctx.usable or not ctx.artist:
        return False
    return bool(split_artists(cand_artist) & split_artists(ctx.artist))

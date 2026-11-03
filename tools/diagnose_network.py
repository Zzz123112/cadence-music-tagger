"""诊断脚本：在真实音乐库上检查三个源的搜索、封面、歌词是否可用。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import db, evidence, matcher, scan
from app.core.context import album_context
from app.core.probe import probe
from app.providers import build_providers
from app.providers.base import ProviderError, SearchQuery


def main(folder: str, limit: int = 3) -> int:
    db_path = os.path.join(os.environ.get("TEMP", "."), "mt-diag.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = db.connect(db_path)

    stats = scan.scan(conn, folder)
    print(f"扫描 {stats['total']} 个文件\n")

    tracks = db.list_tracks(conn)[:limit]
    providers = build_providers()

    for track in tracks:
        print("=" * 70)
        print(f"文件：{track.name}")
        print(f"  格式 {track.fmt} / {track.quality_label()} / 时长 {track.duration:.1f}s")
        print(f"  解析：title={track.parsed_title!r} artist={track.parsed_artist!r}")

        query = matcher.build_query(track)
        print(f"  查询词：{query.keywords!r}")

        for provider in providers:
            print(f"\n  --- {provider.name} ---")
            try:
                cands = provider.search(SearchQuery(keywords=query.keywords,
                                                    artist=query.artist,
                                                    title=query.title))
            except ProviderError as exc:
                print(f"    搜索失败：{exc}")
                continue
            except Exception as exc:
                print(f"    搜索异常：{type(exc).__name__}: {exc}")
                continue

            print(f"    结果 {len(cands)} 条")
            for cand in cands[:2]:
                print(f"      · {cand.title} / {cand.artist} / {cand.album} "
                      f"/ {cand.duration:.0f}s / id={cand.raw_id!r}")
                print(f"        封面URL: {cand.cover_urls[:2]}")
                try:
                    data = provider.fetch_cover(cand)
                    print(f"        封面下载: {'OK ' + str(len(data)) + ' bytes' if data else '返回 None'}")
                except Exception as exc:
                    print(f"        封面下载异常: {type(exc).__name__}: {exc}")
                if cand.raw_id:
                    try:
                        text = provider.fetch_lyrics(cand)
                        print(f"        歌词: {'OK ' + str(len(text)) + ' 字符' if text else '返回 None'}")
                    except AttributeError:
                        print("        歌词: 该源没有实现 fetch_lyrics")
                    except Exception as exc:
                        print(f"        歌词异常: {type(exc).__name__}: {exc}")
    conn.close()
    return 0


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else r"F:\音乐"
    raise SystemExit(main(target))

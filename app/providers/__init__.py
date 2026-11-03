"""数据源注册与并发查询。结果按源分开返回，永不合并。"""
from __future__ import annotations

import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from ..core.models import Candidate
from .audiodb import AudioDbProvider
from .audius import AudiusProvider
from .base import BaseProvider, ProviderError, SearchQuery
from .huayu_a import NeteaseProvider
from .huayu_b import QQMusicProvider
from .itunes import ItunesProvider
from .kugou import KugouProvider
from .kuwo import KuwoProvider
from .musicbrainz import MusicBrainzProvider

ALL_PROVIDERS = [
    MusicBrainzProvider,
    NeteaseProvider,
    QQMusicProvider,
    KugouProvider,
    KuwoProvider,
    ItunesProvider,
    AudioDbProvider,
    AudiusProvider,
]

PROVIDER_NAMES = [cls.name for cls in ALL_PROVIDERS]


def build_providers(enabled: dict[str, bool] | None = None) -> list[BaseProvider]:
    providers: list[BaseProvider] = []
    for cls in ALL_PROVIDERS:
        instance = cls()
        if enabled is not None and not enabled.get(instance.name, True):
            continue
        providers.append(instance)
    return providers


def search_all(
    query: SearchQuery,
    providers: list[BaseProvider],
    timeout: float = 5.0,
    on_result=None,
    cancel=None,
) -> tuple[dict[str, list[Candidate]], dict[str, str]]:
    """并发查询所有源。

    返回 (按源分组的候选, 失败源与原因)。单源失败不影响其他源。

    传了 on_result 时，每完成一个源就回调一次 on_result(源名, 候选, 错误)，
    界面可以边查边显示，不用等最慢的那个源。

    cancel 是一个 threading.Event：用户换了歌就把旧的搜索取消，
    免得慢请求越堆越多把后面的查询一起拖住。
    """
    results: dict[str, list[Candidate]] = {}
    errors: dict[str, str] = {}
    if not providers:
        return results, errors

    def cancelled() -> bool:
        return cancel is not None and cancel.is_set()

    pool = ThreadPoolExecutor(max_workers=len(providers))
    futures = {pool.submit(p.search, query): p for p in providers}
    deadline = time.monotonic() + timeout
    try:
        while futures and not cancelled():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            done, _pending = wait(list(futures), timeout=min(remaining, 0.2),
                                  return_when=FIRST_COMPLETED)
            for future in done:
                provider = futures.pop(future)
                try:
                    items = future.result()
                except ProviderError as exc:
                    errors[provider.name] = str(exc)
                    items = []
                except Exception as exc:  # 任何源崩了都不该拖垮整次查询
                    errors[provider.name] = f"{type(exc).__name__}: {exc}"
                    items = []
                results[provider.name] = items
                if on_result is not None:
                    try:
                        on_result(provider.name, items, errors.get(provider.name, ""))
                    except Exception:
                        pass
    finally:
        # 不等慢源收尾：wait=False，立刻把控制权还回去
        pool.shutdown(wait=False)

    for provider in providers:
        results.setdefault(provider.name, [])
        if provider.name not in results:
            errors.setdefault(provider.name, "超时")
    return results, errors


def flatten(results: dict[str, list[Candidate]]) -> list[Candidate]:
    """把所有源的候选摊平（仅用于排序展示，来源仍需保留）。"""
    out: list[Candidate] = []
    for source, cands in results.items():
        for c in cands:
            c.source = c.source or source
            out.append(c)
    return out

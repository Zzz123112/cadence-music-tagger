"""HTTP 层：超时、重试、限速。所有网络请求都从这里走。

注意：网络库是「可选」的。某些机器上 SSL/证书环境异常会让
`import requests` 直接抛错；如果这里不兜住，整个程序连界面都起不来。
兜住之后最差情况是「能打开、能整理本地文件，只是搜索提示网络不可用」。
"""
from __future__ import annotations

import json
import threading
import time
from urllib.parse import urlparse

from .base import ProviderError

try:
    import requests
    from requests import Session as _Session
except Exception as _exc:  # pragma: no cover - 只在环境异常时走到
    requests = None  # type: ignore[assignment]
    _Session = None  # type: ignore[assignment]
    _IMPORT_ERROR = f"{type(_exc).__name__}: {_exc}"
else:
    _IMPORT_ERROR = ""

USER_AGENT = "MusicTagger/0.1 (local app; https://example.invalid)"
DEFAULT_TIMEOUT = 8
MAX_RETRIES = 2
MIN_INTERVAL = 0.3  # 接口请求之间的最小间隔（怕被限流）
IMAGE_INTERVAL = 0.02  # 图片下载限制放宽，否则 6 张封面要等将近 2 秒

if _Session is not None:
    _session = _Session()
    _session.headers.update({"User-Agent": USER_AGENT})
else:
    _session = None
_lock = threading.Lock()
_last_by_host: dict[str, float] = {}
_last_image = 0.0
_offline = False


def network_available() -> bool:
    return _session is not None


def network_error() -> str:
    return _IMPORT_ERROR


def set_offline(flag: bool) -> None:
    """测试或断网演练时切断网络。"""
    global _offline
    _offline = flag


def is_offline() -> bool:
    return _offline


def _throttle(url: str = "") -> None:
    """按域名限速。

    以前是一个全局时间戳，八个源会排成一队互相等，
    每次点击都要多花好几秒；按域名分开之后各查各的。
    """
    host = urlparse(url).netloc or "_"
    with _lock:
        last = _last_by_host.get(host, 0.0)
        wait = MIN_INTERVAL - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
        _last_by_host[host] = time.monotonic()


def _throttle_image() -> None:
    global _last_image
    with _lock:
        wait = IMAGE_INTERVAL - (time.monotonic() - _last_image)
        if wait > 0:
            time.sleep(wait)
        _last_image = time.monotonic()


def fetch_json(url: str, params: dict | None = None,
               headers: dict | None = None, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """GET 一个 JSON。失败重试 MAX_RETRIES 次，最终抛 ProviderError。"""
    if _session is None:
        raise ProviderError(f"网络模块不可用（{_IMPORT_ERROR or 'unknown'}）")
    if _offline:
        raise ProviderError("offline")
    last_error = ""
    for attempt in range(MAX_RETRIES + 1):
        _throttle(url)
        try:
            resp = _session.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            last_error = f"HTTP {resp.status_code}"
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(1.5 * (attempt + 1))
                continue
            break
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.8 * (attempt + 1))
    raise ProviderError(last_error or "unknown error")


def fetch_text(url: str, params: dict | None = None,
               headers: dict | None = None, timeout: int = DEFAULT_TIMEOUT) -> str:
    """GET 一段纯文本（有些源的 JSON 不是标准格式，需要自己解析）。"""
    if _session is None:
        raise ProviderError(f"网络模块不可用（{_IMPORT_ERROR or 'unknown'}）")
    if _offline:
        raise ProviderError("offline")
    last_error = ""
    for attempt in range(MAX_RETRIES + 1):
        _throttle(url)
        try:
            resp = _session.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                return resp.text
            last_error = f"HTTP {resp.status_code}"
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(1.5 * (attempt + 1))
                continue
            break
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.8 * (attempt + 1))
    raise ProviderError(last_error or "unknown error")


def fetch_bytes(url: str, timeout: int = DEFAULT_TIMEOUT) -> bytes:
    if _session is None:
        raise ProviderError(f"网络模块不可用（{_IMPORT_ERROR or 'unknown'}）")
    if _offline:
        raise ProviderError("offline")
    _throttle_image()
    try:
        resp = _session.get(url, timeout=timeout)
        if resp.status_code == 200 and resp.content:
            return resp.content
    except Exception as exc:
        raise ProviderError(str(exc)) from exc
    raise ProviderError("empty response")


def parse_mmss(value) -> float:
    """把 '4:59' / '04:59' / 秒数 统一成秒。"""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    if ":" in text:
        parts = text.split(":")
        try:
            total = 0.0
            for part in parts:
                total = total * 60 + float(part)
            return total
        except ValueError:
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0

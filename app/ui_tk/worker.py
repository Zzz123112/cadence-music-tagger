"""后台任务：网络查询、封面下载、扫描都放线程里，界面不卡。"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from typing import Callable


class BackgroundWorker:
    def __init__(self, root: tk.Misc, poll_ms: int = 40):
        self.root = root
        self.poll_ms = poll_ms
        self._queue: queue.Queue = queue.Queue()
        self._pending = 0
        self._lock = threading.Lock()
        self._polling = False

    # ------------------------------------------------------------- 提交

    def submit(self, fn: Callable, on_success=None, on_error=None) -> None:
        with self._lock:
            self._pending += 1

        def _run():
            try:
                result = fn()
            except Exception as exc:  # 后台异常也要回到主线程处理
                self._queue.put(("error", on_error, exc))
            else:
                self._queue.put(("ok", on_success, result))

        threading.Thread(target=_run, daemon=True).start()
        self._ensure_polling()

    def post(self, fn: Callable) -> None:
        """从后台线程把一段回调送回主线程执行（不计入任务数）。

        用于"边查边显示"：每返回一个源就刷新一次界面。
        """
        self._queue.put(("post", fn, None))
        self._ensure_polling()

    # ------------------------------------------------------------- 轮询

    def _ensure_polling(self) -> None:
        if not self._polling:
            self._polling = True
            self.root.after(self.poll_ms, self._poll)

    def _poll(self) -> None:
        drained = 0
        while True:
            try:
                kind, callback, payload = self._queue.get_nowait()
            except queue.Empty:
                break
            drained += 1
            if kind == "post":
                try:
                    callback()
                except Exception:
                    pass
                continue
            with self._lock:
                self._pending = max(0, self._pending - 1)
            if callback is None:
                continue
            try:
                callback(payload)
            except Exception:
                pass
        if drained:
            self.root.update_idletasks()
        if self.busy:
            self.root.after(self.poll_ms, self._poll)
        else:
            self._polling = False
            if drained:
                self.root.event_generate("<<WorkerIdle>>", when="tail")

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._pending > 0 or not self._queue.empty()

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """供测试与自动化使用：跑完所有后台任务再返回。"""
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            self._poll()
            if not self.busy:
                return True
            time.sleep(0.01)
        return False

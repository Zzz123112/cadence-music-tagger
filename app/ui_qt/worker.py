"""把耗时任务丢到后台线程，结果回到界面线程。"""
from __future__ import annotations

import threading
import traceback

from PySide6.QtCore import QObject, Signal


class Job(QObject):
    """一次后台任务。信号在主线程发射（Qt 自动排队）。"""

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def start(self) -> None:
        def run():
            try:
                result = self._fn()
            except Exception:
                self.failed.emit(traceback.format_exc(limit=3))
                return
            self.done.emit(result)

        threading.Thread(target=run, daemon=True).start()


def run_async(owner, fn, on_done=None, on_error=None) -> Job:
    """提交任务并绑定回调，返回 Job 以便保留引用。"""
    job = Job(fn, owner)
    if on_done:
        job.done.connect(on_done)
    if on_error:
        job.failed.connect(on_error)
    job.start()
    return job

"""占用检测与释放的测试。"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from app.core import lockers
from conftest import make_wav


def test_is_locked_false_for_normal_file(tmp_path):
    path = make_wav(str(tmp_path / "a.wav"), seconds=0.2)
    assert lockers.is_locked(path) is False


def test_is_locked_false_for_missing_file(tmp_path):
    assert lockers.is_locked(str(tmp_path / "nope.wav")) is False


def test_kill_process_refuses_system_and_self():
    assert lockers.kill_process(0) is False
    assert lockers.kill_process(4) is False
    assert lockers.kill_process(os.getpid()) is False


@pytest.mark.skipif(os.name != "nt", reason="Windows 专属")
def test_find_lockers_detects_holder(tmp_path):
    """起一个子进程持有文件，应当能被找出来。"""
    path = make_wav(str(tmp_path / "held.wav"), seconds=0.2)
    script = (
        "import sys,time\n"
        "f=open(sys.argv[1],'rb')\n"
        "time.sleep(20)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", script, path])
    try:
        deadline = time.time() + 8
        found = []
        while time.time() < deadline:
            found = lockers.find_lockers(path)
            if any(pid == proc.pid for pid, _ in found):
                break
            time.sleep(0.3)
        pids = [pid for pid, _ in found]
        assert proc.pid in pids, f"未检测到持有者，返回 {found}"
    finally:
        proc.kill()
        proc.wait(timeout=5)


@pytest.mark.skipif(os.name != "nt", reason="Windows 专属")
def test_kill_lockers_releases_file(tmp_path):
    path = make_wav(str(tmp_path / "held2.wav"), seconds=0.2)
    script = (
        "import sys,time\n"
        "f=open(sys.argv[1],'rb')\n"
        "time.sleep(30)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", script, path])
    try:
        deadline = time.time() + 8
        while time.time() < deadline:
            if any(pid == proc.pid for pid, _ in lockers.find_lockers(path)):
                break
            time.sleep(0.3)
        killed, failed = lockers.kill_lockers(path)
        proc.wait(timeout=5)
        assert killed >= 1
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()

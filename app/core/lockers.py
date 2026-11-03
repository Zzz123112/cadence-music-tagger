"""找出占用文件的进程，并可强制结束。

用 Windows 自带的 Restart Manager（Vista 起内置），不依赖 handle.exe 这类外部工具。
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

CCH_RM_SESSION_KEY = 32
MAX_APP_NAME = 255
MAX_SVC_NAME = 63

ERROR_MORE_DATA = 234
ERROR_SUCCESS = 0


class RM_UNIQUE_PROCESS(ctypes.Structure):
    _fields_ = [
        ("dwProcessId", wintypes.DWORD),
        ("ProcessStartTime", wintypes.FILETIME),
    ]


class RM_PROCESS_INFO(ctypes.Structure):
    _fields_ = [
        ("Process", RM_UNIQUE_PROCESS),
        ("strAppName", wintypes.WCHAR * (MAX_APP_NAME + 1)),
        ("strServiceShortName", wintypes.WCHAR * (MAX_SVC_NAME + 1)),
        ("ApplicationType", ctypes.c_int),
        ("AppStatus", wintypes.ULONG),
        ("TSSessionId", wintypes.DWORD),
        ("bRestartable", wintypes.BOOL),
    ]


def _rm():
    if os.name != "nt":
        return None
    try:
        return ctypes.WinDLL("rstrtmgr")
    except OSError:
        return None


def find_lockers(path: str) -> list[tuple[int, str]]:
    """返回占用该文件的进程 [(pid, 名称)]。找不到就返回空列表。"""
    rm = _rm()
    if rm is None or not path:
        return []

    session = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(CCH_RM_SESSION_KEY + 1)
    if rm.RmStartSession(ctypes.byref(session), 0, key) != ERROR_SUCCESS:
        return []

    try:
        files = (wintypes.LPCWSTR * 1)(os.path.abspath(path))
        if rm.RmRegisterResources(session, 1, files, 0, None, 0, None) != ERROR_SUCCESS:
            return []

        needed = wintypes.UINT(0)
        count = wintypes.UINT(0)
        reasons = wintypes.DWORD(0)
        rc = rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count),
                          None, ctypes.byref(reasons))
        if rc not in (ERROR_MORE_DATA, ERROR_SUCCESS) or needed.value == 0:
            return []

        array = (RM_PROCESS_INFO * needed.value)()
        count = wintypes.UINT(needed.value)
        rc = rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count),
                          array, ctypes.byref(reasons))
        if rc != ERROR_SUCCESS:
            return []

        out: list[tuple[int, str]] = []
        for i in range(count.value):
            info = array[i]
            pid = int(info.Process.dwProcessId)
            name = (info.strAppName or "").strip() or _process_name(pid)
            if pid:
                out.append((pid, name))
        return out
    finally:
        rm.RmEndSession(session)


def _process_name(pid: int) -> str:
    try:
        import subprocess

        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        line = (result.stdout or "").strip().splitlines()
        if line and "," in line[0]:
            return line[0].split(",")[0].strip('"')
    except Exception:
        pass
    return f"PID {pid}"


def kill_process(pid: int) -> bool:
    """强制结束进程。拒绝结束自己和系统进程。"""
    if pid <= 4 or pid == os.getpid():
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 9)
            return True
        except OSError:
            return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_TERMINATE = 0x0001
    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        return False
    try:
        return bool(kernel32.TerminateProcess(handle, 1))
    finally:
        kernel32.CloseHandle(handle)


def kill_lockers(path: str) -> tuple[int, list[str]]:
    """结束所有占用该文件的进程。返回 (成功数, 失败描述)。"""
    killed = 0
    failed: list[str] = []
    for pid, name in find_lockers(path):
        if kill_process(pid):
            killed += 1
        else:
            failed.append(f"{name} (PID {pid})")
    return killed, failed


def is_locked(path: str) -> bool:
    """尝试以独占方式打开，判断文件是否被占用。"""
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "rb+"):
            return False
    except OSError:
        return True

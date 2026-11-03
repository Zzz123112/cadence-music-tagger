"""拖动/缩放诊断：边操作边多次截图，并把窗口矩形记录下来。

用法：python tools/drag_diag.py <输出目录>
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

from PIL import ImageGrab

u = ctypes.WinDLL("user32", use_last_error=True)
u.FindWindowW.restype = wintypes.HWND
u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                           ctypes.c_int, ctypes.c_int, ctypes.c_uint]

LEFTDOWN, LEFTUP, MOVE = 0x0002, 0x0004, 0x0001


class MI(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class INP(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("mi", MI)]


def send(flags: int) -> None:
    item = INP(type=0, mi=MI(0, 0, 0, flags, 0, None))
    u.SendInput(1, ctypes.byref(item), ctypes.sizeof(INP))


def rect(hwnd) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def main(out_dir: str) -> int:
    os.makedirs(out_dir, exist_ok=True)
    hwnd = u.FindWindowW(None, "Cadence")
    if not hwnd:
        print("没找到窗口")
        return 2
    u.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
    time.sleep(0.5)

    # ---------- 1) 拖动标题栏 ----------
    x, y, w, h = rect(hwnd)
    print("拖动前:", x, y, w, h)
    sx, sy = x + w // 2, y + 30
    u.SetCursorPos(sx, sy)
    time.sleep(0.4)
    send(LEFTDOWN)
    for step in range(1, 21):
        u.SetCursorPos(sx + step * 6, sy + step * 3)
        time.sleep(0.035)
        if step in (6, 12, 18):
            ImageGrab.grab(bbox=(x - 40, y - 40, x + w + 260, y + h + 160)).save(
                os.path.join(out_dir, f"drag_{step}.png"))
    time.sleep(0.2)
    ImageGrab.grab(bbox=(x - 40, y - 40, x + w + 260, y + h + 160)).save(
        os.path.join(out_dir, "drag_hold.png"))
    send(LEFTUP)
    time.sleep(0.5)
    print("拖动后:", rect(hwnd))

    # ---------- 2) 拖右边缘缩放 ----------
    x, y, w, h = rect(hwnd)
    ex, ey = x + w - 2, y + h // 2
    u.SetCursorPos(ex, ey)
    time.sleep(0.4)
    send(LEFTDOWN)
    for step in range(1, 26):
        u.SetCursorPos(ex + step * 6, ey)
        time.sleep(0.03)
    time.sleep(0.2)
    send(LEFTUP)
    time.sleep(0.6)
    print("缩放后:", rect(hwnd))

    # ---------- 3) 拖右下角 ----------
    x, y, w, h = rect(hwnd)
    cx, cy = x + w - 2, y + h - 2
    u.SetCursorPos(cx, cy)
    time.sleep(0.4)
    send(LEFTDOWN)
    for step in range(1, 16):
        u.SetCursorPos(cx + step * 5, cy + step * 4)
        time.sleep(0.03)
    time.sleep(0.2)
    send(LEFTUP)
    time.sleep(0.6)
    print("角缩放后:", rect(hwnd))
    print("窗口存活:", bool(u.IsWindow(hwnd)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "diag"))

"""模拟鼠标拖动标题栏，用来复现"一移动就闪退"。"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]

INPUT_MOUSE = 0
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_MOVE = 0x0001


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("mi", MOUSEINPUT)]


def send(flags: int, dx: int = 0, dy: int = 0) -> None:
    item = INPUT(type=INPUT_MOUSE,
                 mi=MOUSEINPUT(dx=dx, dy=dy, mouseData=0, dwFlags=flags,
                               time=0, dwExtraInfo=None))
    user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT))


def main() -> int:
    hwnd = user32.FindWindowW(None, "Cadence")
    if not hwnd:
        print("没找到窗口")
        return 2
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    print("窗口:", rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    start_x = rect.left + (rect.right - rect.left) // 2
    start_y = rect.top + 30        # 标题栏中间
    user32.SetCursorPos(start_x, start_y)
    time.sleep(0.4)
    send(MOUSEEVENTF_LEFTDOWN)
    time.sleep(0.2)
    for step in range(1, 21):
        user32.SetCursorPos(start_x + step * 6, start_y + step * 3)
        time.sleep(0.03)
    send(MOUSEEVENTF_LEFTUP)
    time.sleep(0.5)
    rect2 = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect2))
    print("拖动后:", rect2.left, rect2.top,
          "移动了", rect2.left - rect.left, rect2.top - rect.top)
    alive = user32.IsWindow(hwnd)
    print("窗口还在:", bool(alive))
    return 0 if alive else 1


if __name__ == "__main__":
    sys.exit(main())

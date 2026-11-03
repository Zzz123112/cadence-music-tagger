"""在拖动过程中截图，检查是否有拖影/残影。"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

from PIL import ImageGrab

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]

INPUT_MOUSE = 0
LEFTDOWN, LEFTUP = 0x0002, 0x0004


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("mi", MOUSEINPUT)]


def send(flags: int) -> None:
    item = INPUT(type=INPUT_MOUSE,
                 mi=MOUSEINPUT(0, 0, 0, flags, 0, None))
    user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT))


def main(out_path: str) -> int:
    hwnd = user32.FindWindowW(None, "Cadence")
    if not hwnd:
        print("没找到窗口")
        return 2
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.4)
    sx = rect.left + (rect.right - rect.left) // 2
    sy = rect.top + 30
    user32.SetCursorPos(sx, sy)
    time.sleep(0.4)
    send(LEFTDOWN)
    time.sleep(0.2)
    # 分几段移动，模拟真实拖动
    for step in range(1, 26):
        user32.SetCursorPos(sx + step * 5, sy + step * 2)
        time.sleep(0.04)
    time.sleep(0.35)          # 按住不动，等合成稳定
    before = ImageGrab.grab()
    before.save(out_path)
    send(LEFTUP)
    time.sleep(0.4)
    after = ImageGrab.grab()
    after.save(out_path.replace(".png", "_after.png"))
    rect2 = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect2))
    print("拖动中窗口位置:", rect2.left, rect2.top)
    print("窗口还在:", bool(user32.IsWindow(hwnd)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "dragmid.png"))

"""拖动时窗口各区是否正常绘制：对比拖动前后窗口区域内的像素。"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

from PIL import ImageGrab

u = ctypes.WinDLL("user32", use_last_error=True)
u.FindWindowW.restype = wintypes.HWND
u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                           ctypes.c_int, ctypes.c_int, ctypes.c_uint]


class MI(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class INP(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("mi", MI)]


def send(flags: int) -> None:
    item = INP(type=0, mi=MI(0, 0, 0, flags, 0, None))
    u.SendInput(1, ctypes.byref(item), ctypes.sizeof(INP))


def rect(hwnd):
    r = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def grab(x, y, w, h):
    return ImageGrab.grab(bbox=(x, y, x + w, y + h)).convert("RGB")


def diff(a, b, box) -> float:
    """两块区域的平均像素差。"""
    ca, cb = a.crop(box), b.crop(box)
    pa, pb = list(ca.getdata()), list(cb.getdata())
    total = 0
    for p, q in zip(pa, pb):
        total += abs(p[0] - q[0]) + abs(p[1] - q[1]) + abs(p[2] - q[2])
    return total / (len(pa) * 3)


def main() -> int:
    hwnd = u.FindWindowW(None, "Cadence")
    if not hwnd:
        print("没找到窗口")
        return 2
    u.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
    time.sleep(0.6)

    x, y, w, h = rect(hwnd)
    base = grab(x, y, w, h)
    print("基准窗口:", x, y, w, h)

    sx, sy = x + w // 2, y + 30
    u.SetCursorPos(sx, sy)
    time.sleep(0.4)
    send(0x0002)
    time.sleep(0.2)
    for step in range(1, 20):
        u.SetCursorPos(sx + step * 5, sy + step * 2)
        time.sleep(0.035)
    time.sleep(0.45)                       # 按住不动，等画面稳定
    nx, ny, nw, nh = rect(hwnd)
    during = grab(nx, ny, nw, nh)
    during.save("diag/during_hold.png")

    boxes = {
        "标题栏(0-56)": (0, 0, w, 56),
        "左栏标题(56-120)": (0, 56, 340, 120),
        "候选区中段": (380, 500, 1160, 700),
        "底部按钮": (0, h - 90, w, h),
    }
    for name, box in boxes.items():
        if box[2] > w or box[3] > h:
            continue
        print(f"  {name}: 与基准差异 {diff(base, during, box):.2f}")

    send(0x0004)
    time.sleep(0.6)
    ax, ay, aw, ah = rect(hwnd)
    after = grab(ax, ay, aw, ah)
    after.save("diag/after_release.png")
    for name, box in boxes.items():
        if box[2] > aw or box[3] > ah:
            continue
        print(f"  松手后 {name}: 与基准差异 {diff(base, after, box):.2f}")
    print("位移:", nx - x, ny - y)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""拖动过程中连续抓帧，用像素定位窗口实际绘制范围，判断有没有拖影。

判定：窗口移动后，旧位置不应再有窗口内容；新位置应完整覆盖。
"""
from __future__ import annotations

import ctypes
import sys
import threading
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


class MI(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class INP(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("mi", MI)]


def send(flags: int) -> None:
    item = INP(0, MI(0, 0, 0, flags, 0, None))
    u.SendInput(1, ctypes.byref(item), ctypes.sizeof(INP))


def rect(hwnd):
    r = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def app_mask(img):
    """返回 (left, top, right, bottom)：界面浅色区域的包围盒。"""
    px = img.load()
    w, h = img.size
    left = top = 10 ** 9
    right = bottom = -1
    step = 4
    for y in range(0, h - 80, step):          # 跳过底部任务栏
        for x in range(0, w, step):
            r, g, b = px[x, y][:3]
            # 界面的两种特征底色（精确匹配，避免撞上别的白色窗口）
            hit_a = abs(r - 246) <= 2 and abs(g - 244) <= 2 and abs(b - 250) <= 2
            hit_b = abs(r - 244) <= 2 and abs(g - 220) <= 2 and abs(b - 233) <= 2
            if hit_a or hit_b:
                if x < left:
                    left = x
                if x > right:
                    right = x
                if y < top:
                    top = y
                if y > bottom:
                    bottom = y
    return left, top, right, bottom


def main() -> int:
    hwnd = u.FindWindowW(None, "Cadence")
    if not hwnd:
        print("没找到窗口")
        return 2
    u.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x1 | 0x2 | 0x10)
    time.sleep(0.6)
    x0, y0, w, h = rect(hwnd)
    print("起始窗口:", x0, y0, w, h)

    frames = []
    stop = threading.Event()

    def capture():
        while not stop.is_set():
            shot = ImageGrab.grab()
            frames.append((time.monotonic(), rect(hwnd), app_mask(shot)))
            time.sleep(0.02)

    th = threading.Thread(target=capture, daemon=True)
    th.start()

    sx, sy = x0 + w // 2, y0 + 30
    u.SetCursorPos(sx, sy)
    time.sleep(0.3)
    send(0x0002)          # 左键按下
    time.sleep(0.1)
    for step in range(1, 31):
        u.SetCursorPos(sx + step * 6, sy + step * 2)
        time.sleep(0.02)
    time.sleep(0.35)      # 按住不动
    send(0x0004)          # 松开
    time.sleep(0.4)
    stop.set()
    time.sleep(0.1)

    print(f"抓帧数: {len(frames)}")
    # 只看"窗口在移动中"的帧
    bad = 0
    checked = 0
    for stamp, (rx, ry, rw, rh), (ml, mt, mr, mb) in frames:
        if mr < 0:
            continue
        checked += 1
        # 绘制区域比窗口大出 40px 以上 => 旧位置有残留/错位
        if ml < rx - 40 or mt < ry - 40 or mr > rx + rw + 40 or mb > ry + rh + 40:
            bad += 1
            if bad <= 5:
                print(f"  可疑帧 窗口=({rx},{ry}) 绘制=({ml},{mt},{mr},{mb})")
    print(f"检查帧数: {checked}  可疑(有残留): {bad}")
    print("结论:", "有拖影" if bad > max(2, checked // 10) else "未发现拖影")
    return 0


if __name__ == "__main__":
    sys.exit(main())

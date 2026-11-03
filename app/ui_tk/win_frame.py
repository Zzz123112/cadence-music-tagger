"""把 Tk 窗口变成「无系统标题栏、但保留原生行为」的窗口。

为什么不用 overrideredirect：
- overrideredirect 的窗口是 WS_POPUP，任务栏没有图标、不能原生缩放，
  并且点它不会正常激活（表现就是"点了没反应"）。
这里改为保留正常的顶层窗口，只把 WS_CAPTION 去掉，再用 DWM 做圆角。
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

GWL_STYLE = -16
GWL_EXSTYLE = -20

WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000

WS_EX_APPWINDOW = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWCP_ROUND = 2

WM_NCCALCSIZE = 0x0083
WM_WINDOWPOSCHANGING = 0x0046


class _WINDOWPOS(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND), ("hwndInsertAfter", wintypes.HWND),
        ("x", ctypes.c_int), ("y", ctypes.c_int),
        ("cx", ctypes.c_int), ("cy", ctypes.c_int),
        ("flags", ctypes.c_uint),
    ]


# 拖动期间要锁定的窗口尺寸：hwnd -> (宽, 高)
_pinned_size: dict[int, tuple[int, int]] = {}
WM_NCHITTEST = 0x0084
GWLP_WNDPROC = -4

HTLEFT, HTRIGHT, HTTOP, HTBOTTOM = 10, 11, 12, 15
HTTOPLEFT, HTTOPRIGHT, HTBOTTOMLEFT, HTBOTTOMRIGHT = 13, 14, 16, 17
RESIZE_BORDER = 6

_LRESULT = ctypes.c_longlong
_WNDPROC = ctypes.WINFUNCTYPE(
    _LRESULT, wintypes.HWND, ctypes.c_uint, ctypes.c_ulonglong, ctypes.c_longlong
)
# 回调对象必须一直被引用，否则会被回收导致崩溃
_procs: dict[int, tuple] = {}
_taskbar_list = None

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_dwmapi = None
try:
    _dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
except OSError:
    _dwmapi = None

_user32.GetWindowLongPtrW.restype = ctypes.c_longlong
_user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.SetWindowLongPtrW.restype = ctypes.c_longlong
_user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
_user32.LoadImageW.restype = wintypes.HANDLE
_user32.LoadImageW.argtypes = [
    wintypes.HINSTANCE, wintypes.LPCWSTR, ctypes.c_uint,
    ctypes.c_int, ctypes.c_int, ctypes.c_uint,
]
try:
    _user32.SendMessageW.restype = ctypes.c_longlong
    _user32.SendMessageW.argtypes = [
        wintypes.HWND, ctypes.c_uint, ctypes.c_ulonglong, ctypes.c_longlong,
    ]
except Exception:
    pass
_user32.SetWindowPos.restype = wintypes.BOOL
_user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, ctypes.c_uint,
]
try:
    _user32.RedrawWindow.restype = wintypes.BOOL
    _user32.RedrawWindow.argtypes = [
        wintypes.HWND, ctypes.c_void_p, wintypes.HANDLE, ctypes.c_uint,
    ]
except Exception:
    pass


def hwnd_of(tk_window) -> int:
    """拿到顶层窗口句柄（不是客户区）。"""
    try:
        frame = tk_window.frame()
        return int(frame, 16)
    except Exception:
        try:
            return int(tk_window.winfo_id())
        except Exception:
            return 0


def _hit_test(hwnd: int, lparam: int) -> int | None:
    """客户区占满后，自己判断鼠标是否落在边缘，交给系统去缩放。"""
    try:
        x = ctypes.c_short(lparam & 0xFFFF).value
        y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
        rect = wintypes.RECT()
        if not _user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return None
        left = x - rect.left
        top = y - rect.top
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        b = RESIZE_BORDER
        on_left, on_right = left < b, left >= width - b
        on_top, on_bottom = top < b, top >= height - b
        if on_top and on_left:
            return HTTOPLEFT
        if on_top and on_right:
            return HTTOPRIGHT
        if on_bottom and on_left:
            return HTBOTTOMLEFT
        if on_bottom and on_right:
            return HTBOTTOMRIGHT
        if on_left:
            return HTLEFT
        if on_right:
            return HTRIGHT
        if on_top:
            return HTTOP
        if on_bottom:
            return HTBOTTOM
    except Exception:
        return None
    return None


def _install_nc_handler(hwnd: int) -> bool:
    """让客户区占满整个窗口，抹掉系统画的那圈黑边框。

    原理：拦截 WM_NCCALCSIZE 并返回 0，客户区就等于整个窗口矩形。
    保留 WS_THICKFRAME，所以贴边拖动仍然是系统原生的缩放。
    """
    if hwnd in _procs:
        return True
    try:
        user32 = _user32
        user32.SetWindowLongPtrW.restype = ctypes.c_longlong
        user32.SetWindowLongPtrW.argtypes = [
            wintypes.HWND, ctypes.c_int, ctypes.c_void_p
        ]
        user32.CallWindowProcW.restype = ctypes.c_longlong
        user32.CallWindowProcW.argtypes = [
            ctypes.c_void_p, wintypes.HWND, ctypes.c_uint,
            ctypes.c_ulonglong, ctypes.c_longlong,
        ]

        state = {"old": None}

        def handler(window, msg, wparam, lparam):
            try:
                if msg == WM_NCCALCSIZE:
                    # 两种 wParam 都直接返回 0：客户区 = 整个窗口矩形，
                    # 这样移动/缩放时不会再被系统扣掉一圈边框导致尺寸跳变
                    return 0
                if msg == WM_WINDOWPOSCHANGING:
                    # 拖动时把尺寸钉死：Tk 在窗口移动后会按它记着的边框尺寸
                    # 来"纠正"窗口大小，一纠正就整窗重绘，看起来就是拖影。
                    pinned = _pinned_size.get(int(window))
                    if pinned:
                        info = ctypes.cast(
                            lparam, ctypes.POINTER(_WINDOWPOS)
                        ).contents
                        if not (info.flags & SWP_NOSIZE):
                            info.cx, info.cy = pinned
                if msg == WM_NCHITTEST:
                    edge = _hit_test(window, lparam)
                    if edge is not None:
                        return edge
            except Exception:
                pass
            try:
                return user32.CallWindowProcW(
                    state["old"], window, msg, wparam, lparam
                )
            except Exception:
                return 0

        proc = _WNDPROC(handler)
        old = user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC,
                                       ctypes.cast(proc, ctypes.c_void_p))
        state["old"] = old
        _procs[hwnd] = (proc, state)
        return True
    except Exception:
        return False


def uninstall_nc_handler(tk_window) -> None:
    """窗口销毁前还原原来的窗口过程，避免残留回调指向已死的对象。"""
    hwnd = hwnd_of(tk_window)
    entry = _procs.pop(hwnd, None)
    if not entry:
        return
    _proc, state = entry
    try:
        _user32.SetWindowLongPtrW(
            ctypes.c_void_p(hwnd) or wintypes.HWND(hwnd), GWLP_WNDPROC,
            ctypes.c_void_p(int(state["old"] or 0)),
        )
    except Exception:
        pass


def prepare(tk_window, rounded: bool = True, taskbar: bool = True,
            borderless: bool = False) -> bool:
    """隐藏系统标题栏。

    borderless=True：连缩放边框一起去掉（WS_POPUP 风格，弹窗用）。
    borderless=False（默认）：**保留** WS_CAPTION/WS_THICKFRAME，
    只靠 WM_NCCALCSIZE 把客户区铺满整个窗口。
    这样窗口仍是"正常窗口"，Windows 11 任务栏才肯给它按钮
    （实测 WS_POPUP + WS_EX_APPWINDOW 在 26xxx 上根本不出图标）。
    """
    hwnd = hwnd_of(tk_window)
    if not hwnd:
        return False
    try:
        style = _user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
        if borderless:
            style &= ~(WS_CAPTION | WS_THICKFRAME)
        else:
            style |= WS_CAPTION | WS_THICKFRAME
        style |= WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
        _user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style)

        ex_style = _user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        if taskbar:
            ex_style |= WS_EX_APPWINDOW
            ex_style &= ~WS_EX_TOOLWINDOW
        else:
            ex_style |= WS_EX_TOOLWINDOW
            ex_style &= ~WS_EX_APPWINDOW
        _user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex_style)
    except Exception:
        return False

    _install_nc_handler(hwnd)

    # 不管是点关闭还是被系统销毁，都要把窗口过程还回去
    try:
        def on_destroy(event, widget=tk_window) -> None:
            if event.widget is widget:
                uninstall_nc_handler(widget)

        tk_window.bind("<Destroy>", on_destroy, add="+")
    except Exception:
        pass

    try:
        _user32.SetWindowPos(
            hwnd, None, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
        )
    except Exception:
        pass

    if rounded:
        apply_round_corners(hwnd)
    return True


def apply_round_corners(hwnd: int | None = None, window=None) -> bool:
    """请求 DWM 圆角（Windows 11）。失败就算了，不影响使用。"""
    if _dwmapi is None:
        return False
    if hwnd is None and window is not None:
        hwnd = hwnd_of(window)
    if not hwnd:
        return False
    value = ctypes.c_int(DWMWCP_ROUND)
    try:
        result = _dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
        return result == 0
    except Exception:
        return False




def cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    try:
        _user32.GetCursorPos(ctypes.byref(pt))
        return int(pt.x), int(pt.y)
    except Exception:
        return 0, 0


def left_button_down() -> bool:
    try:
        return bool(_user32.GetAsyncKeyState(0x01) & 0x8000)
    except Exception:
        return False


def minimize(tk_window) -> bool:
    """最小化。无边框窗口（override-redirect）不能用 Tk 的 iconify。"""
    hwnd = hwnd_of(tk_window)
    if not hwnd:
        return False
    try:
        _user32.ShowWindow(wintypes.HWND(hwnd), 6)   # SW_MINIMIZE
        return True
    except Exception:
        return False


def restore(tk_window) -> bool:
    hwnd = hwnd_of(tk_window)
    if not hwnd:
        return False
    try:
        _user32.ShowWindow(wintypes.HWND(hwnd), 9)   # SW_RESTORE
        return True
    except Exception:
        return False


_ICON_HANDLES: dict[str, int] = {}


def set_window_icon(tk_window, ico_path: str) -> bool:
    """用 WM_SETICON 给窗口设大/小图标。

    Tk 的 iconbitmap 只改窗口类图标，任务栏有时会取窗口图标（WM_GETICON），
    取不到就显示成空白/默认图标。这里两个尺寸都显式设上。
    """
    hwnd = hwnd_of(tk_window)
    if not hwnd or not ico_path or not os.path.isfile(ico_path):
        return False
    try:
        handle = wintypes.HWND(hwnd)
        for size, which in ((32, 1), (16, 0)):   # 1 = ICON_BIG, 0 = ICON_SMALL
            key = f"{ico_path}:{size}"
            hicon = _ICON_HANDLES.get(key)
            if not hicon:
                hicon = _user32.LoadImageW(
                    None, ico_path, 1, size, size, 0x00000010  # IMAGE_ICON|LR_LOADFROMFILE
                )
                if hicon:
                    _ICON_HANDLES[key] = hicon
            if hicon:
                _user32.SendMessageW(handle, 0x0080, which, hicon)  # WM_SETICON
        return True
    except Exception:
        return False


def _taskbar_add_tab(hwnd: int) -> bool:
    """用 ITaskbarList::AddTab 把窗口显式加进任务栏。

    无边框窗口是 WS_POPUP，Windows 11 的任务栏不认 WS_EX_APPWINDOW，
    实测根本不建按钮；AddTab 是让这类窗口出现在任务栏的官方办法。
    """
    global _taskbar_list
    try:
        if _taskbar_list is None:
            ole32 = ctypes.WinDLL("ole32")
            ole32.CoInitialize(None)
            clsid = _guid("{56FDF344-FD6D-11d0-958A-006097C9A090}")
            iid = _guid("{56FDF342-FD6D-11d0-958A-006097C9A090}")
            ptr = ctypes.c_void_p()
            if ole32.CoCreateInstance(
                ctypes.byref(clsid), None, 1, ctypes.byref(iid),
                ctypes.byref(ptr),
            ) != 0 or not ptr.value:
                _taskbar_list = False
                return False
            vtable = ctypes.cast(
                ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
            )[0]
            hr_init = ctypes.WINFUNCTYPE(
                ctypes.c_longlong, ctypes.c_void_p
            )(vtable[3])(ptr)
            if hr_init != 0:
                _taskbar_list = False
                return False
            _taskbar_list = (ptr, vtable)
        ptr, vtable = _taskbar_list  # type: ignore[misc]
        func = ctypes.WINFUNCTYPE(
            ctypes.c_longlong, ctypes.c_void_p, ctypes.c_longlong
        )(vtable[4])
        return func(ptr, hwnd) == 0
    except Exception:
        _taskbar_list = False
        return False


def _guid(text: str):
    import uuid

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8),
        ]

    value = uuid.UUID(text)
    out = _GUID()
    out.Data1 = value.time_low
    out.Data2 = value.time_mid
    out.Data3 = value.time_hi_version
    rest = value.bytes[8:]
    for index in range(8):
        out.Data4[index] = rest[index]
    return out




def start_native_drag(tk_window, screen_x: int = 0, screen_y: int = 0) -> bool:
    """把拖动交给系统：ReleaseCapture + PostMessage(WM_NCLBUTTONDOWN, HTCAPTION)。

    用 PostMessage 而不是 SendMessage：SendMessage 会在当前回调里直接进入
    系统的移动模态循环，容易和 Python 的 GIL 打架（以前崩过）。
    """
    hwnd = hwnd_of(tk_window)
    if not hwnd:
        return False
    try:
        if not screen_x and not screen_y:
            point = wintypes.POINT()
            _user32.GetCursorPos(ctypes.byref(point))
            screen_x, screen_y = point.x, point.y
        # lparam 必须是鼠标的屏幕坐标，否则系统会按 (0,0) 当锚点，窗口先跳一下
        packed = (int(screen_y) << 16) | (int(screen_x) & 0xFFFF)
        _user32.ReleaseCapture()
        _user32.PostMessageW(wintypes.HWND(hwnd), 0x00A1, 2, packed)
        return True
    except Exception:
        return False


def pin_size(tk_window, width: int = 0, height: int = 0) -> None:
    """拖动期间锁住窗口尺寸（0 表示用当前尺寸）。"""
    hwnd = hwnd_of(tk_window)
    if not hwnd:
        return
    if not width or not height:
        _left, _top, width, height = _window_rect(hwnd)
    _pinned_size[hwnd] = (int(width), int(height))


def unpin_size(tk_window) -> None:
    hwnd = hwnd_of(tk_window)
    if hwnd:
        _pinned_size.pop(hwnd, None)


def ensure_taskbar(tk_window, refresh: bool = False) -> bool:
    """确保任务栏里有这个窗口的按钮。

    无边框窗口是 WS_POPUP，Explorer 有时会因为「创建时还不是 APPWINDOW」
    而不给它任务栏按钮，表现就是"有时有图标、有时没有"。
    这里重新声明一次样式；refresh=True 时再做一次隐藏/显示，
    强制 Explorer 重新评估（让任务栏图标回来的常规做法）。
    """
    hwnd = hwnd_of(tk_window)
    if not hwnd:
        return False
    try:
        handle = wintypes.HWND(hwnd)
        ex_style = _user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        ex_style |= WS_EX_APPWINDOW
        ex_style &= ~WS_EX_TOOLWINDOW
        _user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex_style)
        if refresh:
            _user32.SetWindowPos(
                handle, None, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
                | SWP_FRAMECHANGED,
            )
            if _user32.IsWindowVisible(handle):
                _user32.ShowWindow(handle, 0)   # SW_HIDE
                _user32.ShowWindow(handle, 4)   # SW_SHOWNOACTIVATE
        _taskbar_add_tab(hwnd)
        return True
    except Exception:
        return False


def _window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    _user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
    return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def resize_window(tk_window, x: int, y: int, width: int, height: int) -> None:
    """同时移动和改尺寸（拖拽/贴边缩放用）。

    现在主窗口是"正常窗口 + 隐藏标题栏"，Tk 调 geometry 时会自己加上
    一圈边框宽度（实测 +16px 宽），导致拖动一次窗口就变宽一点。
    这里先量一次 Tk 的偏差，之后每次都先减掉，窗口尺寸就稳定了。
    """
    hwnd = hwnd_of(tk_window)
    if not hwnd:
        tk_window.geometry(f"{int(width)}x{int(height)}+{int(x)}+{int(y)}")
        return
    delta = getattr(tk_window, "_frame_delta", None)
    try:
        if delta is None:
            tk_window.geometry(f"{int(width)}x{int(height)}+{int(x)}+{int(y)}")
            tk_window.update_idletasks()
            _left, _top, real_w, real_h = _window_rect(hwnd)
            delta = (real_w - int(width), real_h - int(height))
            tk_window._frame_delta = delta
            if delta == (0, 0):
                return
        _dx, _dy = delta
        tk_window.geometry(
            f"{int(width) - _dx}x{int(height) - _dy}+{int(x)}+{int(y)}"
        )
    except Exception:
        tk_window.geometry(f"{int(width)}x{int(height)}+{int(x)}+{int(y)}")


def move_window(tk_window, x: int, y: int) -> None:
    """只改位置，不动尺寸（窗口已无边框，客户区=窗口矩形）。"""
    hwnd = hwnd_of(tk_window)
    if hwnd:
        try:
            _user32.SetWindowPos(
                hwnd, None, int(x), int(y), 0, 0,
                SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
            )
            return
        except Exception:
            pass
    tk_window.geometry(f"+{int(x)}+{int(y)}")


def drop_layered(tk_window) -> None:
    """去掉 WS_EX_LAYERED。

    淡入用的是 -alpha，那会把窗口变成分层窗口；分层窗口在拖动大内容时
    重绘跟不上，就会出现残影/黑块。动画结束后撤掉它。
    """
    hwnd = hwnd_of(tk_window)
    if not hwnd:
        return
    try:
        ex_style = _user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        if not (ex_style & WS_EX_LAYERED):
            return
        ex_style &= ~WS_EX_LAYERED
        ex_style |= WS_EX_APPWINDOW
        _user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex_style)
        _user32.SetWindowPos(
            hwnd, None, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
        )
    except Exception:
        pass


def redraw_now(tk_window) -> None:
    """强制重绘一次，清掉拖动留下的残影。"""
    hwnd = hwnd_of(tk_window)
    if not hwnd:
        return
    try:
        _user32.RedrawWindow(hwnd, None, None, 0x0001 | 0x0004 | 0x0100)
    except Exception:
        pass


def keep_on_top_when_clicked(tk_window) -> None:
    """点击时把自己提到最前（无标题栏窗口在部分环境下不会自动激活）。"""
    try:
        tk_window.lift()
        tk_window.focus_force()
    except Exception:
        pass


def set_border_color(tk_window, color: str) -> bool:
    """把窗口那圈边框线设成和界面同色，看不出灰边。"""
    if _dwmapi is None:
        return False
    hwnd = hwnd_of(tk_window)
    if not hwnd:
        return False
    try:
        value = color.lstrip("#")
        r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
        colorref = ctypes.c_uint(r | (g << 8) | (b << 16))
        result = _dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_BORDER_COLOR,
            ctypes.byref(colorref), ctypes.sizeof(colorref),
        )
        return result == 0
    except Exception:
        return False


def enable_dark_titlebar(tk_window, dark: bool) -> None:
    """深色模式下让窗口边框/阴影也是深色（不影响自定义标题栏）。"""
    if _dwmapi is None:
        return
    hwnd = hwnd_of(tk_window)
    if not hwnd:
        return
    value = ctypes.c_int(1 if dark else 0)
    try:
        _dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), 20, ctypes.byref(value), ctypes.sizeof(value)
        )
    except Exception:
        pass


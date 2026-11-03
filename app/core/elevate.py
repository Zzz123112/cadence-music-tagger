"""以管理员身份重新启动自己。"""
from __future__ import annotations

import os
import sys


def can_relaunch() -> bool:
    return os.name == "nt" and getattr(sys, "frozen", False)


def relaunch_as_admin(argv: list[str] | None = None) -> tuple[bool, str]:
    """通过 UAC 重新拉起自己。成功返回 (True, "")。

    会自动带上 --elevate-attempt 标记，避免新实例又去申请提权形成死循环。
    """
    if not can_relaunch():
        return False, "仅支持打包后的程序"
    from .elevation_policy import ATTEMPT_FLAG

    params = " ".join(argv or [ATTEMPT_FLAG])
    try:
        import ctypes

        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params,
            os.path.dirname(sys.executable), 1
        )
        # ShellExecuteW 返回值 > 32 表示成功
        if result > 32:
            return True, ""
        if result == 5:  # ERROR_ACCESS_DENIED：用户点了"否"
            return False, "已取消"
        return False, f"错误码 {result}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def in_restricted_session() -> bool:
    """检测是否运行在被限制的会话里（DSH 沙箱会带这个变量）。"""
    return bool(os.environ.get("DSH_SESSION_ID") or os.environ.get("DSH_SHELL"))


# ---------------------------------------------------------------- 始终以管理员启动

COMPAT_KEY = r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"
RUNAS_FLAG = "~ RUNASADMIN"


def _target_exe() -> str:
    return os.path.abspath(sys.executable)


def is_always_admin() -> bool:
    """查询当前程序是否已被标记为「始终以管理员身份运行」。"""
    if os.name != "nt":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, COMPAT_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, _target_exe())
    except OSError:
        return False
    return "RUNASADMIN" in str(value).upper()


def set_always_admin(enabled: bool = True) -> tuple[bool, str]:
    """写注册表兼容性标记，让双击启动时自动请求管理员权限。

    效果：每次启动点一次 UAC，之后所有保存都不再弹窗。
    """
    if os.name != "nt":
        return False, "仅支持 Windows"
    import winreg

    exe = _target_exe()
    try:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, COMPAT_KEY, 0,
                                 winreg.KEY_READ | winreg.KEY_SET_VALUE)
        except FileNotFoundError:
            key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, COMPAT_KEY, 0,
                                     winreg.KEY_READ | winreg.KEY_SET_VALUE)
        try:
            if enabled:
                winreg.SetValueEx(key, exe, 0, winreg.REG_SZ, RUNAS_FLAG)
            else:
                try:
                    winreg.DeleteValue(key, exe)
                except FileNotFoundError:
                    pass
        finally:
            winreg.CloseKey(key)
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""

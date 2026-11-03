"""程序入口。"""
from __future__ import annotations

import os
import sys
import tempfile
import tkinter as tk
import traceback

try:
    from .ui_tk.main_window_v2 import MainWindow
except Exception:  # Pillow 缺失等情况退回旧界面
    from .ui_tk.main_window import MainWindow
from .ui_tk.state import app_data_dir, open_context


def _log_dir() -> str:
    try:
        return app_data_dir()
    except Exception:
        return tempfile.gettempdir()


def _log(message: str) -> None:
    """窗口模式下看不到 stdout，只能落盘。"""
    try:
        with open(os.path.join(_log_dir(), "startup.log"), "a", encoding="utf-8") as fh:
            fh.write(message.rstrip() + "\n")
    except OSError:
        pass


def _reset_log() -> None:
    try:
        path = os.path.join(_log_dir(), "startup.log")
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def _alert(message: str) -> None:
    """窗口模式下没有控制台，致命错误必须弹出来，不能静默退出。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, "音乐标签管家", 0x10)
    except Exception:
        pass


def selftest(log_path: str) -> int:
    """打包后的自检：构建界面、跑一次刷新、写结果、退出。"""
    lines: list[str] = []
    code = 0
    try:
        data_dir = tempfile.mkdtemp(prefix="mt-selftest-")
        ctx = open_context(data_dir=data_dir)
        ctx.settings["sources"] = {"MusicBrainz": False, "网易云": False, "QQ音乐": False}
        root = tk.Tk()
        root.withdraw()
        window = MainWindow(ctx, root)
        root.update_idletasks()
        window.refresh_list()
        lines.append("window ok, list refreshed")
        root.destroy()
        lines.append("SELFTEST OK")
    except Exception:
        code = 1
        lines.append("SELFTEST FAILED")
        lines.append(traceback.format_exc())
    try:
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
    except OSError:
        code = 1
    return code


def smoke(log_path: str, seconds: float = 3.0) -> int:
    """真实启动路径（可见窗口 + mainloop），几秒后自己退出并写日志。"""
    lines: list[str] = []
    code = 0
    try:
        ctx = open_context()
        lines.append(f"data_dir={ctx.data_dir}")
        root = tk.Tk()
        lines.append("tk ok")
        window = MainWindow(ctx, root)
        lines.append("window built")
        root.after(int(seconds * 1000), root.destroy)
        root.mainloop()
        lines.append("SMOKE OK")
    except Exception:
        code = 1
        lines.append("SMOKE FAILED")
        lines.append(traceback.format_exc())
    try:
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
    except OSError:
        code = 1
    return code


def run_gui() -> int:
    """正常启动。任何异常都写日志并弹窗，不至于无声退出。"""
    _reset_log()
    _log(f"start argv={sys.argv[1:]}")
    _log(f"python={sys.version.split()[0]} frozen={getattr(sys, 'frozen', False)}")
    # 显式应用标识：没有它时，Windows 11 的任务栏有时不给图标/把图标显示成空白
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Cadence.MusicTagger.1"
        )
        _log("app user model id set")
    except Exception as exc:
        _log(f"set app id failed: {exc}")
    try:
        ctx = open_context()
        _log(f"context ok, data_dir={ctx.data_dir} library_root={ctx.library_root!r}")

        # 提权不在启动时做：ShellExecuteW("runas") 是阻塞调用，
        # UAC 弹不出来时会把程序卡在"无窗口"状态，关都关不掉。
        # 需要管理员身份时，用「以管理员身份启动.bat」或在设置里勾「始终以管理员启动」。

        root = tk.Tk()
        _log("tk root created")
        window = MainWindow(ctx, root)
        _log("window built")

        def _report(exc_type, exc_value, exc_tb):
            _log("callback error:\n" + "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))

        root.report_callback_exception = _report
        root.update_idletasks()
        root.update()
        _log("first paint ok, entering mainloop")
        root.mainloop()
        _log("mainloop exited normally")
        return 0
    except Exception:
        detail = traceback.format_exc()
        _log("FATAL:\n" + detail)
        _alert(
            "启动失败。\n\n"
            + detail.strip().splitlines()[-1]
            + "\n\n日志位置："
            + os.path.join(_log_dir(), "startup.log")
        )
        return 1


def shot(log_path: str, library: str = "", out: str = "") -> int:
    """把真实界面截成 PNG，用来检查视觉。"""
    import time

    lines: list[str] = []
    code = 0
    try:
        data_dir = tempfile.mkdtemp(prefix="mt-shot-")
        ctx = open_context(data_dir=data_dir)
        ctx.settings["sources"] = {"MusicBrainz": False, "网易云": False, "QQ音乐": False}
        root = tk.Tk()
        root._skip_fade = True  # 截图时不跑淡入动画
        window = MainWindow(ctx, root)
        if not hasattr(window, "prepare_for_shot"):
            root.geometry("1200x780+30+30")
        root.update()
        try:
            root.attributes("-alpha", 1.0)
            root.lift()
            root.attributes("-topmost", True)
            root.update()
        except Exception:
            pass
        if library:
            if hasattr(window, "prepare_for_shot"):
                window.prepare_for_shot(library)
            else:
                window.load_library_for_shot(library)
                root.update()
                window.demo_candidates_for_shot()
            root.update()
        root.update_idletasks()
        time.sleep(0.6)
        root.update()
        from PIL import ImageGrab

        x, y = root.winfo_rootx(), root.winfo_rooty()
        w, h = root.winfo_width(), root.winfo_height()
        image = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        image.save(out or log_path)
        lines.append(f"saved {out or log_path} {image.size}")
    except Exception:
        code = 1
        lines.append("SHOT FAILED")
        lines.append(traceback.format_exc())
    try:
        with open(log_path + ".txt", "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
    except OSError:
        pass
    return code


def permcheck(log_path: str, folder: str) -> int:
    """检查当前进程对某个文件夹的写权限，结果写文件。"""
    lines: list[str] = []
    code = 0
    lines.append(f"exe={sys.executable}")
    lines.append(f"cwd={os.getcwd()}")
    lines.append(f"user={os.environ.get('USERNAME')}")
    interesting = {
        k: v for k, v in os.environ.items()
        if any(tag in k.upper() for tag in ("DSH", "SANDBOX", "APPCONTAINER", "SESSIONNAME"))
    }
    lines.append(f"env={interesting}")
    try:
        import ctypes

        count = ctypes.c_ulong()
        ctypes.windll.advapi32.OpenProcessToken(
            ctypes.windll.kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(count)
        )
        size = ctypes.c_ulong(0)
        ctypes.windll.advapi32.GetTokenInformation(
            count, 1, None, 0, ctypes.byref(size)  # TokenUser
        )
        buffer = ctypes.create_string_buffer(size.value)
        if ctypes.windll.advapi32.GetTokenInformation(
            count, 1, buffer, size.value, ctypes.byref(size)
        ):
            sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
            text = ctypes.c_wchar_p()
            ctypes.windll.advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text))
            lines.append(f"token_sid={text.value}")
    except Exception as exc:
        lines.append(f"token_sid 读取失败: {exc}")
    import getpass

    try:
        lines.append(f"getpass={getpass.getuser()}")
    except Exception as exc:
        lines.append(f"getpass failed: {exc}")

    try:
        from .core import fileio

        lines.append(f"已提权(TokenElevation)={fileio.is_elevated()}")
        lines.append(f"完整性级别={fileio.integrity_level()}")
    except Exception as exc:
        lines.append(f"提权检查失败: {exc}")

    for label, target in (
        ("music_folder", folder),
        ("appdata", os.environ.get("APPDATA", "")),
        ("temp", tempfile.gettempdir()),
    ):
        if not target:
            lines.append(f"{label}: 未设置")
            continue
        probe = os.path.join(target, "_mt_permcheck.tmp")
        try:
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("ok")
            os.remove(probe)
            lines.append(f"{label}: 可写 ({target})")
        except Exception as exc:
            code = 1
            lines.append(f"{label}: 不可写 ({target}) -> {type(exc).__name__}: {exc}")
    try:
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
    except OSError:
        code = 1
    return code


def diag(out_dir: str) -> int:
    """出一份诊断报告，写到指定目录。"""
    try:
        from .core import diagnostics

        ctx = open_context()
        path = diagnostics.write_report(ctx, out_dir)
        _log(f"诊断报告已写到 {path}")
        return 0
    except Exception:
        _log("DIAG FAILED:\n" + traceback.format_exc())
        return 1


def write_test(target: str, log_path: str) -> int:
    """对一个真实文件走完整写入链路，结果写日志。用于验证权限问题。"""
    from .core import db, probe
    from .core.models import Track
    from .write import service
    from .write.service import PendingWrite

    lines: list[str] = []
    code = 0
    try:
        ctx = open_context()
        track = Track(path=target, name=os.path.basename(target))
        track.id = db.upsert_track(ctx.conn, track)
        summary = service.write_batch(
            ctx.conn, [PendingWrite(track, {"title": "测试标题"})],
            os.path.dirname(target), fallback_dir=ctx.data_dir,
        )
        lines.append(f"ok={summary['ok']} failed={summary['failed']} "
                     f"via_helper={summary.get('via_helper')}")
        lines.append(f"error={summary.get('error')}")
        if summary.get("items"):
            lines.append(f"item_error={summary['items'][0].error}")
        lines.append(f"title_after={probe.probe(target).title!r}")
        code = 0 if summary["ok"] == 1 else 1
    except Exception:
        code = 1
        lines.append("WRITE TEST FAILED")
        lines.append(traceback.format_exc())
    try:
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
    except OSError:
        code = 1
    return code


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv
    if len(argv) >= 3 and argv[1] == "--selftest":
        return selftest(argv[2])
    if len(argv) >= 3 and argv[1] == "--smoke":
        return smoke(argv[2])
    if len(argv) >= 3 and argv[1] == "--permcheck":
        return permcheck(argv[2], argv[3] if len(argv) >= 4 else os.getcwd())
    if len(argv) >= 4 and argv[1] == "--write-test":
        return write_test(argv[2], argv[3])
    if len(argv) >= 2 and argv[1] == "--diag":
        return diag(argv[2] if len(argv) >= 3 else "")
    if len(argv) >= 3 and argv[1] == "--shot":
        library = argv[3] if len(argv) >= 4 else ""
        return shot(argv[2], library)
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())


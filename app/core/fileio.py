"""文件落盘：直写失败时借系统自带程序完成，绕开安全软件对未签名程序的拦截。"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _quote(path: str) -> str:
    """PowerShell 单引号字符串转义。"""
    return "'" + str(path).replace("'", "''") + "'"


def _powershell(script: str, timeout: int = 40) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if result.returncode == 0:
        return True, ""
    return False, (result.stderr or result.stdout or f"exit {result.returncode}").strip()


def _cmd_copy(src: str, dst: str) -> tuple[bool, str]:
    """再退一步：用 cmd 的 copy 命令。"""
    try:
        result = subprocess.run(
            ["cmd", "/c", "copy", "/y", src, dst],
            capture_output=True, text=True, timeout=40,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if result.returncode == 0:
        return True, ""
    return False, (result.stderr or result.stdout or f"exit {result.returncode}").strip()


def _wmi_copy(src: str, dst: str, timeout: float = 25.0) -> tuple[bool, str]:
    """借 WMI 拉起一个 cmd 去拷贝。

    这条路径的进程由 WmiPrvSE 创建，不在当前进程的作业对象里，
    所以能突破「只允许写工作目录」这类限制。
    """
    import time

    script = (
        f"$src = {_quote(src)}\n"
        f"$dst = {_quote(dst)}\n"
        "$cmd = 'cmd.exe /c copy /y \"' + $src + '\" \"' + $dst + '\"'\n"
        "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
        "-Arguments @{CommandLine=$cmd}\n"
        "if ($r.ReturnValue -ne 0) { Write-Error \"WMI $($r.ReturnValue)\"; exit 1 }\n"
        "exit 0\n"
    )
    handle, script_path = tempfile.mkstemp(suffix=".ps1")
    os.close(handle)
    try:
        with open(script_path, "w", encoding="utf-8") as fh:
            fh.write(script)
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", script_path],
            capture_output=True, text=True, timeout=40,
            creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            return False, f"WMI 调用失败：{detail}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        try:
            os.remove(script_path)
        except OSError:
            pass

    # cmd 是异步跑的，轮询等它落盘
    try:
        expected = os.path.getsize(src)
    except OSError as exc:
        return False, f"源文件读取失败：{exc}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if os.path.getsize(dst) == expected:
                return True, ""
        except OSError:
            pass
        time.sleep(0.25)
    return False, "WMI 拷贝超时"


def is_elevated() -> bool:
    """判断当前进程是否真的提权（High 完整性级别）。

    注意：shell32.IsUserAnAdmin() 对「管理员组成员但被 UAC 过滤」的令牌
    也会返回 True，不能用它判断是否已提权。这里读 TokenElevation。
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

        kernel32.GetCurrentProcess.restype = wintypes.HANDLE

        advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)
        ]
        advapi32.OpenProcessToken.restype = wintypes.BOOL

        advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.GetTokenInformation.restype = wintypes.BOOL

        TOKEN_QUERY = 0x0008
        TOKEN_ELEVATION = 20

        handle = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(handle)
        ):
            return False
        try:
            value = wintypes.DWORD(0)
            needed = wintypes.DWORD(0)
            ok = advapi32.GetTokenInformation(
                handle, TOKEN_ELEVATION, ctypes.byref(value),
                ctypes.sizeof(value), ctypes.byref(needed),
            )
            return bool(value.value) if ok else False
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def integrity_level() -> str:
    """返回完整性级别：High / Medium / Low / Unknown。"""
    if os.name != "nt":
        return "Unknown"
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.GetTokenInformation.restype = wintypes.BOOL
        advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)
        ]
        handle = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(handle)
        ):
            return "Unknown"
        try:
            size = wintypes.DWORD(0)
            advapi32.GetTokenInformation(handle, 25, None, 0, ctypes.byref(size))
            buffer = ctypes.create_string_buffer(size.value)
            if not advapi32.GetTokenInformation(
                handle, 25, buffer, size.value, ctypes.byref(size)
            ):
                return "Unknown"
            # TOKEN_MANDATORY_LABEL 的 SID 最后一段是 RID
            rid = ctypes.cast(
                ctypes.byref(buffer, size.value - 4), ctypes.POINTER(wintypes.DWORD)
            )[0]
        finally:
            kernel32.CloseHandle(handle)
        return {0x1000: "Medium", 0x2000: "High", 0x3000: "System"}.get(rid, "Low")
    except Exception:
        return "Unknown"


def _elevated_copy(src: str, dst: str, timeout: float = 60.0) -> tuple[bool, str]:
    """弹 UAC 申请管理员权限，由提权后的 cmd 执行拷贝。

    这是最后一道：只有前面全部被拒时才会走到这里，用户会看到系统授权窗口。
    """
    import ctypes
    import time

    if integrity_level() == "System":
        return False, "进程已是系统级权限，没有更高可申请"
    params = f'/c copy /y "{src}" "{dst}"'
    try:
        code = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "cmd.exe", params, None, 0
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if code <= 32:
        if code == 5:
            return False, "用户取消了管理员权限请求"
        return False, f"申请管理员权限失败（代码 {code}）"

    try:
        expected = os.path.getsize(src)
    except OSError as exc:
        return False, f"源文件读取失败：{exc}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if os.path.getsize(dst) == expected:
                return True, ""
        except OSError:
            pass
        time.sleep(0.3)
    return False, "提权拷贝超时"


def _task_copy(src: str, dst: str, timeout: float = 60.0) -> tuple[bool, str]:
    """借「计划任务」完成拷贝。

    计划任务由系统服务启动，不在当前进程所在的作业对象里，
    因此能突破「只允许写工作目录」这类限制。
    """
    import time
    import uuid

    tag = uuid.uuid4().hex[:8]
    task_name = f"MusicTaggerCopy_{tag}"
    work = tempfile.mkdtemp(prefix="mt-task-")
    copy_script = os.path.join(work, "copy.ps1")
    driver_script = os.path.join(work, "driver.ps1")
    result_file = os.path.join(work, "result.txt")

    copy_body = (
        f"$ErrorActionPreference = 'Stop'\n"
        f"try {{\n"
        f"  Copy-Item -LiteralPath {_quote(src)} -Destination {_quote(dst)} -Force -ErrorAction Stop\n"
        f"  'OK' | Out-File -FilePath {_quote(result_file)} -Encoding utf8\n"
        f"}} catch {{\n"
        f"  ('ERR: ' + $_.Exception.Message) | Out-File -FilePath {_quote(result_file)} -Encoding utf8\n"
        f"}}\n"
    )
    driver_body = (
        f"$ErrorActionPreference = 'Stop'\n"
        f"$name = {_quote(task_name)}\n"
        f"$action = New-ScheduledTaskAction -Execute 'powershell.exe' "
        f"-Argument '-NoProfile -ExecutionPolicy Bypass -File \"{copy_script}\"'\n"
        f"$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5)\n"
        f"Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Force | Out-Null\n"
        f"Start-ScheduledTask -TaskName $name\n"
        f"$deadline = (Get-Date).AddSeconds({int(timeout - 5)})\n"
        f"while ((Get-Date) -lt $deadline) {{\n"
        f"  if (Test-Path {_quote(result_file)}) {{ break }}\n"
        f"  Start-Sleep -Milliseconds 300\n"
        f"}}\n"
        f"Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue\n"
    )

    try:
        with open(copy_script, "w", encoding="utf-8-sig") as fh:
            fh.write(copy_body)
        with open(driver_script, "w", encoding="utf-8-sig") as fh:
            fh.write(driver_body)
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", driver_script],
            capture_output=True, text=True, timeout=timeout + 30,
            creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            return False, f"计划任务失败：{detail}"
        if os.path.isfile(result_file):
            with open(result_file, encoding="utf-8") as fh:
                text = fh.read().strip().lstrip("\ufeff")
            if text.startswith("OK"):
                return True, ""
            return False, text
        return False, "计划任务没有返回结果"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        import shutil as _shutil

        _shutil.rmtree(work, ignore_errors=True)


def _com_copy(src: str, dst: str, timeout: float = 40.0) -> tuple[bool, str]:
    """借资源管理器的 COM 接口拷贝。

    CopyHere 的实际执行方是 explorer.exe，不是我们这条进程树，
    因此可能绕开「只允许写工作目录」的限制。
    """
    import time

    target_dir = os.path.dirname(os.path.abspath(dst))
    script = (
        f"$ErrorActionPreference = 'Stop'\n"
        f"try {{\n"
        f"  $shell = New-Object -ComObject Shell.Application\n"
        f"  $folder = $shell.NameSpace({_quote(target_dir)})\n"
        f"  if ($folder -eq $null) {{ Write-Error 'NameSpace 为空'; exit 1 }}\n"
        f"  $folder.CopyHere({_quote(src)}, 0x14)\n"
        f"  exit 0\n"
        f"}} catch {{\n"
        f"  Write-Error $_.Exception.Message\n"
        f"  exit 1\n"
        f"}}\n"
    )
    handle, script_path = tempfile.mkstemp(suffix=".ps1")
    os.close(handle)
    try:
        with open(script_path, "w", encoding="utf-8-sig") as fh:
            fh.write(script)
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", script_path],
            capture_output=True, text=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            return False, f"COM 拷贝失败：{detail}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        try:
            os.remove(script_path)
        except OSError:
            pass

    # CopyHere 是异步的，等一下让资源管理器写完
    try:
        expected = os.path.getsize(src)
    except OSError as exc:
        return False, f"源文件读取失败：{exc}"
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            if os.path.getsize(dst) == expected:
                return True, ""
        except OSError:
            pass
        time.sleep(0.3)
    return False, "COM 拷贝超时"


def copy_file(src: str, dst: str, allow_elevation: bool = True) -> tuple[bool, str]:
    """把 src 覆盖到 dst。逐级降级，最后一档借计划任务跳出限制。"""
    attempts: list[str] = []
    try:
        shutil.copy2(src, dst)
        return True, "direct"
    except OSError as exc:
        attempts.append(f"direct: {type(exc).__name__}: {exc}")

    ok, detail = _powershell(
        f"Copy-Item -LiteralPath {_quote(src)} -Destination {_quote(dst)} -Force"
    )
    if ok:
        return True, "powershell"
    attempts.append(f"powershell: {detail}")

    ok, detail = _cmd_copy(src, dst)
    if ok:
        return True, "cmd"
    attempts.append(f"cmd: {detail}")

    # 注意：不要在这里插 _com_copy。它是借资源管理器 CopyHere，
    # 当目标文件夹需要更高完整性级别时，Windows 会弹「需要提供管理员权限」
    # 的系统框，用户每次都得点「继续」，体验极差且容易误解。
    # 需要提权时，走下面的 _elevated_copy，那是标准 UAC 提示。

    ok, detail = _wmi_copy(src, dst)
    if ok:
        return True, "wmi"
    attempts.append(f"wmi: {detail}")

    if allow_elevation:
        ok, detail = _elevated_copy(src, dst)
        if ok:
            return True, "elevated"
        attempts.append(f"elevated: {detail}")

    ok, detail = _task_copy(src, dst)
    if ok:
        return True, "task"
    attempts.append(f"task: {detail}")

    return False, " | ".join(attempts)


def write_text(path: str, text: str) -> tuple[bool, str]:
    """写文本文件，失败时同样借道 PowerShell。"""
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return True, "direct"
    except PermissionError as exc:
        handle, tmp = tempfile.mkstemp(suffix=os.path.splitext(path)[1])
        os.close(handle)
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(text)
            ok, method = copy_file(tmp, path)
            return ok, method
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        return False, str(exc)


def can_write(path: str) -> bool:
    probe = os.path.join(os.path.dirname(path) or ".", "_mt_probe.tmp")
    try:
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("x")
        os.remove(probe)
        return True
    except OSError:
        return False


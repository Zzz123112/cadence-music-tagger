"""借道落盘：安全软件拦截未签名程序时的兜底路径。"""
from __future__ import annotations

import os

import pytest

from app.core import db, fileio, probe
from app.core.models import Track
from app.write import service, writer
from app.write.service import PendingWrite
from app.write.writer import WriteOptions
from conftest import make_wav


def test_copy_file_direct(tmp_path):
    src = tmp_path / "a.bin"
    dst = tmp_path / "b.bin"
    src.write_bytes(b"hello")
    ok, method = fileio.copy_file(str(src), str(dst))
    assert ok and method == "direct"
    assert dst.read_bytes() == b"hello"


def test_copy_file_falls_back_to_powershell(tmp_path, monkeypatch):
    src = tmp_path / "a.bin"
    dst = tmp_path / "b.bin"
    src.write_bytes(b"hello")

    def deny(*_a, **_k):
        raise PermissionError("denied")

    monkeypatch.setattr(fileio.shutil, "copy2", deny)
    monkeypatch.setattr(fileio, "_powershell", lambda script, timeout=40: (True, ""))
    ok, method = fileio.copy_file(str(src), str(dst))
    assert ok is True
    assert method == "powershell"


def test_copy_file_reports_failure(tmp_path, monkeypatch):
    src = tmp_path / "a.bin"
    dst = tmp_path / "b.bin"
    src.write_bytes(b"hello")
    monkeypatch.setattr(fileio.shutil, "copy2",
                        lambda *a, **k: (_ for _ in ()).throw(PermissionError("x")))
    monkeypatch.setattr(fileio, "_powershell",
                        lambda script, timeout=40: (False, "Access denied"))
    monkeypatch.setattr(fileio, "_cmd_copy",
                        lambda s, d: (False, "Access denied"))
    monkeypatch.setattr(fileio, "_wmi_copy",
                        lambda s, d, timeout=25.0: (False, "WMI denied"))
    monkeypatch.setattr(fileio, "_com_copy",
                        lambda s, d, timeout=40.0: (False, "COM denied"))
    monkeypatch.setattr(fileio, "_elevated_copy",
                        lambda s, d, timeout=60.0: (False, "elevated denied"))
    monkeypatch.setattr(fileio, "_task_copy",
                        lambda s, d, timeout=60.0: (False, "task denied"))
    ok, detail = fileio.copy_file(str(src), str(dst))
    assert ok is False
    assert "Access denied" in detail
    assert "WMI denied" in detail
    assert "task denied" in detail


def test_copy_file_falls_back_to_cmd(tmp_path, monkeypatch):
    src = tmp_path / "a.bin"
    dst = tmp_path / "b.bin"
    src.write_bytes(b"hello")
    monkeypatch.setattr(fileio.shutil, "copy2",
                        lambda *a, **k: (_ for _ in ()).throw(PermissionError("x")))
    monkeypatch.setattr(fileio, "_powershell", lambda script, timeout=40: (False, "no"))
    monkeypatch.setattr(fileio, "_cmd_copy", lambda s, d: (True, ""))
    ok, method = fileio.copy_file(str(src), str(dst))
    assert ok is True
    assert method == "cmd"


def test_write_text_falls_back(tmp_path, monkeypatch):
    target = tmp_path / "x.lrc"
    real_open = open

    def fake_open(path, mode="r", *a, **k):
        if "w" in mode and os.path.abspath(str(path)) == os.path.abspath(str(target)):
            raise PermissionError("denied")
        return real_open(path, mode, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)
    monkeypatch.setattr(fileio, "copy_file", lambda s, d, allow_elevation=True: (True, "powershell"))
    ok, method = fileio.write_text(str(target), "歌词")
    assert ok is True
    assert method == "powershell"


def test_write_with_fallback_uses_helper(tmp_path, monkeypatch):
    music = tmp_path / "music"
    path = make_wav(str(music / "a.wav"), seconds=0.2)
    real = writer.write
    calls = {"direct": 0}

    def flaky(target, fields, cover=None, lyrics=None, opts=None):
        if os.path.abspath(target) == os.path.abspath(path):
            calls["direct"] += 1
            raise PermissionError("denied")
        return real(target, fields, cover, lyrics, opts)

    monkeypatch.setattr(service.writer, "write", flaky)
    monkeypatch.setattr(fileio, "copy_file", lambda s, d, allow_elevation=True: (True, "powershell"))

    track = Track(path=path, name="a.wav")
    item = PendingWrite(track=track, fields={"title": "晴天"})
    method = service._write_with_fallback(path, item, WriteOptions())
    assert method == "helper"
    assert calls["direct"] == 1


def test_wmi_copy_real(tmp_path):
    """真实走一次 WMI，确认这条路能落盘。"""
    src = tmp_path / "src.bin"
    dst = tmp_path / "dst.bin"
    src.write_bytes(b"wmi-hello")
    ok, detail = fileio._wmi_copy(str(src), str(dst))
    assert ok is True, detail
    assert dst.read_bytes() == b"wmi-hello"


def test_elevated_copy_cancelled(monkeypatch):
    import ctypes

    monkeypatch.setattr(fileio, "is_elevated", lambda: False)
    monkeypatch.setattr(ctypes.windll.shell32, "ShellExecuteW", lambda *a: 5)
    ok, detail = fileio._elevated_copy("a", "b")
    assert ok is False
    assert "取消" in detail


def test_elevated_copy_skipped_at_system_integrity(monkeypatch):
    """System 完整性级别已经到顶，不该再弹 UAC。"""
    monkeypatch.setattr(fileio, "integrity_level", lambda: "System")
    ok, detail = fileio._elevated_copy("a", "b")
    assert ok is False
    assert "系统级权限" in detail


def test_elevated_copy_attempts_at_high_integrity(tmp_path, monkeypatch):
    """High 级别仍可能写不进（受限令牌），要照常申请 UAC。"""
    import ctypes

    monkeypatch.setattr(fileio, "integrity_level", lambda: "High")
    monkeypatch.setattr(ctypes.windll.shell32, "ShellExecuteW", lambda *a: 5)
    ok, detail = fileio._elevated_copy("a", "b")
    assert ok is False
    assert "取消" in detail


def test_elevated_copy_success(tmp_path, monkeypatch):
    import ctypes
    import shutil as _shutil

    src = tmp_path / "a.bin"
    dst = tmp_path / "b.bin"
    src.write_bytes(b"elevated")

    monkeypatch.setattr(fileio, "is_elevated", lambda: False)

    def fake_shell(*_args):
        _shutil.copy2(src, dst)  # 模拟提权后的 cmd 完成拷贝
        return 42

    monkeypatch.setattr(ctypes.windll.shell32, "ShellExecuteW", fake_shell)
    ok, detail = fileio._elevated_copy(str(src), str(dst))
    assert ok is True, detail
    assert dst.read_bytes() == b"elevated"


def test_copy_file_respects_allow_elevation(tmp_path, monkeypatch):
    src = tmp_path / "a.bin"
    dst = tmp_path / "b.bin"
    src.write_bytes(b"x")
    monkeypatch.setattr(fileio.shutil, "copy2",
                        lambda *a, **k: (_ for _ in ()).throw(PermissionError("x")))
    monkeypatch.setattr(fileio, "_powershell", lambda *a, **k: (False, "no"))
    monkeypatch.setattr(fileio, "_cmd_copy", lambda *a, **k: (False, "no"))
    monkeypatch.setattr(fileio, "_wmi_copy", lambda *a, **k: (False, "no"))
    monkeypatch.setattr(fileio, "_com_copy", lambda *a, **k: (False, "no"))
    monkeypatch.setattr(fileio, "_task_copy", lambda *a, **k: (False, "no"))
    called = []
    monkeypatch.setattr(fileio, "_elevated_copy",
                        lambda s, d: (called.append(1), (False, "skip"))[1])

    ok, _ = fileio.copy_file(str(src), str(dst), allow_elevation=False)
    assert ok is False
    assert called == []

    fileio.copy_file(str(src), str(dst), allow_elevation=True)
    assert called == [1]


def test_copy_file_falls_back_to_wmi(tmp_path, monkeypatch):
    src = tmp_path / "a.bin"
    dst = tmp_path / "b.bin"
    src.write_bytes(b"hello")
    monkeypatch.setattr(fileio.shutil, "copy2",
                        lambda *a, **k: (_ for _ in ()).throw(PermissionError("x")))
    monkeypatch.setattr(fileio, "_powershell", lambda script, timeout=40: (False, "no"))
    monkeypatch.setattr(fileio, "_cmd_copy", lambda s, d: (False, "no"))
    monkeypatch.setattr(fileio, "_wmi_copy", lambda s, d, allow_elevation=True, timeout=25.0: (True, ""))
    ok, method = fileio.copy_file(str(src), str(dst))
    assert ok is True
    assert method == "wmi"


def test_powershell_copy_real_chinese_path(tmp_path, monkeypatch):
    """真实调用 PowerShell 复制，覆盖中文与空格路径的引号转义。"""
    folder = tmp_path / "中文 目录"
    folder.mkdir()
    src = folder / "歌 曲.tmp"
    dst = folder / "结 果.lrc"
    src.write_bytes(b"hello")

    def deny(*_a, **_k):
        raise PermissionError("denied")

    monkeypatch.setattr(fileio.shutil, "copy2", deny)
    ok, method = fileio.copy_file(str(src), str(dst))
    assert ok is True
    assert method == "powershell"
    assert dst.read_bytes() == b"hello"


def test_write_with_fallback_handles_mutagen_error(tmp_path, monkeypatch):
    """mutagen 会把 PermissionError 包成 MutagenError（不继承 OSError）。"""
    music = tmp_path / "music"
    path = make_wav(str(music / "a.wav"), seconds=0.2)
    real = writer.write

    class MutagenError(Exception):
        pass

    def flaky(target, fields, cover=None, lyrics=None, opts=None):
        if os.path.abspath(target) == os.path.abspath(path):
            raise MutagenError("[Errno 13] Permission denied: 'x'")
        return real(target, fields, cover, lyrics, opts)

    monkeypatch.setattr(service.writer, "write", flaky)
    track = Track(path=path, name="a.wav")
    item = PendingWrite(track=track, fields={"title": "晴天"})
    assert service._write_with_fallback(path, item, WriteOptions()) == "helper"
    assert probe.probe(path).title == "晴天"


def test_write_with_fallback_reraises_other_errors(tmp_path, monkeypatch):
    """不是权限问题就不该悄悄走兜底路径。"""
    music = tmp_path / "music"
    path = make_wav(str(music / "a.wav"), seconds=0.2)

    def boom(*_a, **_k):
        raise OSError("磁盘坏了")

    monkeypatch.setattr(service.writer, "write", boom)
    track = Track(path=path, name="a.wav")
    item = PendingWrite(track=track, fields={"title": "x"})
    with pytest.raises(OSError):
        service._write_with_fallback(path, item, WriteOptions())


def test_write_batch_counts_helper_path(conn, tmp_path, monkeypatch):
    music = tmp_path / "music"
    path = make_wav(str(music / "a.wav"), seconds=0.2)
    track = Track(path=path, name="a.wav")
    track.id = db.upsert_track(conn, track)

    real = writer.write

    def flaky(target, fields, cover=None, lyrics=None, opts=None):
        if os.path.abspath(target) == os.path.abspath(path):
            raise PermissionError("denied")
        return real(target, fields, cover, lyrics, opts)

    monkeypatch.setattr(service.writer, "write", flaky)

    summary = service.write_batch(conn, [PendingWrite(track, {"title": "晴天"})], str(music))
    assert summary["ok"] == 1
    assert summary["via_helper"] == 1
    assert probe.probe(path).title == "晴天"



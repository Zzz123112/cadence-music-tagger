"""提权策略与重启流程的测试。

重点覆盖：判据用「能不能写」而不是令牌标志；以及防重启死循环。
"""
from __future__ import annotations

import os

import pytest

from app.core import elevate, elevation_policy


# ---------------------------------------------------------------- 纯函数

def test_can_write_dir_true(tmp_path):
    assert elevation_policy.can_write_dir(str(tmp_path)) is True


def test_can_write_dir_false_for_bad_path(tmp_path):
    bad = tmp_path / "missing" / "deeper"
    assert elevation_policy.can_write_dir(str(bad)) is False


def test_can_write_dir_false_for_empty():
    assert elevation_policy.can_write_dir("") is False


def test_has_attempted_flag():
    assert elevation_policy.has_attempted(["--elevate-attempt"]) is True
    assert elevation_policy.has_attempted(["--other"]) is False
    assert elevation_policy.has_attempted([]) is False


def test_should_auto_elevate_all_conditions_met():
    assert elevation_policy.should_auto_elevate(
        True, "F:\\音乐", [], can_write=lambda p: False
    ) is True


def test_should_auto_elevate_requires_flag():
    assert elevation_policy.should_auto_elevate(
        False, "F:\\音乐", [], can_write=lambda p: False
    ) is False


def test_should_auto_elevate_requires_library():
    assert elevation_policy.should_auto_elevate(
        True, "", [], can_write=lambda p: False
    ) is False


def test_should_auto_elevate_stops_after_attempt():
    """提权重启后的实例不能再重启，否则无限循环。"""
    assert elevation_policy.should_auto_elevate(
        True, "F:\\音乐", ["--elevate-attempt"], can_write=lambda p: False
    ) is False


def test_should_auto_elevate_skips_when_writable():
    assert elevation_policy.should_auto_elevate(
        True, "F:\\音乐", [], can_write=lambda p: True
    ) is False


def test_should_offer_conditions():
    assert elevation_policy.should_offer(
        "F:\\音乐", [], can_write=lambda p: False) is True
    assert elevation_policy.should_offer(
        "F:\\音乐", ["--elevate-attempt"], can_write=lambda p: False) is False
    assert elevation_policy.should_offer(
        "F:\\音乐", [], can_write=lambda p: True) is False
    assert elevation_policy.should_offer(
        "", [], can_write=lambda p: False) is False


# ---------------------------------------------------------------- 重启

def test_relaunch_passes_attempt_flag(monkeypatch):
    import ctypes

    captured = {}

    def fake_shell(hwnd, verb, exe, params, cwd, show):
        captured["verb"] = verb
        captured["exe"] = exe
        captured["params"] = params
        return 42

    monkeypatch.setattr(elevate, "can_relaunch", lambda: True)
    monkeypatch.setattr(ctypes.windll.shell32, "ShellExecuteW", fake_shell)
    ok, message = elevate.relaunch_as_admin()
    assert ok is True, message
    assert captured["verb"] == "runas"
    assert elevation_policy.ATTEMPT_FLAG in captured["params"]


def test_relaunch_cancelled(monkeypatch):
    import ctypes

    monkeypatch.setattr(elevate, "can_relaunch", lambda: True)
    monkeypatch.setattr(ctypes.windll.shell32, "ShellExecuteW", lambda *a: 5)
    ok, message = elevate.relaunch_as_admin()
    assert ok is False
    assert "取消" in message


def test_relaunch_requires_frozen(monkeypatch):
    monkeypatch.setattr(elevate, "can_relaunch", lambda: False)
    ok, message = elevate.relaunch_as_admin()
    assert ok is False
    assert "打包" in message


# ---------------------------------------------------------------- 界面流程

def test_offer_dialog_skipped_when_auto_already_tried(ui, monkeypatch):
    """启动时自动提权试过了（哪怕失败），界面里不能再弹第二次。"""
    import app.ui_tk.main_window as mw

    called = []
    monkeypatch.setattr(mw.messagebox, "askyesnocancel",
                        lambda *a, **k: called.append(1))
    monkeypatch.setattr(elevation_policy, "can_write_dir", lambda p: False)
    ui.ctx.elevate_attempted = True
    ui._elevation_asked = False
    ui._offer_elevation_once()
    assert called == []
    ui.ctx.elevate_attempted = False


def test_offer_dialog_shown_when_auto_not_tried(ui, monkeypatch):
    import app.ui_tk.main_window as mw

    called = []
    monkeypatch.setattr(mw.messagebox, "askyesnocancel",
                        lambda *a, **k: called.append(1) or False)
    monkeypatch.setattr(elevation_policy, "can_write_dir", lambda p: False)
    ui.ctx.elevate_attempted = False
    ui._elevation_asked = False
    ui._offer_elevation_once()
    assert called == [1]


def test_offer_dialog_skipped_when_writable(ui, monkeypatch):
    import app.ui_tk.main_window as mw

    called = []
    monkeypatch.setattr(mw.messagebox, "askyesnocancel",
                        lambda *a, **k: called.append(1))
    monkeypatch.setattr(elevation_policy, "can_write_dir", lambda p: True)
    ui._elevation_asked = False
    ui._offer_elevation_once()
    assert called == []


def test_offer_dialog_skipped_after_attempt(ui, monkeypatch):
    import sys

    import app.ui_tk.main_window as mw

    called = []
    monkeypatch.setattr(mw.messagebox, "askyesnocancel",
                        lambda *a, **k: called.append(1))
    monkeypatch.setattr(elevation_policy, "can_write_dir", lambda p: False)
    monkeypatch.setattr(sys, "argv", ["MusicTagger.exe", "--elevate-attempt"])
    ui._elevation_asked = False
    ui._offer_elevation_once()
    assert called == []


def test_offer_dialog_relaunches_and_saves_flag(ui, monkeypatch):
    import app.ui_tk.main_window as mw

    monkeypatch.setattr(elevation_policy, "can_write_dir", lambda p: False)
    monkeypatch.setattr(mw.messagebox, "askyesnocancel", lambda *a, **k: True)
    relaunched = []
    monkeypatch.setattr(elevate, "relaunch_as_admin",
                        lambda *a, **k: (relaunched.append(1), (True, ""))[1])
    # 成功重启会销毁窗口，测试里屏蔽掉，避免破坏共享的 Tk 根
    monkeypatch.setattr(ui.root, "destroy", lambda *a, **k: None)
    monkeypatch.setattr(ui, "root", ui.root)
    ui._elevation_asked = False
    ui._offer_elevation_once()
    assert relaunched == [1]
    assert ui.ctx.settings["auto_elevate"] is True


def test_offer_dialog_declined_disables_auto(ui, monkeypatch):
    import app.ui_tk.main_window as mw

    monkeypatch.setattr(elevation_policy, "can_write_dir", lambda p: False)
    monkeypatch.setattr(mw.messagebox, "askyesnocancel", lambda *a, **k: False)
    relaunched = []
    monkeypatch.setattr(elevate, "relaunch_as_admin",
                        lambda *a, **k: (relaunched.append(1), (True, ""))[1])
    ui._elevation_asked = False
    ui._offer_elevation_once()
    assert relaunched == []
    assert ui.ctx.settings["auto_elevate"] is False

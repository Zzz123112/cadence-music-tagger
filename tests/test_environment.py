"""安全软件检测与权限建议。"""
from __future__ import annotations

from app.core import environment


def test_detect_antivirus_maps_known_processes(monkeypatch):
    monkeypatch.setattr(environment, "_running_processes",
                        lambda: {"explorer.exe", "avp", "chrome"})
    assert environment.detect_antivirus() == ["卡巴斯基"]


def test_detect_antivirus_empty_when_none(monkeypatch):
    monkeypatch.setattr(environment, "_running_processes", lambda: {"explorer", "cmd"})
    assert environment.detect_antivirus() == []


def test_advice_mentions_trusted_group_for_kaspersky():
    text = environment.advice_for_permission_denied(["卡巴斯基"])
    assert "受信任" in text
    assert "应用程序控制" in text


def test_advice_without_av_suggests_admin():
    text = environment.advice_for_permission_denied([])
    assert "管理员" in text


def test_advice_lists_multiple_products():
    text = environment.advice_for_permission_denied(["卡巴斯基", "火绒"])
    assert "卡巴斯基" in text and "火绒" in text


def test_running_processes_returns_set():
    names = environment._running_processes()
    assert isinstance(names, set)

"""「始终以管理员身份启动」的注册表标记测试。"""
from __future__ import annotations

import os

import pytest

from app.core import elevate


@pytest.mark.skipif(os.name != "nt", reason="Windows 专属")
def test_set_and_clear_always_admin(tmp_path, monkeypatch):
    """写入后能读回，删除后读不到——用临时路径模拟 exe，避免污染真实注册表。"""
    fake_exe = str(tmp_path / "FakeMusicTagger.exe")
    (tmp_path / "FakeMusicTagger.exe").write_text("stub", encoding="utf-8")
    monkeypatch.setattr(elevate, "_target_exe", lambda: fake_exe)

    assert elevate.is_always_admin() is False

    ok, error = elevate.set_always_admin(True)
    assert ok is True, error
    assert elevate.is_always_admin() is True

    ok, error = elevate.set_always_admin(False)
    assert ok is True, error
    assert elevate.is_always_admin() is False


@pytest.mark.skipif(os.name != "nt", reason="Windows 专属")
def test_clear_when_absent_is_ok(tmp_path, monkeypatch):
    fake_exe = str(tmp_path / "NeverSet.exe")
    monkeypatch.setattr(elevate, "_target_exe", lambda: fake_exe)
    ok, error = elevate.set_always_admin(False)
    assert ok is True, error

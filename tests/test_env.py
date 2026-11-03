"""数据目录与启动健壮性测试（修复「打不开」的直接原因）。"""
from __future__ import annotations

import os

import pytest

from app.ui_tk import state


@pytest.fixture(autouse=True)
def _reset_cache():
    state._app_dir_cache = None
    yield
    state._app_dir_cache = None


def test_app_data_dir_is_writable():
    path = state.app_data_dir()
    assert os.path.isdir(path)
    assert state._is_writable(path)


def test_data_dir_skips_unwritable_candidate(monkeypatch, tmp_path):
    blocked = tmp_path / "roaming"
    good = tmp_path / "local"
    monkeypatch.setattr(
        state, "data_dir_candidates", lambda: [str(blocked), str(good)]
    )
    monkeypatch.setattr(state, "_is_writable", lambda p: p.endswith("local"))
    assert state.app_data_dir() == str(good)


def test_data_dir_raises_when_nothing_writable(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "data_dir_candidates", lambda: [str(tmp_path / "a")])
    monkeypatch.setattr(state, "_is_writable", lambda p: False)
    with pytest.raises(RuntimeError):
        state.app_data_dir()


def test_open_context_creates_db_in_data_dir(tmp_path):
    ctx = state.open_context(data_dir=str(tmp_path / "data"))
    try:
        assert os.path.isfile(str(tmp_path / "data" / "index.db"))
        assert ctx.conn is not None
    finally:
        ctx.conn.close()


def test_settings_saved_into_data_dir(tmp_path):
    ctx = state.open_context(data_dir=str(tmp_path / "data"))
    try:
        ctx.settings["continuous"] = False
        ctx.save_settings()
        path = os.path.join(str(tmp_path / "data"), "settings.json")
        assert os.path.isfile(path)
        with open(path, encoding="utf-8") as fh:
            assert '"continuous": false' in fh.read()
    finally:
        ctx.conn.close()

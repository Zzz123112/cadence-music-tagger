"""阶段 0 验证固化为测试：样本文件真实写入后，音频区字节必须不变（B-01）。"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import format_matrix  # noqa: E402

SAMPLES = format_matrix.SAMPLES
ALLOWED = {"可写（全通过）", "可写（部分字段）", "只读"}


def sample_files():
    """没有本地 samples/ 时返回占位项，让测试正常跳过而不是收集阶段报错。"""
    if not os.path.isdir(SAMPLES):
        return [None]
    found = sorted(
        os.path.join(SAMPLES, n) for n in os.listdir(SAMPLES)
        if os.path.isfile(os.path.join(SAMPLES, n))
    )
    return found or [None]


@pytest.mark.parametrize("path", sample_files(), ids=lambda p: os.path.basename(p) if p else "no-samples")
def test_sample_write_does_not_touch_audio(path):
    if path is None:
        pytest.skip("需要本地 samples/ 样本文件")
    row = format_matrix.test_one(path)
    assert row["verdict"] in ALLOWED, f"{row['file']}: {row['verdict']} {row['note']}"
    if row["verdict"] == "只读":
        pytest.skip("该格式按设计只读")
    assert row["tags_ok"], "标签没写进去"
    assert row["params_ok"], "音频参数被改动"
    if row["span"]:
        assert row["audio_hash_ok"], "音频数据区字节被改动"


def test_matrix_covers_core_formats():
    """PRD 要求至少覆盖 FLAC / WAV / DSF / AIFF 这几种。"""
    files = [p for p in sample_files() if p]
    if not files:
        pytest.skip("需要本地 samples/ 样本文件")
    formats = {format_matrix.test_one(p)["format"] for p in files}
    assert {"FLAC", "WAV", "DSF", "AIFF"} <= formats

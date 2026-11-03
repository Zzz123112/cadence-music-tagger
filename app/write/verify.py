"""写入后的重读校验。参数不一致即视为失败，触发回滚。"""
from __future__ import annotations

from ..core.models import ProbeResult
from ..core.probe import probe

DURATION_TOLERANCE = 0.05


def compare(before: ProbeResult, after: ProbeResult) -> tuple[bool, str]:
    if after.error:
        return False, f"重读失败：{after.error}"
    if before.bit_depth and after.bit_depth and before.bit_depth != after.bit_depth:
        return False, f"位深改变：{before.bit_depth} → {after.bit_depth}"
    if before.sample_rate and after.sample_rate and before.sample_rate != after.sample_rate:
        return False, f"采样率改变：{before.sample_rate} → {after.sample_rate}"
    if before.channels and after.channels and before.channels != after.channels:
        return False, f"声道数改变：{before.channels} → {after.channels}"
    if before.duration and after.duration:
        if abs(before.duration - after.duration) > DURATION_TOLERANCE:
            return False, f"时长改变：{before.duration:.2f} → {after.duration:.2f}"
    return True, ""


def verify(before: ProbeResult, path: str) -> tuple[bool, str]:
    return compare(before, probe(path))

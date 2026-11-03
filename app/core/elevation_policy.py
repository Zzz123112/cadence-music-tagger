"""提权策略：纯函数，便于测试。

不依赖令牌标志位（TokenElevation 在不同机器上表现不一致），
统一以「目标目录能不能写」作为唯一判据。
"""
from __future__ import annotations

import os

ATTEMPT_FLAG = "--elevate-attempt"


def has_attempted(argv: list[str]) -> bool:
    """本次启动是不是「提权重启」之后的实例。"""
    return ATTEMPT_FLAG in (argv or [])


def can_write_dir(path: str) -> bool:
    """通过真实写探针判断目录是否可写。"""
    if not path:
        return False
    probe = os.path.join(path, "_mt_probe.tmp")
    try:
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("x")
        os.remove(probe)
        return True
    except OSError:
        return False


def should_auto_elevate(
    auto_flag: bool,
    library_root: str,
    argv: list[str],
    can_write=None,
) -> bool:
    """启动时要不要直接提权重启。

    四个条件缺一不可：
      1. 用户勾了自动提权
      2. 配了音乐文件夹
      3. 当前写不进去
      4. 这次不是提权重启后的实例（防止无限重启）
    """
    if not auto_flag:
        return False
    if not library_root:
        return False
    if has_attempted(argv):
        return False
    can_write = can_write or can_write_dir
    return not can_write(library_root)


def should_offer(
    library_root: str,
    argv: list[str],
    can_write=None,
) -> bool:
    """启动时要不要弹「以管理员身份重启」的询问框。"""
    if not library_root:
        return False
    if has_attempted(argv):
        return False
    can_write = can_write or can_write_dir
    return not can_write(library_root)

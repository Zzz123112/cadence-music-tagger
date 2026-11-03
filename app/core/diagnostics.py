"""诊断报告：把环境、权限、错误记录汇总成一段文本。"""
from __future__ import annotations

import os
import platform
import sys


def _writable(path: str) -> tuple[bool, str]:
    probe = os.path.join(path, "_mt_diag.tmp")
    try:
        os.makedirs(path, exist_ok=True)
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("x")
        os.remove(probe)
        return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def build_report(ctx) -> str:
    lines: list[str] = []
    lines.append("=== 基本信息 ===")
    lines.append(f"程序: {sys.executable}")
    lines.append(f"打包运行: {getattr(sys, 'frozen', False)}")
    lines.append(f"Python: {sys.version.split()[0]}")
    lines.append(f"系统: {platform.platform()}")
    lines.append(f"用户: {os.environ.get('USERNAME')}")
    marks = {
        k: v for k, v in os.environ.items()
        if any(t in k.upper() for t in ("DSH", "SANDBOX", "SESSIONNAME"))
    }
    lines.append(f"环境标记: {marks}")
    try:
        from .environment import detect_antivirus

        lines.append(f"检测到的安全软件: {detect_antivirus() or '未发现'}")
    except Exception as exc:
        lines.append(f"安全软件检测失败: {exc}")
    lines.append("")

    lines.append("=== 路径与权限 ===")
    targets = [
        ("音乐文件夹", getattr(ctx, "library_root", "") or ""),
        ("数据目录", getattr(ctx, "data_dir", "") or ""),
    ]
    root = getattr(ctx, "library_root", "") or ""
    if getattr(sys, "frozen", False):
        targets.append(("程序目录", os.path.dirname(sys.executable)))
    for label, path in targets:
        if not path:
            lines.append(f"{label}: 未设置")
            continue
        ok, error = _writable(path)
        lines.append(f"{label}: {'可写' if ok else '不可写（' + error + '）'}  {path}")
    lines.append("")

    lines.append("=== 曲库 ===")
    try:
        from . import db

        counts = db.count_by_status(ctx.conn)
        lines.append(f"共 {counts.get('all', 0)} 首，状态分布 {counts}")
    except Exception as exc:
        lines.append(f"统计失败: {exc}")
    lines.append("")

    lines.append("=== 最近的写入错误 ===")
    log_path = os.path.join(getattr(ctx, "data_dir", "") or "", "write.log")
    try:
        with open(log_path, encoding="utf-8") as fh:
            tail = fh.readlines()[-40:]
        lines.extend(line.rstrip() for line in tail) if tail else lines.append("（无）")
    except OSError:
        lines.append("（没有错误记录）")

    return "\n".join(lines)


def write_report(ctx, out_dir: str = "") -> str:
    text = build_report(ctx)
    target_dir = out_dir or getattr(ctx, "data_dir", "") or os.getcwd()
    # 批处理传进来的路径可能带尾反斜杠或引号，先清洗
    target_dir = target_dir.strip().strip('"').rstrip("\\/") or os.getcwd()
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, "diag-report.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


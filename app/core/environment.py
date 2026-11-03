"""运行环境探测：安全软件、受限会话。"""
from __future__ import annotations

import os
import subprocess

# 进程名 -> 显示名
KNOWN_AV = {
    "avp": "卡巴斯基",
    "avpui": "卡巴斯基",
    "kavfs": "卡巴斯基",
    "360tray": "360 安全卫士",
    "360sd": "360 杀毒",
    "zhudongfangyu": "360 主动防御",
    "hipstray": "火绒",
    "usysdiag": "火绒",
    "wsctrl": "火绒",
    "qqpctray": "腾讯电脑管家",
    "qqpcrtp": "腾讯电脑管家",
    "kxetray": "金山毒霸",
    "avastui": "Avast",
    "avgui": "AVG",
    "nortonsecurity": "Norton",
    "mcafee": "McAfee",
    "ekrn": "ESET",
}


def _running_processes() -> set[str]:
    if os.name != "nt":
        return set()
    try:
        result = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return set()
    names = set()
    for line in (result.stdout or "").splitlines():
        if line.startswith('"'):
            executable = line.split(",")[0].strip('"').lower()
            names.add(os.path.splitext(executable)[0])
    return names


def detect_antivirus() -> list[str]:
    """返回正在运行的安全软件名称列表（去重）。"""
    running = _running_processes()
    found = {label for proc, label in KNOWN_AV.items() if proc in running}
    return sorted(found)


def advice_for_permission_denied(av: list[str] | None = None) -> str:
    """写不进去时，按环境给出具体建议。"""
    av = detect_antivirus() if av is None else av
    lines = []
    if av:
        names = "、".join(av)
        lines.append(
            f"检测到 {names} 正在运行。安全软件的「应用程序控制 / 受保护文件夹」"
            "经常会把未签名的程序挡在用户目录之外。"
        )
        if "卡巴斯基" in names:
            lines.append(
                "卡巴斯基的改法：设置 → 安全 → 应用程序控制 → 找到 MusicTagger → "
                "把它移到「受信任」组。"
            )
        lines.append("也可以先右键程序选「以管理员身份运行」试试，通常能绕过这一限制。")
    else:
        lines.append("系统拒绝写入。可以右键程序选「以管理员身份运行」，或换一个有写权限的目录。")
    return "\n".join(lines)

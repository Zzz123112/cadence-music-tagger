"""把程序打包成"解压即用"的压缩包。

产物：dist_zip/Cadence-1.0.0-portable.zip
内容：Cadence\（程序本体）+ 使用说明.txt + 启动.bat
"""
from __future__ import annotations

import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(ROOT, "Cadence")
if not os.path.isdir(APP_DIR):
    APP_DIR = os.path.join(ROOT, "CadenceApp")
README = os.path.join(ROOT, "installer", "readme_portable.txt")
OUT_DIR = os.path.join(ROOT, "dist_zip")
NAME = "Cadence-1.0.0"

SKIP_DIRS = {"MusicTaggerData", "__pycache__", ".pytest_cache"}

LAUNCHER = """@echo off
cd /d "%~dp0Cadence"
start "" "Cadence.exe"
"""


def main() -> int:
    if not os.path.isdir(APP_DIR):
        raise SystemExit("找不到程序目录，请先打包程序本体")
    os.makedirs(OUT_DIR, exist_ok=True)
    target = os.path.join(OUT_DIR, f"{NAME}-portable.zip")
    if os.path.isfile(target):
        os.remove(target)

    files = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for base, dirs, names in os.walk(APP_DIR):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in names:
                full = os.path.join(base, name)
                rel = os.path.relpath(full, APP_DIR)
                zf.write(full, os.path.join("Cadence", rel))
                files += 1
        zf.writestr("使用说明.txt", open(README, encoding="utf-8").read())
        zf.writestr("启动 Cadence.bat", LAUNCHER)

    size = os.path.getsize(target)
    print(f"{files} 个文件 -> {target}  ({size/1048576:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""设置（毛玻璃版）：数据源、权限、写入、诊断。"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox

from . import visual
from .glass_dialog import GlassDialog, _blend


class GlassSettingsDialog(GlassDialog):
    def __init__(self, parent, ctx, window=None):
        super().__init__(parent, "设置", width=520, height=600)
        self.ctx = ctx
        self.window = window
        self._vars: dict[str, tk.BooleanVar] = {}
        self._build()

    # ------------------------------------------------------------ 界面

    def _build(self) -> None:
        body = self.body

        self.section(body, "数据源", pady=(0, 3))
        from ..providers import PROVIDER_NAMES

        for name in PROVIDER_NAMES:
            value = bool(self.ctx.settings.get("sources", {}).get(name, True))
            self._vars[f"source:{name}"] = self.check(
                body, name, value, self._save)

        self.section(body, "权限")
        hint = tk.Label(
            body,
            text="保存时若系统提示需要权限，点 UAC 的「是」即可；\n"
                 "也可以双击「以管理员身份启动.bat」启动本程序。",
            bg=self.bg, fg=self.palette["text2"], font=(visual.FONT, 8),
            anchor="w", justify="left",
        )
        hint.pack(fill="x")

        self.section(body, "写入")
        for key, label in (
            ("write_cover", "封面内嵌进文件"),
            ("write_cover_file", "同时生成 cover.jpg"),
            ("write_lrc", "同时生成 .lrc"),
            ("replace_cover", "替换文件中原有的封面"),
        ):
            self._vars[key] = self.check(
                body, label, bool(self.ctx.settings.get(key, True)), self._save)

        foot = tk.Frame(body, bg=self.bg)
        foot.pack(fill="x", pady=(4, 0))
        self.button(foot, "权限自检", self.check_permissions).pack(side="left")
        self.button(foot, "导出诊断报告", self.export_diagnostics).pack(side="left", padx=6)
        self.button(foot, "关闭", self.close, primary=True).pack(side="right")

    # ------------------------------------------------------------ 保存

    def _save(self) -> None:
        sources = {}
        for key, var in self._vars.items():
            if key.startswith("source:"):
                sources[key.split(":", 1)[1]] = var.get()
            else:
                self.ctx.settings[key] = var.get()
        if sources:
            self.ctx.settings["sources"] = sources
        self.ctx.save_settings()

    # ------------------------------------------------------------ 诊断

    def export_diagnostics(self) -> None:
        from ..core import diagnostics

        try:
            text = diagnostics.build_report(self.ctx)
            target = diagnostics.write_report(self.ctx)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        messagebox.showinfo(
            "诊断报告已生成",
            f"已保存到：\n{target}\n\n内容也已复制到剪贴板。",
            parent=self,
        )

    def check_permissions(self) -> None:
        lines = []
        for label, path in (("音乐文件夹", self.ctx.library_root),
                            ("程序数据目录", self.ctx.data_dir)):
            if not path:
                lines.append(f"{label}：未设置")
                continue
            probe = os.path.join(path, "_mt_perm.tmp")
            try:
                with open(probe, "w", encoding="utf-8") as fh:
                    fh.write("ok")
                os.remove(probe)
                lines.append(f"{label}：可写\n　{path}")
            except Exception as exc:
                lines.append(f"{label}：不可写（{type(exc).__name__}）\n　{path}")
        messagebox.showinfo("权限自检", "\n".join(lines), parent=self)

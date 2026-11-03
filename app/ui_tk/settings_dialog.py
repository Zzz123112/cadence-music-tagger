"""设置对话框：数据源、写入行为、备份与历史还原。

主界面只留操作，其余都收在这里。
"""
from __future__ import annotations

import os
import time
import tkinter as tk
from tkinter import messagebox, ttk

from ..core import db
from ..write import service
from . import style as S
from .style import flat_button


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, ctx, window):
        super().__init__(parent)
        self.ctx = ctx
        self.window = window
        self.title("设置")
        self.geometry("520x640")
        self.configure(bg=S.CARD)
        self.transient(parent)
        self.grab_set()

        self._vars: dict[str, tk.BooleanVar] = {}
        self._build()

    def _build(self) -> None:
        pad = tk.Frame(self, bg=S.CARD)
        pad.pack(fill="both", expand=True, padx=18, pady=14)

        tk.Label(pad, text="数据源", bg=S.CARD, fg=S.T3,
                 font=S.FONT_SMALL, anchor="w").pack(fill="x", pady=(0, 4))
        for name in ("MusicBrainz", "网易云", "QQ音乐"):
            var = tk.BooleanVar(value=bool(self.ctx.settings.get("sources", {}).get(name, True)))
            var.trace_add("write", lambda *_a, n=name: self._save())
            ttk.Checkbutton(pad, text=name, variable=var).pack(anchor="w")
            self._vars[f"source:{name}"] = var

        tk.Label(pad, text="权限", bg=S.CARD, fg=S.T3,
                 font=S.FONT_SMALL, anchor="w").pack(fill="x", pady=(12, 4))
        elevate_var = tk.BooleanVar(value=bool(self.ctx.settings.get("auto_elevate", False)))
        elevate_var.trace_add("write", lambda *_a, v=elevate_var: self._set_auto_elevate(v))
        ttk.Checkbutton(
            pad, text="启动时自动申请管理员权限（保存不再弹窗）", variable=elevate_var
        ).pack(anchor="w")
        self._vars["auto_elevate"] = elevate_var

        from ..core import elevate

        always_var = tk.BooleanVar(value=elevate.is_always_admin())
        always_var.trace_add("write",
                             lambda *_a, v=always_var: self._set_always_admin(v))
        ttk.Checkbutton(
            pad,
            text="让本程序始终以管理员启动（推荐，双击一次授权即可）",
            variable=always_var,
        ).pack(anchor="w")
        self._vars["always_admin"] = always_var

        tk.Label(pad, text="写入", bg=S.CARD, fg=S.T3,
                 font=S.FONT_SMALL, anchor="w").pack(fill="x", pady=(12, 4))
        for key, label in (
            ("write_cover", "封面内嵌进文件"),
            ("write_cover_file", "同时生成 cover.jpg"),
            ("write_lrc", "同时生成 .lrc"),
            ("replace_cover", "替换文件中原有的封面"),
        ):
            var = tk.BooleanVar(value=bool(self.ctx.settings.get(key, True)))
            var.trace_add("write", lambda *_a, k=key: self._save())
            ttk.Checkbutton(pad, text=label, variable=var).pack(anchor="w")
            self._vars[key] = var

        foot = tk.Frame(pad, bg=S.CARD)
        foot.pack(fill="x", pady=(8, 0))
        flat_button(foot, "权限自检", self.check_permissions, bg=S.H2,
                    font=S.FONT_SMALL).pack(side="left")
        flat_button(foot, "导出诊断报告", self.export_diagnostics, bg=S.H2,
                    font=S.FONT_SMALL).pack(side="left", padx=6)
        flat_button(foot, "关闭", self.destroy, bg=S.H2,
                    font=S.FONT_SMALL).pack(side="right")

    # ------------------------------------------------------------- 诊断

    def export_diagnostics(self) -> None:
        """把环境、权限、最近错误打包成一个文本文件，方便发出来。"""
        from ..core import diagnostics

        try:
            text = diagnostics.build_report(self.ctx)
            target = diagnostics.write_report(self.ctx)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))
            return

        self.clipboard_clear()
        self.clipboard_append(text)
        messagebox.showinfo(
            "诊断报告已生成",
            f"已保存到：\n{target}\n\n内容也已复制到剪贴板，直接粘贴发给我即可。",
        )

    # ------------------------------------------------------------- 权限

    def check_permissions(self) -> None:
        """检查程序对关键目录有没有写权限——写不进去时先看这里。"""
        targets = [
            ("音乐文件夹", self.ctx.library_root),
            ("程序数据目录", self.ctx.data_dir),
        ]
        lines = []
        for label, path in targets:
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
        lines.append(
            "\n如果音乐文件夹显示不可写：\n"
            "· 换一个有写权限的位置，或\n"
            "· 右键程序「以管理员身份运行」"
        )
        messagebox.showinfo("权限自检", "\n".join(lines))

    # ------------------------------------------------------------- 行为

    def _set_always_admin(self, var) -> None:
        from ..core import elevate

        wanted = bool(var.get())
        ok, error = elevate.set_always_admin(wanted)
        if not ok:
            messagebox.showerror("设置失败", error)
            return
        if wanted:
            messagebox.showinfo(
                "已设置",
                "下次双击启动时会请求一次管理员权限。\n"
                "授权之后，保存标签就不再弹窗了。",
            )

    def _set_auto_elevate(self, var) -> None:
        self.ctx.settings["auto_elevate"] = bool(var.get())
        self.ctx.save_settings()
        if var.get():
            from ..core import elevate, fileio

            if not fileio.is_elevated():
                ok, message = elevate.relaunch_as_admin()
                if ok:
                    self.window.root.destroy()
                else:
                    messagebox.showinfo("提权失败", message)

    def _save(self) -> None:
        for key, var in self._vars.items():
            if key.startswith("source:"):
                self.ctx.settings.setdefault("sources", {})[key.split(":", 1)[1]] = var.get()
            else:
                self.ctx.settings[key] = var.get()
        self.ctx.save_settings()

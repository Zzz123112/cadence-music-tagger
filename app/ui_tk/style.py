"""界面配色与 ttk 样式。对应线框图 v2 的浅色 Apple 风格。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

BG = "#F5F5F7"
CARD = "#FFFFFF"
HAIR = "#ECECEF"
H2 = "#F4F4F6"
T1 = "#1D1D1F"
T2 = "#6E6E73"
T3 = "#86868B"
BLUE = "#0071E3"
BLUE_BG = "#E8F0FE"
ORANGE = "#C97400"
ORANGE_BG = "#FFF6E8"
GREEN = "#1E7B3C"
GREEN_BG = "#EAF7EE"
RED = "#C7362E"
RED_BG = "#FFECEA"

FONT = ("Microsoft YaHei UI", 9)
FONT_BOLD = ("Microsoft YaHei UI", 9, "bold")
FONT_TITLE = ("Microsoft YaHei UI", 18, "bold")
FONT_H2 = ("Microsoft YaHei UI", 11, "bold")
FONT_SMALL = ("Microsoft YaHei UI", 8)
FONT_MONO = ("Consolas", 9)

STATUS_TAGS = {
    "ready": GREEN,
    "no_cover": ORANGE,
    "no_lyrics": ORANGE,
    "empty": RED,
    "conflict": RED,
    "ignored": T2,
}


def setup(root: tk.Tk) -> None:
    style = ttk.Style(root)
    for theme in ("clam", "vista", "default"):
        if theme in style.theme_names():
            style.theme_use(theme)
            break

    style.configure(".", font=FONT, background=CARD, foreground=T1)
    style.configure("TFrame", background=CARD)
    style.configure("Side.TFrame", background=BG)
    style.configure("Top.TFrame", background="#FAFAFC")
    style.configure("TLabel", background=CARD, foreground=T1, font=FONT)
    style.configure("Muted.TLabel", background=CARD, foreground=T2, font=FONT_SMALL)
    style.configure("Title.TLabel", background=CARD, foreground=T1, font=FONT_TITLE)
    style.configure("H2.TLabel", background=CARD, foreground=T1, font=FONT_H2)
    style.configure("Section.TLabel", background=CARD, foreground=T3, font=FONT_SMALL)
    style.configure("Side.TLabel", background=BG, foreground=T2, font=FONT_SMALL)

    style.configure(
        "TButton",
        background=H2,
        foreground=T1,
        borderwidth=0,
        focusthickness=0,
        padding=(12, 5),
        font=FONT,
        relief="flat",
    )
    style.map(
        "TButton",
        background=[("active", "#EAEAEF"), ("pressed", "#E0E0E6")],
        foreground=[("disabled", T3)],
    )
    style.configure(
        "Primary.TButton",
        background=BLUE,
        foreground="#FFFFFF",
        font=FONT_BOLD,
        padding=(14, 6),
    )
    style.map("Primary.TButton", background=[("active", "#0062C4")])

    style.configure("TEntry", fieldbackground=H2, borderwidth=0, padding=5)
    style.configure("TCombobox", fieldbackground=H2, borderwidth=0, padding=4)

    style.configure(
        "Treeview",
        background=CARD,
        fieldbackground=CARD,
        foreground=T1,
        borderwidth=0,
        rowheight=26,
        font=FONT,
    )
    style.configure(
        "Treeview.Heading",
        background=CARD,
        foreground=T3,
        font=FONT_SMALL,
        relief="flat",
    )
    style.map(
        "Treeview",
        background=[("selected", BLUE_BG)],
        foreground=[("selected", T1)],
    )
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    style.configure("TNotebook", background=CARD, borderwidth=0)
    style.configure("TNotebook.Tab", background=H2, padding=(14, 6), font=FONT)
    style.map("TNotebook.Tab", background=[("selected", CARD)])

    style.configure("TCheckbutton", background=CARD, font=FONT)


def flat_button(parent, text, command, bg=H2, fg=T1, font=FONT, **kw) -> tk.Button:
    return tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        font=font,
        relief="flat",
        bd=0,
        activebackground=H2,
        activeforeground=T1,
        highlightthickness=0,
        cursor="hand2",
        padx=10,
        pady=5,
        **kw,
    )

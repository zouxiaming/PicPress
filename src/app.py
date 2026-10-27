# -*- coding: utf-8 -*-
"""
PicPress · 图片批量压缩（Windows 桌面端）
=========================================
纯 tkinter 自绘界面，无第三方 UI 依赖。设计语言取自 Vercel：
白色画布 + 墨黑主色 + 1px 细线分隔 + 胶囊主按钮 + 几何无衬线字。

功能：
  · 批量添加文件 / 文件夹，支持拖拽入窗
  · 目标体积精确到 MB（也可用 KB 预设）
  · 输出格式：保持原格式 / JPEG / WebP / PNG
  · 保存方式：输出到文件夹，或直接替换原图（可自动备份）
  · 实时逐文件进度、体积节省统计
"""

from __future__ import annotations

import ctypes
import json
import math
import os
import queue
import subprocess
import sys
import threading
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

from compressor import (
    EXT_OF_FMT,
    SUPPORTED_EXTS,
    Options,
    collect_images,
    compress_one,
    human_size,
    make_backup_root,
    mb_to_bytes,
)

import shellmenu

APP_NAME = "PicPress"
APP_TITLE = "PicPress · 图片批量压缩"
VERSION = "1.0.0"

# ---------------------------------------------------------------- 设计令牌（Vercel）

C = {
    "canvas": "#ffffff",
    "soft": "#fafafa",
    "soft2": "#f5f5f5",
    "ink": "#171717",
    "body": "#4d4d4d",
    "mute": "#888888",
    "hair": "#ebebeb",
    "hair_strong": "#a1a1a1",
    "link": "#0070f3",
    "link_soft": "#d3e5ff",
    "err": "#ee0000",
    "err_soft": "#f7d4d6",
    "warn": "#f5a623",
    "violet": "#7928ca",
    "cyan": "#50e3c2",
}

FAM = "Microsoft YaHei UI"
MONO = "Consolas"

SC = 1.0  # DPI 缩放系数，init_ui() 里计算


def px(n: float) -> int:
    return max(1, int(round(n * SC)))


def ft(size: float, weight: str = "normal", mono: bool = False, family: str | None = None):
    """以像素为单位的字体元组（负值 = 像素，避免 Tk 二次缩放）。"""
    fam = family or (MONO if mono else FAM)
    return (fam, -max(8, int(round(size * SC))), weight)


def rrect(cv: tk.Canvas, x1, y1, x2, y2, r, fill="", outline="", width=1, tags=()):
    """
    在 Canvas 上画精确圆角矩形。

    注意：不能用 create_polygon(smooth=True) —— Tk 的样条曲线不经过控制点，
    长边的中段会被拉向内部，导致边框看起来「缺一截」。这里改成解析计算圆角路径。
    """
    r = max(0.0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    if outline:
        # 1px 描边要落在整像素行/列上，否则会被摊成两行半透明灰（视觉上像「边框缺一截」）
        x1, y1, x2, y2 = round(x1), round(y1), round(x2) - 1, round(y2) - 1
    if r < 0.9:
        return cv.create_rectangle(x1, y1, x2, y2, fill=fill, outline=outline,
                                   width=width, tags=tags)
    steps = 7
    pts = []
    for cx, cy, a0 in ((x1 + r, y1 + r, 180), (x2 - r, y1 + r, 270),
                       (x2 - r, y2 - r, 0), (x1 + r, y2 - r, 90)):
        for i in range(steps + 1):
            a = math.radians(a0 + 90.0 * i / steps)
            pts.append(cx + r * math.cos(a))
            pts.append(cy + r * math.sin(a))
    return cv.create_polygon(pts, fill=fill, outline=outline or "", width=width, tags=tags)


# ---------------------------------------------------------------- 矢量图标（不用 emoji）

def icon(cv: tk.Canvas, name: str, cx: float, cy: float, size: float, color: str, width: float = 1.6):
    """在 (cx, cy) 处绘制 size 大小的线性矢量图标。"""
    s = size / 2.0
    w = max(1.0, width * SC)
    if name == "plus":
        cv.create_line(cx - s, cy, cx + s, cy, fill=color, width=w, capstyle="round")
        cv.create_line(cx, cy - s, cx, cy + s, fill=color, width=w, capstyle="round")
    elif name == "folder-add":
        cv.create_line(cx - s, cy - s * 0.55, cx - s * 0.25, cy - s * 0.55, fill=color, width=w)
        cv.create_line(cx - s * 0.25, cy - s * 0.55, cx - s * 0.05, cy - s * 0.2, fill=color, width=w)
        cv.create_line(cx - s * 0.05, cy - s * 0.2, cx + s, cy - s * 0.2, fill=color, width=w)
        cv.create_line(cx + s, cy - s * 0.2, cx + s, cy + s * 0.6, fill=color, width=w, capstyle="round")
        cv.create_line(cx + s, cy + s * 0.6, cx - s, cy + s * 0.6, fill=color, width=w, capstyle="round")
        cv.create_line(cx - s, cy + s * 0.6, cx - s, cy - s * 0.55, fill=color, width=w, capstyle="round")
        cv.create_line(cx, cy + s * 0.12, cx, cy + s * 0.95, fill=color, width=w, capstyle="round")
        cv.create_line(cx - s * 0.3, cy + s * 0.62, cx, cy + s * 0.95, fill=color, width=w, capstyle="round")
        cv.create_line(cx + s * 0.3, cy + s * 0.62, cx, cy + s * 0.95, fill=color, width=w, capstyle="round")
    elif name == "folder":
        pts = [cx - s, cy - s * 0.5, cx - s * 0.22, cy - s * 0.5, cx, cy - s * 0.16,
               cx + s, cy - s * 0.16, cx + s, cy + s * 0.6, cx - s, cy + s * 0.6]
        cv.create_line(*pts, cx - s, cy - s * 0.5, fill=color, width=w, joinstyle="round", capstyle="round")
    elif name == "trash":
        cv.create_line(cx - s * 0.75, cy - s * 0.55, cx + s * 0.75, cy - s * 0.55, fill=color, width=w, capstyle="round")
        cv.create_line(cx - s * 0.28, cy - s * 0.55, cx - s * 0.28, cy - s * 0.85, fill=color, width=w)
        cv.create_line(cx - s * 0.28, cy - s * 0.85, cx + s * 0.28, cy - s * 0.85, fill=color, width=w)
        cv.create_line(cx + s * 0.28, cy - s * 0.85, cx + s * 0.28, cy - s * 0.55, fill=color, width=w)
        pts = [cx - s * 0.55, cy - s * 0.55, cx - s * 0.42, cy + s * 0.85,
               cx + s * 0.42, cy + s * 0.85, cx + s * 0.55, cy - s * 0.55]
        cv.create_line(*pts, fill=color, width=w, joinstyle="round")
    elif name == "play":
        cv.create_polygon(cx - s * 0.55, cy - s * 0.8, cx - s * 0.55, cy + s * 0.8, cx + s * 0.85, cy,
                          fill=color, outline=color, width=max(0.5, w * 0.6))
    elif name == "image":
        cv.create_line(cx - s, cy - s * 0.75, cx + s, cy - s * 0.75, cx + s, cy + s * 0.75,
                       cx - s, cy + s * 0.75, cx - s, cy - s * 0.75, fill=color, width=w, joinstyle="round")
        cv.create_oval(cx - s * 0.55, cy - s * 0.42, cx - s * 0.1, cy + s * 0.03, outline=color, width=w)
        cv.create_line(cx - s * 0.9, cy + s * 0.72, cx - s * 0.15, cy - s * 0.1,
                       cx + s * 0.3, cy + s * 0.3, cx + s * 0.6, cy, cx + s * 0.95, cy + s * 0.4,
                       fill=color, width=w, joinstyle="round")
    elif name == "arrow-down":
        cv.create_line(cx, cy - s, cx, cy + s * 0.4, fill=color, width=w, capstyle="round")
        cv.create_line(cx - s * 0.55, cy - s * 0.15, cx, cy + s * 0.4, cx + s * 0.55, cy - s * 0.15,
                       fill=color, width=w, joinstyle="round", capstyle="round")
        cv.create_line(cx - s * 0.85, cy + s * 0.85, cx + s * 0.85, cy + s * 0.85,
                       fill=color, width=w, capstyle="round")
    elif name == "help":
        cv.create_oval(cx - s, cy - s, cx + s, cy + s, outline=color, width=w)
        cv.create_text(cx, cy + s * 0.05, text="?", fill=color, font=(FAM, -int(round(size * 0.82 * SC)), "bold"))
    elif name == "check":
        cv.create_line(cx - s * 0.72, cy + s * 0.05, cx - s * 0.15, cy + s * 0.62,
                       cx + s * 0.78, cy - s * 0.55, fill=color, width=w + 0.4 * SC,
                       joinstyle="round", capstyle="round")
    elif name == "logo":
        cv.create_line(cx - s * 0.62, cy - s * 0.72, cx + s * 0.62, cy - s * 0.72,
                       fill=color, width=w * 1.5, capstyle="round")
        cv.create_line(cx, cy - s * 0.30, cx, cy + s * 0.60, fill=color, width=w * 1.5, capstyle="round")
        cv.create_line(cx - s * 0.5, cy + s * 0.12, cx, cy + s * 0.62, cx + s * 0.5, cy + s * 0.12,
                       fill=color, width=w * 1.5, joinstyle="round", capstyle="round")


# ---------------------------------------------------------------- 控件


class Btn(tk.Canvas):
    """胶囊 / 圆角按钮。kind: primary | secondary | ghost | soft | danger"""

    STYLES = {
        "primary":   dict(fill="#171717", hover="#333333", text="#ffffff", line=""),
        "secondary": dict(fill="#ffffff", hover="#fafafa", text="#171717", line="#ebebeb"),
        "ghost":     dict(fill="", hover="#f5f5f5", text="#4d4d4d", line=""),
        "soft":      dict(fill="#f5f5f5", hover="#ebebeb", text="#171717", line=""),
        "danger":    dict(fill="#ffffff", hover="#f7d4d6", text="#ee0000", line="#f7d4d6"),
    }

    def __init__(self, master, text, command=None, kind="primary", w=120, h=40,
                 radius=None, icon_name=None, font_size=14, parent_bg="#ffffff", pad_x=18):
        super().__init__(master, width=px(w), height=px(h), bg=parent_bg,
                         highlightthickness=0, bd=0, takefocus=0)
        self._text, self._kind, self._cmd = text, kind, command
        self._bw, self._bh = px(w), px(h)   # 注意：不能用 _w，那是 tkinter 的控件路径
        self._radius = px(h / 2) if radius is None else px(radius)
        self._icon = icon_name
        self._fs = font_size
        self._pad_x = pad_x
        self._state = "normal"      # normal | hover | disabled
        self._press = False
        self.configure(cursor="hand2")
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_down)
        self.bind("<ButtonRelease-1>", self._on_up)

    # --- 状态
    def set_enabled(self, on: bool):
        self._state = "normal" if on else "disabled"
        self.configure(cursor="hand2" if on else "arrow")
        self._draw()

    def set_text(self, text: str):
        self._text = text
        self._draw()

    # --- 交互
    def _on_enter(self, _=None):
        if self._state == "disabled":
            return
        self._state = "hover"
        self._draw()

    def _on_leave(self, _=None):
        if self._state == "disabled":
            return
        self._state, self._press = "normal", False
        self._draw()

    def _on_down(self, _=None):
        if self._state == "disabled":
            return
        self._press = True
        self._draw()

    def _on_up(self, _=None):
        if self._state == "disabled":
            return
        was, self._press = self._press, False
        self._draw()
        if was and self._cmd:
            self._cmd()

    # --- 绘制
    def _draw(self):
        self.delete("all")
        w = self.winfo_width() if self.winfo_width() > 1 else self._bw
        h = self.winfo_height() if self.winfo_height() > 1 else self._bh
        st = self.STYLES[self._kind]
        disabled = self._state == "disabled"
        fill = st["fill"]
        fg = st["text"]
        line = st["line"]
        if disabled:
            fill = st["fill"] or self.cget("bg")
            fg = "#c9c9c9"
            line = st["line"] or ""
        elif self._state == "hover":
            fill = st["hover"] or fill
        if self._press and not disabled:
            fill = st["hover"] or fill
        if fill:
            rrect(self, 0, 0, w, h, self._radius, fill=fill,
                  outline=line or "", width=1 if line else 0)
        elif line:
            rrect(self, 0.5, 0.5, w - 0.5, h - 0.5, self._radius, fill="", outline=line, width=1)

        txt_w = tkfont.Font(font=ft(self._fs, "bold" if self._kind == "primary" else "normal")).measure(self._text)
        icon_sz = self._fs * 1.05
        gap = 6 if self._icon else 0
        total = txt_w + (icon_sz + gap if self._icon else 0)
        x0 = (w - total) / 2
        cy = h / 2
        if self._icon:
            icon(self, self._icon, x0 + icon_sz / 2, cy, icon_sz, fg, 1.6)
            x0 += icon_sz + gap
        self.create_text(x0, cy, text=self._text, anchor="w", fill=fg,
                         font=ft(self._fs, "bold" if self._kind == "primary" else "normal"))


class Segmented(tk.Canvas):
    """分段控制器（输出格式选择）。"""

    def __init__(self, master, options, value, command=None, h=34, parent_bg="#ffffff"):
        self._opts = [o[0] for o in options]
        self._labels = options
        self._value = value
        self._cmd = command
        self._h = px(h)
        super().__init__(master, height=self._h, bg=parent_bg, highlightthickness=0, bd=0)
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.configure(cursor="hand2")
        self._hover = -1

    def set_value(self, v, notify=False):
        self._value = v
        self._draw()
        if notify and self._cmd:
            self._cmd(v)

    def get(self):
        return self._value

    def _hit(self, x):
        w = self.winfo_width()
        n = len(self._opts)
        if n == 0 or w <= 1:
            return -1
        seg = w / n
        i = int(x // seg)
        return max(0, min(n - 1, i))

    def _motion(self, e):
        i = self._hit(e.x)
        if i != self._hover:
            self._hover = i
            self._draw()

    def _click(self, e):
        i = self._hit(e.x)
        if 0 <= i < len(self._opts):
            self._value = self._opts[i]
            self._draw()
            if self._cmd:
                self._cmd(self._value)

    def _draw(self):
        self.delete("all")
        w = self.winfo_width() if self.winfo_width() > 1 else 300
        h = self._h
        n = len(self._opts)
        if n == 0:
            return
        rrect(self, 0, 0, w, h, px(8), fill=C["soft2"], outline="")
        seg = w / n
        pad = px(3)
        for i, label in enumerate(self._labels):
            sel = self._opts[i] == self._value
            x1 = i * seg
            if sel:
                rrect(self, x1 + pad, pad, x1 + seg - pad, h - pad, px(6),
                      fill="#ffffff", outline=C["hair"], width=1)
            col = C["ink"] if sel else (C["body"] if self._hover == i else C["mute"])
            self.create_text(x1 + seg / 2, h / 2, text=label[1], fill=col,
                             font=ft(12.5, "bold" if sel else "normal"))


class Check(tk.Canvas):
    """自绘复选框。"""

    def __init__(self, master, text, value=False, command=None, parent_bg="#ffffff",
                 desc=None, w=300, h=26):
        self._value = value
        self._cmd = command
        self._enabled = True
        self._desc = desc
        self._text = text
        hh = h + (17 if desc else 0)
        super().__init__(master, width=px(w), height=px(hh), bg=parent_bg,
                         highlightthickness=0, bd=0, takefocus=0)
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._toggle)
        self.configure(cursor="hand2")

    def get(self):
        return self._value

    def set(self, v):
        self._value = bool(v)
        self._draw()

    def set_text(self, text: str, desc: str | None = None):
        self._text = text
        if desc is not None:
            self._desc = desc
        self._draw()

    def set_enabled(self, on: bool):
        self._enabled = on
        self.configure(cursor="hand2" if on else "arrow")
        self._draw()

    def _toggle(self, _=None):
        if not self._enabled:
            return
        self._value = not self._value
        self._draw()
        if self._cmd:
            self._cmd(self._value)

    def _draw(self):
        self.delete("all")
        box = px(16)
        x, y = px(1), px(3)
        col_box = C["ink"] if self._value else "#ffffff"
        col_line = C["ink"] if self._value else (C["hair_strong"] if self._enabled else "#dcdcdc")
        rrect(self, x + 0.5, y + 0.5, x + box - 0.5, y + box - 0.5, px(4),
              fill=col_box if self._value else "#ffffff", outline=col_line, width=1)
        if self._value:
            icon(self, "check", x + box / 2, y + box / 2, box * 0.78, "#ffffff", 1.9)
        fg = C["ink"] if self._enabled else "#b5b5b5"
        ty = y + box / 2
        self.create_text(x + box + px(9), ty, text=self._text, anchor="w", fill=fg, font=ft(13))
        if self._desc:
            self.create_text(x + box + px(9), ty + px(15), text=self._desc, anchor="w",
                             fill="#a9a9a9" if self._enabled else "#cfcfcf", font=ft(11))


class ChoiceCard(tk.Canvas):
    """单选卡片（保存方式）。"""

    def __init__(self, master, title, desc, value, group_get, command, h=56, parent_bg="#ffffff"):
        self._title, self._desc, self._value = title, desc, value
        self._group_get = group_get
        self._cmd = command
        self._h = px(h)
        super().__init__(master, height=self._h, bg=parent_bg, highlightthickness=0, bd=0, takefocus=0)
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", lambda e: self._cmd(self._value))
        self.configure(cursor="hand2")

    def _draw(self):
        self.delete("all")
        w = self.winfo_width() if self.winfo_width() > 1 else 300
        h = self._h
        sel = self._group_get() == self._value
        rrect(self, 0.5, 0.5, w - 0.5, h - 0.5, px(8),
              fill=C["soft"] if sel else "#ffffff",
              outline=C["ink"] if sel else C["hair"], width=1.4 if sel else 1)
        # 单选圆点
        cx, cy, r = px(18), h / 2, px(7)
        self.create_oval(cx - r, cy - r, cx + r, cy + r,
                         outline=C["ink"] if sel else C["hair_strong"], width=1.2 * SC,
                         fill="#ffffff")
        if sel:
            r2 = px(3.6)
            self.create_oval(cx - r2, cy - r2, cx + r2, cy + r2, fill=C["ink"], outline="")
        self.create_text(cx + r + px(10), cy - px(8), text=self._title, anchor="w",
                         fill=C["ink"], font=ft(13, "bold" if sel else "normal"))
        self.create_text(cx + r + px(10), cy + px(9), text=self._desc, anchor="w",
                         fill=C["mute"], font=ft(11))


class Bar(tk.Canvas):
    """细进度条。"""

    def __init__(self, master, h=6, parent_bg="#ffffff"):
        self._h = px(h)
        super().__init__(master, height=self._h, bg=parent_bg, highlightthickness=0, bd=0)
        self._frac = 0.0
        self._color = C["ink"]
        self.bind("<Configure>", lambda e: self._draw())

    def set(self, frac: float, color: str | None = None):
        self._frac = max(0.0, min(1.0, frac))
        if color:
            self._color = color
        self._draw()

    def _draw(self):
        self.delete("all")
        w = self.winfo_width() if self.winfo_width() > 1 else 200
        h = self._h
        r = h / 2
        rrect(self, 0, 0, w, h, r, fill=C["hair"], outline="")
        if self._frac > 0:
            fw = max(h, w * self._frac)
            rrect(self, 0, 0, fw, h, r, fill=self._color, outline="")


class Card(tk.Canvas):
    """带 1px 细线圆角的卡片容器；内容放在 .body 里。"""

    def __init__(self, master, pad=20, radius=10, fill="#ffffff", border="#ebebeb", parent_bg="#ffffff"):
        super().__init__(master, bg=parent_bg, highlightthickness=0, bd=0)
        self._pad, self._radius, self._fill, self._border = px(pad), px(radius), fill, border
        self.body = tk.Frame(self, bg=fill)
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _=None):
        w, h = self.winfo_width(), self.winfo_height()
        self.delete("card")
        rrect(self, 0, 0, w, h, self._radius, fill=self._fill,
              outline=self._border, width=1, tags="card")
        self.tag_lower("card")
        p = self._pad
        self.body.place(x=p, y=p, width=max(1, w - 2 * p), height=max(1, h - 2 * p))


# ---------------------------------------------------------------- 任务模型


@dataclass
class Task:
    path: Path
    size: int
    out_size: int | None = None
    status: str = "待处理"
    note: str = ""
    iid: str = ""


APP_DIR = Path.home() / ".picpress"
CFG_FILE = APP_DIR / "config.json"

DEFAULTS = {
    "target_mb": 0.5,
    "fmt": "keep",
    "replace": False,
    "backup": True,
    "keep_exif": False,
    "out_dir": str(Path.home() / "Pictures" / "PicPress输出"),
    "quick_mb": 2.0,      # 右键菜单的默认压缩体积
    "menu_on": False,     # 是否已启用资源管理器右键菜单
}


def load_config() -> dict:
    """读取配置并与默认值合并（含类型归一化）。"""
    cfg = dict(DEFAULTS)
    try:
        if CFG_FILE.exists():
            cfg.update(json.loads(CFG_FILE.read_text("utf-8")))
    except Exception:
        pass
    if cfg.get("fmt") not in ("keep", "jpeg", "webp", "png"):
        cfg["fmt"] = "keep"
    try:
        float(cfg.get("target_mb", 0.5))
    except Exception:
        cfg["target_mb"] = 0.5
    # 布尔项做一次归一化，避免历史配置里的字符串被当成真值
    cfg["replace"] = cfg.get("replace") in (True, "replace", "True", "true", 1)
    cfg["backup"] = cfg.get("backup") not in (False, "False", "false", 0)
    cfg["keep_exif"] = cfg.get("keep_exif") in (True, "True", "true", 1)
    cfg["menu_on"] = cfg.get("menu_on") in (True, "True", "true", 1)
    try:
        v = float(cfg.get("quick_mb", 2.0))
        cfg["quick_mb"] = v if v > 0 else 2.0
    except Exception:
        cfg["quick_mb"] = 2.0
    return cfg


def save_config(cfg: dict) -> None:
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        CFG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
    except Exception:
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        global SC
        SC = self._init_dpi()

        self.title(APP_TITLE)
        self.configure(bg=C["canvas"])
        self.minsize(px(1020), px(720))
        self.geometry(f"{px(1180)}x{px(800)}")
        self._center()
        self._set_icon()
        self._force_light_titlebar()

        self.cfg = self._load_cfg()
        self.tasks: list[Task] = []
        self.evq: queue.Queue = queue.Queue()
        self.cancel_flag = threading.Event()
        self.running = False
        self.worker_thread: threading.Thread | None = None
        self.last_out_dir = Path(self.cfg["out_dir"])
        self.last_backup_dir: Path | None = None
        self._drops: list[list[str]] = []

        self._build_style()
        self._build_ui()
        self._bind_keys()
        self._enable_dnd()
        self._autofit()
        self._sync_shell_menu()
        self.after(60, self._poll)

    def _sync_shell_menu(self):
        """启动时让注册表与实际设置保持一致（换过 exe 路径也能自动修正）。"""
        try:
            if self.cfg.get("menu_on"):
                shellmenu.sync(float(self.cfg.get("quick_mb", 2.0)))
            elif shellmenu.is_installed():
                shellmenu.uninstall()
        except Exception:
            pass

    def _open_settings(self):
        w = getattr(self, "_settings", None)
        if w is not None and w.winfo_exists():
            w.lift()
            w.focus_force()
            return
        self._settings = SettingsWindow(self)

    def _autofit(self):
        """按右栏内容所需高度自动调整窗口，避免设置项被裁切。"""
        try:
            self.update_idletasks()
            need = (self._right_body.winfo_reqheight() + px(44)      # 卡片上下内边距
                    + px(62) + px(33) + px(18) + px(14))             # 导航 + 状态栏 + 外边距
            cap = self.winfo_screenheight() - px(80)
            target = int(min(max(self.winfo_height(), need), cap))
            if abs(target - self.winfo_height()) > 2:
                self.geometry(f"{self.winfo_width()}x{target}")
                self._center()
        except Exception:
            pass

    # ------------------------------------------------------------ 系统集成

    def _init_dpi(self) -> float:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
        try:
            self.update_idletasks()
            dpi = self.winfo_fpixels("1i")
            return max(1.0, min(2.5, round(dpi / 96.0, 3)))
        except Exception:
            return 1.0

    def _center(self):
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - px(20))}")

    def _set_icon(self):
        p = self._asset("assets/icon.ico")
        if p and os.path.exists(p):
            try:
                self.iconbitmap(p)
            except Exception:
                pass

    @staticmethod
    def _asset(rel: str) -> str | None:
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return os.path.join(base, rel)
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), rel)

    def _force_light_titlebar(self):
        try:
            hwnd = ctypes.windll.user32.GetAncestor(self.winfo_id(), 2)
            v = ctypes.c_int(0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(v), ctypes.sizeof(v))
        except Exception:
            pass

    # ------------------------------------------------------------ 配置

    def _load_cfg(self) -> dict:
        return load_config()

    def _save_cfg(self):
        self.cfg["target_mb"] = self._target_mb()
        self.cfg["fmt"] = self.seg.get()
        self.cfg["replace"] = (self.mode == "replace")
        self.cfg["backup"] = self.chk_backup.get()
        self.cfg["keep_exif"] = self.chk_exif.get()
        self.cfg["out_dir"] = str(self.out_dir)
        save_config(self.cfg)


    def _save_cfg(self):
        try:
            self.cfg["target_mb"] = self._target_mb()
            self.cfg["fmt"] = self.seg.get()
            self.cfg["replace"] = (self.mode == "replace")
            self.cfg["backup"] = self.chk_backup.get()
            self.cfg["keep_exif"] = self.chk_exif.get()
            self.cfg["out_dir"] = str(self.out_dir)
            APP_DIR.mkdir(parents=True, exist_ok=True)
            CFG_FILE.write_text(json.dumps(self.cfg, ensure_ascii=False, indent=2), "utf-8")
        except Exception:
            pass

    # ------------------------------------------------------------ 样式

    def _build_style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("PP.Treeview",
                     background=C["canvas"], fieldbackground=C["canvas"],
                     foreground=C["ink"], borderwidth=0, relief="flat",
                     rowheight=px(34), font=ft(13))
        st.layout("PP.Treeview", [("PP.Treeview.treearea", {"sticky": "nswe"})])
        st.configure("PP.Treeview.Heading",
                     background=C["soft"], foreground=C["mute"], borderwidth=0,
                     relief="flat", font=ft(11.5, "bold"))
        st.map("PP.Treeview.Heading", background=[("active", C["soft2"])])
        st.map("PP.Treeview",
               background=[("selected", C["soft2"])],
               foreground=[("selected", C["ink"])])
        st.configure("PP.Vertical.TScrollbar", background=C["hair"], troughcolor=C["canvas"],
                     borderwidth=0, arrowsize=px(11), width=px(8), relief="flat")
        st.map("PP.Vertical.TScrollbar", background=[("active", C["hair_strong"])])

    # ------------------------------------------------------------ 界面

    def _build_ui(self):
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        self._build_nav()

        body = tk.Frame(self, bg=C["canvas"])
        body.grid(row=1, column=0, sticky="nsew", padx=px(24), pady=(px(18), px(14)))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, minsize=px(372))

        self._build_left(body)
        self._build_right(body)
        self._build_statusbar()

    def _build_nav(self):
        nav = tk.Frame(self, bg=C["canvas"], height=px(62))
        nav.grid(row=0, column=0, sticky="ew")
        nav.grid_propagate(False)
        nav.columnconfigure(2, weight=1)

        mark = tk.Canvas(nav, width=px(26), height=px(26), bg=C["canvas"],
                         highlightthickness=0, bd=0)
        mark.grid(row=0, column=0, padx=(px(24), px(10)), pady=px(18))
        rrect(mark, 0, 0, px(26), px(26), px(7), fill=C["ink"], outline="")
        icon(mark, "logo", px(13), px(13), px(15), "#ffffff", 1.5)

        t = tk.Frame(nav, bg=C["canvas"])
        t.grid(row=0, column=1, sticky="w")
        tk.Label(t, text=APP_NAME, bg=C["canvas"], fg=C["ink"],
                 font=ft(17, "bold")).pack(side="left")
        tk.Label(t, text="图片批量压缩", bg=C["canvas"], fg=C["mute"],
                 font=ft(12.5)).pack(side="left", padx=(px(10), 0))

        right = tk.Frame(nav, bg=C["canvas"])
        right.grid(row=0, column=3, sticky="e", padx=(0, px(20)))
        Btn(right, "设置", self._open_settings, kind="ghost", w=60, h=32,
            font_size=12.5).pack(side="left")
        Btn(right, "使用说明", self._show_help, kind="ghost", w=96, h=32, font_size=12.5).pack(side="left")
        tk.Label(right, text=f"v{VERSION}", bg=C["canvas"], fg=C["mute"],
                 font=ft(11.5, mono=True)).pack(side="left", padx=(px(10), 0))

        tk.Frame(nav, bg=C["hair"], height=1).place(x=0, rely=1, y=-1, relwidth=1)

    def _build_left(self, parent):
        left = tk.Frame(parent, bg=C["canvas"])
        left.grid(row=0, column=0, sticky="nsew", padx=(0, px(20)))
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        # 标题行
        head = tk.Frame(left, bg=C["canvas"])
        head.grid(row=0, column=0, sticky="ew", pady=(0, px(12)))
        head.columnconfigure(1, weight=1)
        tk.Label(head, text="文件列表", bg=C["canvas"], fg=C["ink"],
                 font=ft(15, "bold")).grid(row=0, column=0, sticky="w")
        self.lbl_count = tk.Label(head, text="0 个文件", bg=C["canvas"], fg=C["mute"],
                                  font=ft(12))
        self.lbl_count.grid(row=0, column=1, sticky="w", padx=(px(10), 0))

        btns = tk.Frame(head, bg=C["canvas"])
        btns.grid(row=0, column=2, sticky="e")
        Btn(btns, "添加文件", self._add_files, kind="secondary", w=104, h=34,
            icon_name="plus", font_size=12.5).pack(side="left")
        Btn(btns, "添加文件夹", self._add_folder, kind="secondary", w=112, h=34,
            icon_name="folder-add", font_size=12.5).pack(side="left", padx=(px(8), 0))
        Btn(btns, "清空", self._clear, kind="ghost", w=64, h=34, font_size=12.5).pack(side="left", padx=(px(4), 0))

        # 列表卡片
        wrap = tk.Frame(left, bg=C["canvas"], highlightthickness=1,
                        highlightbackground=C["hair"], highlightcolor=C["hair"])
        wrap.grid(row=1, column=0, sticky="nsew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)

        cols = ("name", "src", "dst", "save", "status")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", style="PP.Treeview",
                                 selectmode="extended")
        self.tree.heading("name", text="文件名", anchor="w")
        self.tree.heading("src", text="原始", anchor="e")
        self.tree.heading("dst", text="压缩后", anchor="e")
        self.tree.heading("save", text="节省", anchor="e")
        self.tree.heading("status", text="状态", anchor="w")
        self.tree.column("name", width=px(330), minwidth=px(160), stretch=True, anchor="w")
        self.tree.column("src", width=px(92), minwidth=px(80), stretch=False, anchor="e")
        self.tree.column("dst", width=px(92), minwidth=px(80), stretch=False, anchor="e")
        self.tree.column("save", width=px(64), minwidth=px(54), stretch=False, anchor="e")
        self.tree.column("status", width=px(150), minwidth=px(120), stretch=False, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(px(1), 0), pady=px(1))

        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview,
                           style="PP.Vertical.TScrollbar")
        sb.grid(row=0, column=1, sticky="ns", pady=px(1), padx=(0, px(1)))
        self.tree.configure(yscrollcommand=sb.set)

        self.tree.tag_configure("done", foreground=C["link"])
        self.tree.tag_configure("warn", foreground=C["warn"])
        self.tree.tag_configure("err", foreground=C["err"])
        self.tree.tag_configure("mute", foreground=C["mute"])
        self.tree.bind("<Double-1>", self._reveal_selected)
        self.tree.bind("<Button-3>", self._ctx_menu)
        self.tree.bind("<Delete>", lambda e: self._remove_selected())

        self._build_empty_state(wrap)

        # 底部动作区
        foot = tk.Frame(left, bg=C["canvas"])
        foot.grid(row=2, column=0, sticky="ew", pady=(px(14), 0))
        foot.columnconfigure(0, weight=1)

        self.lbl_summary = tk.Label(foot, text="等待添加图片", bg=C["canvas"], fg=C["body"],
                                    font=ft(12.5), anchor="w")
        self.lbl_summary.grid(row=0, column=0, sticky="w")
        self.lbl_target_hint = tk.Label(foot, text="", bg=C["canvas"], fg=C["mute"],
                                        font=ft(12), anchor="e")
        self.lbl_target_hint.grid(row=0, column=1, sticky="e")

        self.bar = Bar(foot, h=6, parent_bg=C["canvas"])
        self.bar.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(px(10), px(12)))

        self.btn_start = Btn(foot, "开始压缩", self._start, kind="primary", w=150, h=44,
                             icon_name="arrow-down", font_size=14)
        self.btn_start.grid(row=2, column=0, sticky="w")
        self.btn_open = Btn(foot, "打开输出文件夹", self._open_out, kind="secondary", w=150, h=44,
                            icon_name="folder", font_size=13)
        self.btn_open.grid(row=2, column=1, sticky="e", padx=(0, px(8)))
        self.btn_cancel = Btn(foot, "取消", self._cancel, kind="ghost", w=76, h=44, font_size=13)
        self.btn_cancel.grid(row=2, column=2, sticky="e")
        self.btn_cancel.grid_remove()

    def _build_empty_state(self, wrap):
        self.empty = tk.Canvas(wrap, bg=C["canvas"], highlightthickness=0, bd=0)
        self.empty.grid(row=0, column=0, columnspan=2, sticky="nsew")
        self.empty.bind("<Configure>", lambda e: self._draw_empty())

    def _draw_empty(self):
        cv = self.empty
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        if w <= 2 or h <= 2:
            return
        cx, cy = w / 2, h / 2
        rrect(cv, cx - px(34), cy - px(58), cx + px(34), cy + px(10), px(14),
              fill=C["soft"], outline=C["hair"], width=1)
        icon(cv, "image", cx, cy - px(24), px(30), C["hair_strong"], 1.6)
        cv.create_text(cx, cy + px(38), text="把图片或文件夹拖到这里", fill=C["ink"], font=ft(14, "bold"))
        cv.create_text(cx, cy + px(60), text="也可以点击右上角「添加文件」· 支持 JPG / PNG / WebP / BMP / TIFF / GIF",
                       fill=C["mute"], font=ft(12))

    def _build_right(self, parent):
        card = Card(parent, pad=px(22), radius=px(12))
        card.grid(row=0, column=1, sticky="nsew")
        b = card.body
        self._right_body = b
        b.columnconfigure(0, weight=1)

        r = 0
        tk.Label(b, text="压缩目标", bg=C["canvas"], fg=C["mute"], font=ft(11.5, "bold", mono=True),
                 anchor="w").grid(row=r, column=0, sticky="ew")
        r += 1

        # 输入框（带 MB 后缀）
        box = tk.Frame(b, bg=C["canvas"], highlightthickness=1,
                       highlightbackground=C["hair"], highlightcolor=C["hair"])
        box.grid(row=r, column=0, sticky="ew", pady=(px(10), 0))
        box.columnconfigure(0, weight=1)
        vcmd = (self.register(self._validate_num), "%P")
        self.ent = tk.Entry(box, bg=C["canvas"], fg=C["ink"], bd=0, relief="flat",
                            font=ft(19, "bold"), insertbackground=C["ink"],
                            validate="key", validatecommand=vcmd, justify="left")
        self.ent.insert(0, self._fmt_num(self.cfg["target_mb"]))
        self.ent.grid(row=0, column=0, sticky="ew", padx=(px(12), 0), pady=px(9))
        self.ent.bind("<KeyRelease>", self._on_target_change)
        tk.Label(box, text="MB", bg=C["canvas"], fg=C["mute"], font=ft(13)).grid(
            row=0, column=1, padx=(0, px(12)))
        self.ent.bind("<FocusIn>", lambda e: box.configure(highlightbackground=C["ink"]))
        self.ent.bind("<FocusOut>", lambda e: box.configure(highlightbackground=C["hair"]))
        r += 1

        self.lbl_target_sub = tk.Label(b, text="每个文件压到不超过该体积 · 1 MB = 1024 KB",
                                       bg=C["canvas"], fg=C["mute"], font=ft(11.5), anchor="w")
        self.lbl_target_sub.grid(row=r, column=0, sticky="ew", pady=(px(7), 0))
        r += 1

        chips = tk.Frame(b, bg=C["canvas"])
        chips.grid(row=r, column=0, sticky="ew", pady=(px(11), 0))
        self.chips = []
        for label, mb in (("0.2 MB", 0.2), ("0.5 MB", 0.5), ("1 MB", 1), ("2 MB", 2), ("5 MB", 5)):
            c = Btn(chips, label, lambda m=mb: self._set_target(m), kind="soft", w=0, h=28,
                    radius=6, font_size=11.5)
            c.pack(side="left", padx=(0, px(6)))
            need = tkfont.Font(font=ft(11.5)).measure(label) + px(22)
            c.configure(width=need)
            self.chips.append((c, mb))
        r += 1

        self._divider(b, r)
        r += 1

        tk.Label(b, text="输出格式", bg=C["canvas"], fg=C["mute"], font=ft(11.5, "bold", mono=True),
                 anchor="w").grid(row=r, column=0, sticky="ew")
        r += 1
        self.seg = Segmented(b, [("keep", "保持原格式"), ("jpeg", "JPEG"), ("webp", "WebP"), ("png", "PNG")],
                             self.cfg["fmt"], self._on_fmt_change, h=34, parent_bg=C["canvas"])
        self.seg.grid(row=r, column=0, sticky="ew", pady=(px(10), 0))
        r += 1
        self.lbl_fmt_hint = tk.Label(b, text="", bg=C["canvas"], fg=C["mute"], font=ft(11.5), anchor="w",
                                     justify="left", wraplength=px(300))
        self.lbl_fmt_hint.grid(row=r, column=0, sticky="ew", pady=(px(7), 0))
        r += 1

        self._divider(b, r)
        r += 1

        tk.Label(b, text="保存方式", bg=C["canvas"], fg=C["mute"], font=ft(11.5, "bold", mono=True),
                 anchor="w").grid(row=r, column=0, sticky="ew")
        r += 1
        self.mode = "replace" if self.cfg["replace"] else "folder"
        self.card_folder = ChoiceCard(b, "输出到文件夹", "原图保留不动，压缩件另存",
                                      "folder", lambda: self.mode, self._set_mode)
        self.card_folder.grid(row=r, column=0, sticky="ew", pady=(px(10), 0))
        r += 1
        self.card_replace = ChoiceCard(b, "替换原图", "压缩成功后直接覆盖，原图可备份",
                                       "replace", lambda: self.mode, self._set_mode)
        self.card_replace.grid(row=r, column=0, sticky="ew", pady=(px(8), 0))
        r += 1

        self.chk_backup = Check(b, "压缩前备份原图", self.cfg["backup"], parent_bg=C["canvas"])
        self.chk_backup.grid(row=r, column=0, sticky="ew", pady=(px(10), 0))
        r += 1
        self.chk_exif = Check(b, "保留 EXIF（拍摄信息 / GPS）", self.cfg["keep_exif"],
                              parent_bg=C["canvas"], desc="关闭可进一步减小体积，并去掉位置隐私")
        self.chk_exif.grid(row=r, column=0, sticky="ew", pady=(px(2), 0))
        r += 1

        self._divider(b, r)
        r += 1

        path_row = tk.Frame(b, bg=C["canvas"])
        path_row.grid(row=r, column=0, sticky="ew")
        path_row.columnconfigure(0, weight=1)
        tk.Label(path_row, text="输出文件夹", bg=C["canvas"], fg=C["body"], font=ft(12.5),
                 anchor="w").grid(row=0, column=0, sticky="w")
        self.btn_pick = Btn(path_row, "选择", self._pick_dir, kind="secondary", w=62, h=28,
                            radius=6, font_size=12)
        self.btn_pick.grid(row=0, column=1, sticky="e")
        r += 1
        self.lbl_outdir = tk.Label(b, text="", bg=C["canvas"], fg=C["mute"], font=ft(11.5),
                                   anchor="w", justify="left", wraplength=px(310))
        self.lbl_outdir.grid(row=r, column=0, sticky="ew", pady=(px(4), 0))
        r += 1

        b.rowconfigure(r, weight=1)  # 把剩余空间吃掉，内容顶到上方

        self.out_dir = Path(self.cfg["out_dir"])
        self._refresh_outdir_label()
        self._apply_mode_ui()
        self._on_fmt_change(self.seg.get())
        self._on_target_change()   # 初始化「= xxx KB」提示与预设高亮

    def _divider(self, parent, row):
        f = tk.Frame(parent, bg=C["hair"], height=1)
        f.grid(row=row, column=0, sticky="ew", pady=px(13))

    def _build_statusbar(self):
        tk.Frame(self, bg=C["hair"], height=1).grid(row=2, column=0, sticky="ew")
        bar = tk.Frame(self, bg=C["soft"], height=px(32))
        bar.grid(row=3, column=0, sticky="ew")
        bar.grid_propagate(False)
        bar.columnconfigure(0, weight=1)
        self.lbl_status = tk.Label(bar, text="就绪", bg=C["soft"], fg=C["mute"], font=ft(11.5), anchor="w")
        self.lbl_status.grid(row=0, column=0, sticky="w", padx=px(24))
        self.lbl_right = tk.Label(bar, text="拖入图片即可开始", bg=C["soft"], fg=C["mute"],
                                  font=ft(11.5), anchor="e")
        self.lbl_right.grid(row=0, column=1, sticky="e", padx=px(24))

    # ------------------------------------------------------------ 输入逻辑

    @staticmethod
    def _fmt_num(v) -> str:
        try:
            f = float(v)
        except Exception:
            return "0.5"
        return f"{f:g}"

    @staticmethod
    def _validate_num(p: str) -> bool:
        if p == "":
            return True
        if p.count(".") > 1:
            return False
        return all(ch.isdigit() or ch == "." for ch in p)

    def _target_mb(self) -> float:
        try:
            v = float(self.ent.get().strip() or "0")
        except Exception:
            v = 0.0
        return max(0.001, v)

    def _set_target(self, mb: float):
        self.ent.delete(0, "end")
        self.ent.insert(0, f"{mb:g}")
        self._on_target_change()

    def _on_target_change(self, _=None):
        mb = self._target_mb()
        kb = mb * 1024
        sub = f"= {kb:.0f} KB · 每个文件压到不超过该体积"
        self.lbl_target_sub.configure(text=sub)
        for c, cmb in self.chips:
            active = abs(cmb - mb) < 1e-6
            if active:
                c._kind = "secondary"
            else:
                c._kind = "soft"
            c._draw()
        self._update_summary()

    def _on_fmt_change(self, v):
        hints = {
            "keep": "遵循原格式；PNG 走无损优化，必要时降色或缩尺寸。",
            "jpeg": "体积最小、兼容性最好，但不支持透明（透明区会变白底）。",
            "webp": "同画质下比 JPEG 小约 25%，现代浏览器与系统都支持。",
            "png": "无损容器，靠降色与缩尺寸达标，适合图标 / 截图。",
        }
        self.lbl_fmt_hint.configure(text=hints.get(v, ""))
        self._update_summary()

    def _set_mode(self, m):
        if self.running:
            return
        self.mode = m
        self._apply_mode_ui()

    def _apply_mode_ui(self):
        self.card_folder._draw()
        self.card_replace._draw()
        is_replace = self.mode == "replace"
        self.chk_backup.set_enabled(is_replace)
        self.btn_pick.set_enabled(not is_replace)
        self.lbl_outdir.configure(fg="#c9c9c9" if is_replace else C["mute"])
        self.btn_open.set_text("打开备份文件夹" if is_replace else "打开输出文件夹")
        self._update_summary()
        self._save_cfg()

    def _refresh_outdir_label(self):
        p = str(self.out_dir)
        if len(p) > 46:
            p = p[:18] + " … " + p[-24:]
        self.lbl_outdir.configure(text=p)

    def _pick_dir(self):
        d = filedialog.askdirectory(title="选择输出文件夹", initialdir=str(self.out_dir))
        if d:
            self.out_dir = Path(d)
            self._refresh_outdir_label()
            self._save_cfg()

    # ------------------------------------------------------------ 文件管理

    def _add_files(self):
        if self.running:
            return
        types = [("图片文件", " ".join(f"*{e}" for e in sorted(SUPPORTED_EXTS))), ("所有文件", "*.*")]
        paths = filedialog.askopenfilenames(title="选择图片", filetypes=types)
        if paths:
            self._ingest(list(paths))

    def _add_folder(self):
        if self.running:
            return
        d = filedialog.askdirectory(title="选择文件夹（含子文件夹）")
        if d:
            self._ingest([d])

    def _ingest(self, paths):
        files = collect_images(paths)
        if not files:
            self._status("没有找到受支持的图片文件", C["warn"])
            return
        have = {str(t.path.resolve()).lower() for t in self.tasks}
        added = 0
        for f in files:
            key = str(f.resolve()).lower()
            if key in have:
                continue
            have.add(key)
            try:
                size = f.stat().st_size
            except OSError:
                continue
            t = Task(path=f, size=size)
            t.iid = self.tree.insert("", "end", values=(
                f.name, human_size(size), "-", "-", "待处理"), tags=("mute",))
            self.tasks.append(t)
            added += 1
        self._refresh_empty()
        self._update_summary()
        self._status(f"已添加 {added} 个文件" + (f"，跳过 {len(files) - added} 个重复项" if added < len(files) else ""))

    def _refresh_empty(self):
        if self.tasks:
            self.empty.grid_remove()
        else:
            self.empty.grid()

    def _remove_selected(self):
        if self.running:
            return
        sel = set(self.tree.selection())
        if not sel:
            return
        self.tasks = [t for t in self.tasks if t.iid not in sel]
        for iid in sel:
            self.tree.delete(iid)
        self._refresh_empty()
        self._update_summary()

    def _clear(self):
        if self.running:
            return
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self.tasks.clear()
        self.bar.set(0)
        self._refresh_empty()
        self._update_summary()
        self._status("列表已清空")

    def _reveal_selected(self, _=None):
        sel = self.tree.selection()
        if not sel:
            return
        for t in self.tasks:
            if t.iid == sel[0]:
                target = t.path if t.path.exists() else (t.path.parent)
                try:
                    subprocess.Popen(["explorer", "/select,", str(target)])
                except Exception:
                    pass
                break

    def _ctx_menu(self, e):
        row = self.tree.identify_row(e.y)
        if row and row not in self.tree.selection():
            self.tree.selection_set(row)
        m = tk.Menu(self, tearoff=0, bg=C["canvas"], fg=C["ink"], bd=0,
                    activebackground=C["soft2"], activeforeground=C["ink"], font=ft(12))
        m.add_command(label="在资源管理器中显示", command=self._reveal_selected)
        m.add_command(label="从列表移除", command=self._remove_selected)
        m.add_separator()
        m.add_command(label="清空列表", command=self._clear)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    # ------------------------------------------------------------ 汇总

    def _update_summary(self):
        n = len(self.tasks)
        total = sum(t.size for t in self.tasks)
        done = [t for t in self.tasks if t.out_size]
        out_total = sum(t.out_size for t in done)
        self.lbl_count.configure(text=f"{n} 个文件")

        if n == 0:
            self.lbl_summary.configure(text="等待添加图片")
            self.lbl_target_hint.configure(text="")
            self.btn_start.set_enabled(False)
            return

        self.btn_start.set_enabled(not self.running)
        mb = self._target_mb()
        if done:
            saved = total - out_total
            pct = (saved / total * 100) if total else 0
            self.lbl_summary.configure(
                text=f"共 {n} 个 · {human_size(total)} → {human_size(out_total)} · 节省 {pct:.0f}%")
        else:
            self.lbl_summary.configure(text=f"共 {n} 个文件 · {human_size(total)}")
        est = mb * n
        self.lbl_target_hint.configure(text=f"目标 ≤ {mb:g} MB × {n} = {est:.2f} MB")

    def _status(self, text, color=None):
        self.lbl_status.configure(text=text, fg=color or C["mute"])

    # ------------------------------------------------------------ 执行

    def _build_options(self) -> Options:
        return Options(
            target_bytes=mb_to_bytes(self._target_mb()),
            fmt=self.seg.get(),
            replace=self.mode == "replace",
            out_dir=str(self.out_dir),
            backup=self.chk_backup.get(),
            keep_exif=self.chk_exif.get(),
        )

    def _start(self):
        if self.running or not self.tasks:
            return
        mb = self._target_mb()
        if mb <= 0:
            self._status("请先填写有效的目标体积", C["err"])
            return
        if self.mode == "replace":
            backup_home = Path.home() / "PicPress备份"
            if self.chk_backup.get():
                tip = f"原图会先备份到：\n{backup_home}"
            else:
                tip = "注意：未开启备份，覆盖后原图无法恢复。"
            ok = messagebox.askokcancel(
                "确认替换原图",
                f"将把 {len(self.tasks)} 个文件压缩后直接覆盖原图。\n\n{tip}\n\n确定继续吗？")
            if not ok:
                return

        opts = self._build_options()
        self._save_cfg()

        for t in self.tasks:
            t.out_size = None
            t.status = "待处理"
            t.note = ""
            self.tree.item(t.iid, values=(t.path.name, human_size(t.size), "-", "-", "待处理"),
                           tags=("mute",))

        self.running = True
        self.cancel_flag.clear()
        self.btn_start.set_enabled(False)
        self.btn_start.set_text("压缩中…")
        self.btn_cancel.grid()
        self.bar.set(0)
        self._total = len(self.tasks)
        self._done = 0
        self._status("正在压缩…")

        self.worker_thread = threading.Thread(target=self._worker, args=(opts,), daemon=True)
        self.worker_thread.start()

    def _worker(self, opts: Options):
        files = [t.path for t in self.tasks]
        backup_root = None
        if opts.replace and opts.backup:
            try:
                backup_root = make_backup_root()
                self.evq.put(("backup_dir", backup_root))
            except Exception:
                backup_root = None
        workers = max(2, min(4, (os.cpu_count() or 4) // 2))
        pending = deque(files)
        running = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            while pending or running:
                while pending and len(running) < workers and not self.cancel_flag.is_set():
                    f = pending.popleft()
                    self.evq.put(("begin", f))
                    running[ex.submit(compress_one, f, opts, backup_root)] = f
                if not running:
                    break
                done, _ = wait(running, return_when=FIRST_COMPLETED, timeout=0.2)
                for fut in done:
                    f = running.pop(fut)
                    try:
                        res = fut.result()
                    except Exception as e:
                        res = None
                        self.evq.put(("error", (f, f"{type(e).__name__}: {e}")))
                    if res is not None:
                        self.evq.put(("result", res))
        self.evq.put(("finished", None))

    def _cancel(self):
        self.cancel_flag.set()
        self._status("正在停止…", C["warn"])

    def _poll(self):
        if self._drops:
            batch = self._drops.pop(0)
            self._on_drop(batch)
        try:
            while True:
                kind, payload = self.evq.get_nowait()
                if kind == "begin":
                    self._on_begin(payload)
                elif kind == "result":
                    self._on_result(payload)
                elif kind == "error":
                    self._on_error(*payload)
                elif kind == "backup_dir":
                    self.last_backup_dir = payload
                elif kind == "finished":
                    self._on_finished()
        except queue.Empty:
            pass
        self.after(70, self._poll)

    def _task_by_path(self, p: Path) -> Task | None:
        for t in self.tasks:
            if t.path == p:
                return t
        return None

    def _on_begin(self, p: Path):
        t = self._task_by_path(p)
        if not t:
            return
        t.status = "压缩中"
        self.tree.item(t.iid, values=(t.path.name, human_size(t.size), "-", "-", "压缩中"),
                       tags=("warn",))

    def _on_result(self, r):
        t = self._task_by_path(r.src)
        self._done += 1
        if not t:
            return
        if r.ok:
            t.out_size = r.out_size
            saved = t.size - r.out_size
            if r.skipped:
                t.status = "跳过"
                tag, status = "mute", "跳过 · 已达标"
                pct = "-"
            elif r.reached:
                t.status = "完成"
                tag, status = "done", "完成"
                pct = f"{saved / t.size * 100:.0f}%" if t.size else "-"
            else:
                t.status = "未达标"
                tag, status = "warn", "未达标"
                pct = f"仅至 {human_size(r.out_size)}"
            self.tree.item(t.iid, values=(t.path.name, human_size(t.size),
                                          human_size(r.out_size), pct, status), tags=(tag,))
            t.note = r.note
        else:
            t.status = "失败"
            self.tree.item(t.iid, values=(t.path.name, human_size(t.size), "-", "-",
                                          f"失败：{(r.error or '')[:34]}"), tags=("err",))
        self.bar.set(self._done / max(1, self._total))
        self._update_summary()

    def _on_error(self, p: Path, msg: str):
        t = self._task_by_path(p)
        self._done += 1
        if t:
            t.status = "失败"
            self.tree.item(t.iid, values=(t.path.name, human_size(t.size), "-", "-",
                                          f"失败：{msg[:34]}"), tags=("err",))
        self.bar.set(self._done / max(1, self._total))

    def _on_finished(self):
        self.running = False
        self.btn_start.set_enabled(True)
        self.btn_start.set_text("开始压缩")
        self.btn_cancel.grid_remove()
        self._update_summary()

        ok = sum(1 for t in self.tasks if t.status == "完成")
        skip = sum(1 for t in self.tasks if t.status == "跳过")
        bad = sum(1 for t in self.tasks if t.status in ("失败", "未达标"))
        total_in = sum(t.size for t in self.tasks)
        total_out = sum(t.out_size or t.size for t in self.tasks)
        saved = total_in - total_out

        if self.cancel_flag.is_set():
            self.bar.set(0)
            self._status(f"已取消 · 成功 {ok} / 跳过 {skip} / 失败 {bad}", C["warn"])
        elif bad:
            self._status(f"完成 {ok} · 跳过 {skip} · 失败 {bad} · 共节省 {human_size(saved)}", C["warn"])
        else:
            self._status(f"全部完成 · {ok} 个已压缩，{skip} 个跳过 · 节省 {human_size(saved)}", C["link"])
        self.lbl_right.configure(text=f"共节省 {human_size(saved)}")

    # ------------------------------------------------------------ 其它

    def _open_out(self):
        target = self.last_backup_dir if (self.mode == "replace" and self.last_backup_dir) else self.out_dir
        try:
            target.mkdir(parents=True, exist_ok=True)
            os.startfile(str(target))
        except Exception as e:
            self._status(f"无法打开：{e}", C["err"])

    def _show_help(self):
        messagebox.showinfo(
            "使用说明",
            "批量压缩：\n"
            "1. 拖入图片 / 文件夹，或点击「添加文件」。\n"
            "2. 在右侧填写目标体积（MB），或点预设值。\n"
            "3. 选择输出格式与保存方式。\n"
            "4. 点「开始压缩」，列表里会实时显示每个文件的结果。\n\n"
            "右键快速压缩：\n"
            "· 打开「设置」勾选右键菜单后，在资源管理器里右键任意图片，\n"
            "  选择「压缩到 2MB (PicPress)」，即可在原文件夹生成副本。\n"
            "· 副本命名为「原文件名_2MB.jpg」，原图不会被改动。\n"
            "· 支持一次选中多张图片。\n\n"
            "小提示：\n"
            "· 双击某一行可在资源管理器中定位该文件。\n"
            "· 原图本身就小于目标时会自动跳过（不损失画质）。\n"
            "· 替换原图模式下默认先备份到「用户目录 \\ PicPress备份」。",
        )

    def _bind_keys(self):
        self.bind("<Control-o>", lambda e: self._add_files())
        self.bind("<Control-O>", lambda e: self._add_files())
        self.bind("<Delete>", lambda e: self._remove_selected())
        self.bind("<Control-Return>", lambda e: self._start())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        if self.running:
            if not messagebox.askokcancel("正在压缩", "还有文件在处理中，确定要退出吗？"):
                return
            self.cancel_flag.set()
        self._save_cfg()
        self.destroy()

    # ------------------------------------------------------------ 拖拽

    def _enable_dnd(self):
        """用 Win32 WM_DROPFILES 实现拖拽入窗（不引入第三方库）。"""
        try:
            user32 = ctypes.windll.user32
            shell32 = ctypes.windll.shell32

            # 必须显式声明签名，否则 64 位下句柄会被按 32 位整数截断，直接崩进程
            shell32.DragAcceptFiles.argtypes = [ctypes.c_void_p, ctypes.c_bool]
            shell32.DragAcceptFiles.restype = None
            shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                               ctypes.c_wchar_p, ctypes.c_uint]
            shell32.DragQueryFileW.restype = ctypes.c_uint
            shell32.DragFinish.argtypes = [ctypes.c_void_p]
            shell32.DragFinish.restype = None

            LRESULT = ctypes.c_ssize_t
            WNDPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_void_p, ctypes.c_uint,
                                         ctypes.c_size_t, ctypes.c_ssize_t)
            user32.SetWindowLongPtrW.restype = ctypes.c_void_p
            user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
            user32.CallWindowProcW.restype = LRESULT
            user32.CallWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                               ctypes.c_size_t, ctypes.c_ssize_t]

            hwnd = user32.GetAncestor(self.winfo_id(), 2)  # GA_ROOT
            shell32.DragAcceptFiles(ctypes.c_void_p(hwnd), True)
            self._hwnd = hwnd

            def _proc(h, msg, wp, lp):
                if msg == 0x0233:  # WM_DROPFILES
                    try:
                        hd = ctypes.c_void_p(wp)
                        n = shell32.DragQueryFileW(hd, 0xFFFFFFFF, None, 0)
                        got = []
                        for i in range(n):
                            ln = shell32.DragQueryFileW(hd, i, None, 0) + 1
                            buf = ctypes.create_unicode_buffer(ln)
                            shell32.DragQueryFileW(hd, i, buf, ln)
                            got.append(buf.value)
                        shell32.DragFinish(hd)
                        if got:
                            # 只入队，决不能在这里回调 Tcl（在窗口过程里重入 Tk 会崩）
                            self._drops.append(got)
                    except Exception:
                        pass
                    return 0
                return user32.CallWindowProcW(self._old_proc, ctypes.c_void_p(h), msg,
                                              ctypes.c_size_t(wp), ctypes.c_ssize_t(lp))

            self._proc_ref = WNDPROC(_proc)
            # 一次性换取旧过程，避免中间出现 NULL 窗口过程的窗口期
            self._old_proc = user32.SetWindowLongPtrW(hwnd, -4,
                                                      ctypes.cast(self._proc_ref, ctypes.c_void_p))
        except Exception:
            self._proc_ref = None
        except Exception:
            self._proc_ref = None

    def _on_drop(self, paths):
        if self.running:
            self._status("压缩进行中，完成后才能添加新文件", C["warn"])
            return
        self._ingest(paths)
        self.lift()
        self.focus_force()


class SettingsWindow(tk.Toplevel):
    """设置窗口：资源管理器右键菜单开关 + 默认压缩体积。"""

    def __init__(self, app: "App"):
        super().__init__(app)
        self.app = app
        self.title("设置 · PicPress")
        self.configure(bg=C["canvas"])
        self.resizable(False, False)
        self.transient(app)
        self._job = None
        self._menu_on = bool(app.cfg.get("menu_on"))
        self._err = ""
        self._build()
        self._center_over(app)
        try:
            ico = App._asset("assets/icon.ico")
            if ico:
                self.iconbitmap(ico)
        except Exception:
            pass
        self.bind("<Escape>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._refresh()

    # ------------------------------------------------------------ 布局
    def _build(self):
        f = tk.Frame(self, bg=C["canvas"])
        f.pack(fill="both", expand=True, padx=px(24), pady=(px(20), px(16)))
        f.columnconfigure(0, weight=1)

        r = 0
        tk.Label(f, text="设置", bg=C["canvas"], fg=C["ink"], font=ft(19, "bold"),
                 anchor="w").grid(row=r, column=0, sticky="w")
        r += 1
        tk.Label(f, text="右键菜单与默认压缩体积", bg=C["canvas"], fg=C["mute"],
                 font=ft(12), anchor="w").grid(row=r, column=0, sticky="w", pady=(px(2), 0))
        r += 1
        self._hr(f, r)
        r += 1

        tk.Label(f, text="右键菜单", bg=C["canvas"], fg=C["mute"],
                 font=ft(11.5, "bold", mono=True), anchor="w").grid(row=r, column=0, sticky="w")
        r += 1
        self.chk = Check(f, "在图片右键菜单中显示「压缩到 2MB」", self._menu_on,
                         command=self._on_toggle, parent_bg=C["canvas"], w=430)
        self.chk.grid(row=r, column=0, sticky="w", pady=(px(9), 0))
        r += 1
        self.lbl_preview = tk.Label(f, text="", bg=C["canvas"], fg=C["body"], font=ft(11.5),
                                    anchor="w", justify="left", wraplength=px(414))
        self.lbl_preview.grid(row=r, column=0, sticky="w", pady=(px(7), 0))
        r += 1
        self.lbl_state = tk.Label(f, text="", bg=C["canvas"], fg=C["mute"], font=ft(11.5),
                                  anchor="w", justify="left", wraplength=px(414))
        self.lbl_state.grid(row=r, column=0, sticky="w", pady=(px(3), 0))
        r += 1
        self.lbl_path = tk.Label(f, text="", bg=C["canvas"], fg="#a9a9a9", font=ft(11),
                                 anchor="w", justify="left", wraplength=px(414))
        self.lbl_path.grid(row=r, column=0, sticky="w", pady=(px(6), 0))
        r += 1
        self._hr(f, r)
        r += 1

        tk.Label(f, text="默认压缩体积", bg=C["canvas"], fg=C["mute"],
                 font=ft(11.5, "bold", mono=True), anchor="w").grid(row=r, column=0, sticky="w")
        r += 1
        box = tk.Frame(f, bg=C["canvas"], highlightthickness=1,
                       highlightbackground=C["hair"], highlightcolor=C["hair"])
        box.grid(row=r, column=0, sticky="ew", pady=(px(9), 0))
        box.columnconfigure(0, weight=1)
        vcmd = (self.register(App._validate_num), "%P")
        self.ent = tk.Entry(box, bg=C["canvas"], fg=C["ink"], bd=0, relief="flat",
                            font=ft(17, "bold"), insertbackground=C["ink"],
                            validate="key", validatecommand=vcmd)
        self.ent.insert(0, f"{float(self.app.cfg.get('quick_mb', 2.0)):g}")
        self.ent.grid(row=0, column=0, sticky="ew", padx=(px(12), 0), pady=px(8))
        tk.Label(box, text="MB", bg=C["canvas"], fg=C["mute"], font=ft(12.5)).grid(
            row=0, column=1, padx=(0, px(12)))
        self.ent.bind("<KeyRelease>", self._on_size_change)
        r += 1
        self.lbl_name = tk.Label(f, text="", bg=C["canvas"], fg=C["mute"], font=ft(11),
                                 anchor="w", justify="left", wraplength=px(414))
        self.lbl_name.grid(row=r, column=0, sticky="w", pady=(px(7), 0))
        r += 1

        f.rowconfigure(r, weight=1)
        r += 1
        Btn(f, "关闭", self.close, kind="primary", w=84, h=34, font_size=13).grid(
            row=r, column=0, sticky="e")

    def _hr(self, parent, row):
        tk.Frame(parent, bg=C["hair"], height=1).grid(row=row, column=0, sticky="ew", pady=px(15))

    def _center_over(self, parent):
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - h) // 3)
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

    # ------------------------------------------------------------ 逻辑
    def _mb(self) -> float:
        try:
            v = float(self.ent.get().strip() or "0")
        except Exception:
            v = 0.0
        return v if v > 0 else 2.0

    def _on_toggle(self, v):
        self._menu_on = bool(v)
        self._apply()

    def _on_size_change(self, _=None):
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
        self._job = self.after(450, self._apply)

    def _apply(self):
        self._job = None
        mb = self._mb()
        self.app.cfg["quick_mb"] = mb
        self.app.cfg["menu_on"] = self._menu_on
        self._err = ""
        try:
            if self._menu_on:
                shellmenu.install(mb)
            else:
                shellmenu.uninstall()
        except Exception as e:
            self._err = f"{type(e).__name__}: {e}"
        self.app._save_cfg()
        self._refresh()

    def _refresh(self):
        label = shellmenu.size_label(self._mb())
        self.chk.set_text(f"在图片右键菜单中显示「压缩到 {label}」")
        self.lbl_preview.configure(text=f"菜单项预览：压缩到 {label} (PicPress)")
        if self._err:
            self.lbl_state.configure(text=f"注册失败：{self._err}", fg=C["err"])
        elif self._menu_on:
            self.lbl_state.configure(text="已启用 · 现在到资源管理器里右键任意图片试试", fg=C["link"])
        else:
            self.lbl_state.configure(text="未启用", fg=C["mute"])

        q = shellmenu.launch_prefix()[0]
        self.lbl_path.configure(text=f"菜单调用：{q}")
        self.lbl_name.configure(
            text=f"副本命名：原文件名_{label}.jpg，与原图同目录；原图不会被改动。")

    def close(self):
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
            self._apply()
        self.destroy()


def cli_main(args) -> int:
    """
    命令行模式（无界面），便于批处理与自动化：
      PicPress.exe --cli 图片或文件夹... --target 0.5 --out D:\\out [--fmt webp]
                   [--replace] [--no-backup] [--keep-exif] [--log result.txt]
    """
    import argparse
    import time as _t
    from concurrent.futures import ThreadPoolExecutor as _TPE

    ap = argparse.ArgumentParser(prog="PicPress", description="图片批量压缩（命令行）")
    ap.add_argument("paths", nargs="+", help="图片文件或文件夹")
    ap.add_argument("--target", type=float, default=0.5, help="目标体积，单位 MB")
    ap.add_argument("--out", default=None, help="输出文件夹")
    ap.add_argument("--fmt", default="keep", choices=["keep", "jpeg", "webp", "png"])
    ap.add_argument("--replace", action="store_true", help="压缩后替换原图")
    ap.add_argument("--no-backup", action="store_true", help="替换时不备份")
    ap.add_argument("--keep-exif", action="store_true", help="保留 EXIF")
    ap.add_argument("--log", default=None, help="把结果写入该文本文件")
    a = ap.parse_args(args)

    files = collect_images(a.paths)
    opts = Options(target_bytes=mb_to_bytes(a.target), fmt=a.fmt, replace=a.replace,
                   out_dir=a.out, backup=not a.no_backup, keep_exif=a.keep_exif)
    bak = make_backup_root() if (a.replace and not a.no_backup) else None

    t0 = _t.perf_counter()
    results = []
    with _TPE(max_workers=max(2, min(4, (os.cpu_count() or 4) // 2))) as ex:
        for r in ex.map(lambda f: compress_one(f, opts, bak), files):
            results.append(r)

    lines = [f"目标 {a.target} MB / 格式 {a.fmt} / 文件 {len(files)} 个"]
    ok = bad = 0
    tin = tout = 0
    for r in results:
        flag = "OK " if r.ok and r.reached else "BAD"
        if flag == "OK ":
            ok += 1
        else:
            bad += 1
        tin += r.src_size
        tout += r.out_size or r.src_size
        lines.append(f"[{flag}] {r.src.name}  {human_size(r.src_size)} -> "
                     f"{human_size(r.out_size) if r.out_size else '-'}  {r.note} {r.error}".rstrip())
    lines.append(f"完成 {ok} / 失败 {bad} · 共 {human_size(tin)} -> {human_size(tout)}"
                 f" · 用时 {_t.perf_counter() - t0:.1f}s")
    text = "\n".join(lines)
    try:
        # 窗口化 exe 没有自动设置控制台代码页，中文会变乱码，这里显式切到 UTF-8
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        print(text)
    except Exception:
        pass
    if a.log:
        try:
            Path(a.log).write_text(text, "utf-8")
        except Exception:
            pass
    return 0 if bad == 0 else 2


def main():
    argv = sys.argv[1:]
    if argv and argv[0] in ("--cli", "/cli"):
        sys.exit(cli_main(argv[1:]))
    if argv and argv[0] in ("--quick", "/quick"):
        from quick import run_quick
        sys.exit(run_quick(argv[1:]))
    if argv and argv[0] in ("--install-menu", "/install-menu"):
        mb = 2.0
        if len(argv) > 1:
            try:
                mb = max(0.01, float(argv[1]))
            except ValueError:
                pass
        cfg = load_config()
        cfg["menu_on"] = True
        cfg["quick_mb"] = mb
        save_config(cfg)
        shellmenu.install(mb)
        sys.exit(0)
    if argv and argv[0] in ("--uninstall-menu", "/uninstall-menu"):
        cfg = load_config()
        cfg["menu_on"] = False
        save_config(cfg)
        shellmenu.uninstall()
        sys.exit(0)
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()

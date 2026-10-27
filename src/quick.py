# -*- coding: utf-8 -*-
"""
右键快速压缩
============
从资源管理器右键菜单调用（`PicPress.exe --quick "图片路径"`）：
  · 按设置里的默认体积压缩；
  · 在原文件所在文件夹生成副本，命名「原文件名_2MB.jpg」；
  · 原图完全不动。

为什么需要队列 + 单实例锁
--------------------------
右键多选 5 张图时，Windows 会按 Player 模型为每个文件各启动一次本程序，
如果每次都弹一个窗口就是 5 个窗口。所以这里用命名互斥量：
第一个进程拿到「处理者」锁并开窗，其余进程只把路径写进临时队列就退出；
持锁进程稍等片刻把队列收齐，一次处理完。用户看到永远只有一个窗口。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import queue
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import tkinter as tk

import app as A  # 复用设计令牌与自绘控件（导入不会创建主窗口）
from compressor import Options, Result, compress_one, human_size, mb_to_bytes
from shellmenu import size_label

JOB_FILE = Path(tempfile.gettempdir()) / "picpress_quick.jobs"
APP_MUTEX = "Local\\PicPressQuickApp"
QUEUE_MUTEX = "Local\\PicPressQuickQueue"

k32 = ctypes.windll.kernel32
k32.CreateMutexW.restype = ctypes.c_void_p
k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
k32.WaitForSingleObject.restype = ctypes.c_uint
k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
k32.ReleaseMutex.argtypes = [ctypes.c_void_p]
k32.ReleaseMutex.restype = ctypes.c_bool
k32.CloseHandle.argtypes = [ctypes.c_void_p]
k32.CloseHandle.restype = ctypes.c_bool

WAIT_OBJECT_0 = 0


@contextmanager
def _hold(name: str, timeout: int = 3000):
    h = k32.CreateMutexW(None, False, name)
    got = bool(h) and k32.WaitForSingleObject(h, timeout) == WAIT_OBJECT_0
    try:
        yield got
    finally:
        if got:
            k32.ReleaseMutex(h)
        if h:
            k32.CloseHandle(h)


def _try_lock(name: str):
    h = k32.CreateMutexW(None, False, name)
    if h and k32.WaitForSingleObject(h, 0) == WAIT_OBJECT_0:
        return h
    if h:
        k32.CloseHandle(h)
    return None


def _unlock(h) -> None:
    k32.ReleaseMutex(h)
    k32.CloseHandle(h)


# ---------------------------------------------------------------- 队列


def _enqueue(paths: list[str]) -> None:
    with _hold(QUEUE_MUTEX) as ok:
        if not ok:
            return
        try:
            with open(JOB_FILE, "a", encoding="utf-8") as f:
                for p in paths:
                    f.write(str(p).replace("\n", " ") + "\n")
        except OSError:
            pass


def _drain() -> list[str]:
    with _hold(QUEUE_MUTEX) as ok:
        if not ok or not JOB_FILE.exists():
            return []
        try:
            lines = [x.strip() for x in JOB_FILE.read_text("utf-8").splitlines() if x.strip()]
        except OSError:
            lines = []
        try:
            JOB_FILE.unlink()
        except OSError:
            pass
        return lines


# ---------------------------------------------------------------- 配置


def load_target_mb() -> float:
    try:
        cfg = json.loads(A.CFG_FILE.read_text("utf-8"))
        v = float(cfg.get("quick_mb", 2.0))
        return v if v > 0 else 2.0
    except Exception:
        return 2.0


# ---------------------------------------------------------------- 窗口


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong),
                ("rcMonitor", ctypes.wintypes.RECT),
                ("rcWork", ctypes.wintypes.RECT),
                ("dwFlags", ctypes.c_ulong)]


def _place_on_cursor_monitor(root, w: int, h: int) -> None:
    """把窗口放在鼠标所在那块屏幕的中央（多显示器下也正确）。"""
    try:
        import ctypes.wintypes as wt

        pt = wt.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        hmon = ctypes.windll.user32.MonitorFromPoint(pt, 2)  # NEAREST
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        r = mi.rcWork
        x = r.left + max(0, (r.right - r.left - w) // 2)
        y = r.top + max(0, (r.bottom - r.top - h) // 2 - A.px(40))
        root.geometry(f"{w}x{h}+{x}+{y}")
    except Exception:
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")


def _short(name: str, limit: int = 34) -> str:
    return name if len(name) <= limit else name[: limit - 1] + "…"


class QuickWindow:
    def __init__(self, paths: list[str], target_mb: float):
        self.target_mb = target_mb
        self.label = size_label(target_mb)
        self.todo = list(dict.fromkeys(paths))   # 去重保序
        self.total = len(self.todo)
        self.done = 0
        self.results = []
        self.evq: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.finished = False

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass

        self.root = tk.Tk()
        A.SC = max(1.0, min(2.5, round(self.root.winfo_fpixels("1i") / 96.0, 3)))
        self._build()

    # ------------------------------------------------------------ UI
    def _build(self):
        C = A.C
        r = self.root
        r.title(f"PicPress · 压缩到 {self.label}")
        r.configure(bg=C["canvas"])
        r.resizable(False, False)
        r.attributes("-topmost", True)
        self._set_icon()

        w, h = A.px(430), A.px(150)
        _place_on_cursor_monitor(r, w, h)

        pad = A.px(22)
        f = tk.Frame(r, bg=C["canvas"])
        f.pack(fill="both", expand=True, padx=pad, pady=(A.px(16), A.px(14)))
        f.columnconfigure(1, weight=1)

        mark = tk.Canvas(f, width=A.px(22), height=A.px(22), bg=C["canvas"],
                         highlightthickness=0, bd=0)
        mark.grid(row=0, column=0, sticky="w", padx=(0, A.px(10)))
        A.rrect(mark, 0, 0, A.px(22), A.px(22), A.px(6), fill=C["ink"], outline="")
        A.icon(mark, "arrow-down", A.px(11), A.px(11), A.px(13), "#ffffff", 1.4)

        self.lbl_title = tk.Label(f, text=f"正在压缩 · 0/{self.total}", bg=C["canvas"],
                                  fg=C["ink"], font=A.ft(14, "bold"), anchor="w")
        self.lbl_title.grid(row=0, column=1, sticky="w")

        self.btn_cancel = A.Btn(f, "取消", self._on_cancel, kind="ghost", w=58, h=26,
                                radius=6, font_size=11.5, parent_bg=C["canvas"])
        self.btn_cancel.grid(row=0, column=2, sticky="e")

        self.lbl_file = tk.Label(f, text="准备中…", bg=C["canvas"], fg=C["mute"],
                                 font=A.ft(12), anchor="w")
        self.lbl_file.grid(row=1, column=0, columnspan=3, sticky="w", pady=(A.px(9), 0))

        self.bar = A.Bar(f, h=6, parent_bg=C["canvas"])
        self.bar.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(A.px(12), A.px(9)))

        self.lbl_hint = tk.Label(
            f, text=f"压缩到 {self.label} · 副本生成在原文件所在文件夹",
            bg=C["canvas"], fg=C["mute"], font=A.ft(11), anchor="w")
        self.lbl_hint.grid(row=3, column=0, columnspan=3, sticky="w")

        r.bind("<Escape>", lambda e: self._on_cancel())
        r.protocol("WM_DELETE_WINDOW", self._on_cancel)
        r.after(80, self._poll)

    def _set_icon(self):
        p = A.App._asset("assets/icon.ico")
        if p:
            try:
                self.root.iconbitmap(p)
            except Exception:
                pass

    # ------------------------------------------------------------ 任务
    def _on_cancel(self):
        self.cancel.set()
        self.btn_cancel.set_enabled(False)
        self.lbl_hint.configure(text="正在停止…")

    def _work(self):
        opts_kwargs = dict(target_bytes=mb_to_bytes(self.target_mb), fmt="keep",
                           replace=False, keep_exif=False, name_suffix=f"_{self.label}")
        while not self.cancel.is_set():
            if not self.todo:
                extra = _drain()
                if not extra:
                    break
                for p in extra:
                    if p not in self.todo:
                        self.todo.append(p)
                self.total = len(self.todo) + self.done
                continue
            p = self.todo.pop(0)
            src = Path(p)
            self.evq.put(("begin", (src, self.done + 1, max(self.total, self.done + 1))))
            opts = Options(out_dir=str(src.parent), **opts_kwargs)
            try:
                res = compress_one(src, opts)
            except Exception as e:  # 兜底，任何意外都不能让窗口卡死
                res = Result(False, src, src, 0, 0, None, 1.0,
                             error=f"{type(e).__name__}: {e}", reached=False)
            self.done += 1
            self.evq.put(("res", res))
        self.evq.put(("done", None))

    def run(self) -> None:
        threading.Thread(target=self._work, daemon=True).start()
        self.root.mainloop()

    # ------------------------------------------------------------ 事件循环
    def _poll(self):
        try:
            while True:
                kind, payload = self.evq.get_nowait()
                if kind == "begin":
                    self._on_begin(*payload)
                elif kind == "res":
                    self._on_result(payload)
                elif kind == "done":
                    self._on_done()
        except queue.Empty:
            pass
        if not self.finished:
            self.root.after(60, self._poll)

    def _on_begin(self, src: Path, idx: int, total: int):
        self.lbl_title.configure(text=f"正在压缩 · {idx}/{max(total, idx)}")
        self.lbl_file.configure(text=_short(src.name), fg=A.C["body"])

    def _on_result(self, r):
        self.results.append(r)
        self.bar.set(self.done / max(1, self.total))
        if r.ok:
            self.lbl_file.configure(
                text=f"{_short(r.out.name)} · {human_size(r.out_size)}"
                     f"（原 {human_size(r.src_size)}）", fg=A.C["body"])
        else:
            self.lbl_file.configure(text=f"{_short(r.src.name)} 失败：{r.error[:40]}",
                                    fg=A.C["err"])

    def _on_done(self):
        self.finished = True
        ok = [r for r in self.results if r.ok]
        bad = [r for r in self.results if not r.ok]
        self.bar.set(1.0 if not bad else self.done / max(1, self.total),
                     color=A.C["ink"] if not bad else A.C["warn"])
        self.btn_cancel.grid_remove()

        if self.cancel.is_set():
            self.lbl_title.configure(text=f"已取消 · 完成 {len(ok)} 个")
            self.lbl_hint.configure(text="窗口即将关闭")
            delay = 1200
        elif not self.results:
            self.lbl_title.configure(text="没有可处理的图片")
            self.lbl_hint.configure(text="窗口即将关闭")
            delay = 1400
        elif bad:
            self.lbl_title.configure(text=f"完成 {len(ok)} 个 · 失败 {len(bad)} 个",
                                     fg=A.C["warn"])
            self.lbl_file.configure(text=f"{_short(bad[0].src.name)}：{bad[0].error[:44]}",
                                    fg=A.C["err"])
            self.lbl_hint.configure(text="窗口即将关闭")
            delay = 3600
        else:
            one = ok[-1]
            self.lbl_title.configure(text="已完成" if len(ok) == 1 else f"已完成 {len(ok)} 个")
            if len(ok) == 1:
                self.lbl_file.configure(
                    text=f"{_short(one.out.name)} · {human_size(one.out_size)}"
                         f"（原 {human_size(one.src_size)}）")
            else:
                saved = sum(r.src_size for r in ok) - sum(r.out_size for r in ok)
                self.lbl_file.configure(text=f"共生成 {len(ok)} 个副本 · 节省 {human_size(saved)}")
            self.lbl_hint.configure(text="窗口即将关闭")
            delay = 1500

        self.root.after(delay, self._close)

    def _close(self):
        try:
            self.root.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------- 入口


def run_quick(args: list[str]) -> int:
    paths = [a for a in args if a and not a.startswith("--")]
    if not paths:
        return 0

    _enqueue(paths)
    lock = _try_lock(APP_MUTEX)
    if lock is None:
        return 0          # 已有窗口在处理，本进程只负责投递路径

    try:
        time.sleep(0.45)  # 收集窗口：等 Explorer 把其余选中项也送进来
        files = _drain()
        if not files:
            return 0
        QuickWindow(files, load_target_mb()).run()
    finally:
        _unlock(lock)
    return 0


if __name__ == "__main__":
    sys.exit(run_quick(sys.argv[1:]))

# -*- coding: utf-8 -*-
"""交互测试：向真实窗口投递鼠标消息，验证自绘控件能响应点击。"""
import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import app as A  # noqa: E402

user32 = ctypes.windll.user32


def click(widget):
    """把控件中心点换算成窗口客户区坐标，投递按下/抬起。"""
    a = widget.winfo_toplevel()
    hwnd = user32.GetAncestor(a.winfo_id(), 2)
    x = widget.winfo_rootx() - a.winfo_rootx() + widget.winfo_width() // 2
    y = widget.winfo_rooty() - a.winfo_rooty() + widget.winfo_height() // 2
    lp = (y << 16) | (x & 0xFFFF)
    user32.PostMessageW(hwnd, 0x0200, 0, lp)          # WM_MOUSEMOVE
    user32.PostMessageW(hwnd, 0x0201, 1, lp)          # WM_LBUTTONDOWN
    user32.PostMessageW(hwnd, 0x0202, 0, lp)          # WM_LBUTTONUP


def pump(a, n=12):
    for _ in range(n):
        a.update()
        a.after(30)
        a.update_idletasks()


a = A.App()
a.update()
a.deiconify()
a.lift()
a.focus_force()
pump(a, 6)

print("初始目标 =", a.ent.get(), "| 模式 =", a.mode, "| 格式 =", a.seg.get())

# 点「2 MB」预设
chip = [c for c, mb in a.chips if abs(mb - 2) < 1e-9][0]
click(chip)
pump(a)
print("点击 2MB 后目标 =", a.ent.get(), "=> ", "通过" if a.ent.get() == "2" else "失败")

# 点「替换原图」卡片
click(a.card_replace)
pump(a)
print("点击 替换原图 后模式 =", a.mode, "=>", "通过" if a.mode == "replace" else "失败")

# 点输出格式 WebP
seg = a.seg
x = seg.winfo_width() * 5 // 8
hwnd = user32.GetAncestor(a.winfo_id(), 2)
sx = seg.winfo_rootx() - a.winfo_rootx() + x
sy = seg.winfo_rooty() - a.winfo_rooty() + seg.winfo_height() // 2
lp = (sy << 16) | (sx & 0xFFFF)
user32.PostMessageW(hwnd, 0x0201, 1, lp)
user32.PostMessageW(hwnd, 0x0202, 0, lp)
pump(a)
print("点击 WebP 后格式 =", a.seg.get(), "=>", "通过" if a.seg.get() == "webp" else "失败")

# 点备份复选框
before = a.chk_backup.get()
click(a.chk_backup)
pump(a)
print("点击 备份复选框 :", before, "->", a.chk_backup.get(),
      "=>", "通过" if a.chk_backup.get() != before else "失败（可能被禁用，属预期）")

print("拖拽钩子:", "已安装" if getattr(a, "_proc_ref", None) else "未安装")
a.destroy()

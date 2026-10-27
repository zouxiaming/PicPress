# -*- coding: utf-8 -*-
"""
Windows 资源管理器右键菜单注册（当前用户，无需管理员）
=====================================================
在 HKCU\\Software\\Classes\\SystemFileAssociations\\.jpg\\shell 下挂一个动词，
选中图片右键即可看到「压缩到 2MB (PicPress)」。

用 SystemFileAssociations 而不是 * 或具体 ProgID，好处是：
  · 只对图片扩展名出现，不会污染所有文件；
  · 不受用户改过默认打开方式的影响；
  · 只写 HKCU，不需要提权。
"""

from __future__ import annotations

import sys
import winreg
from pathlib import Path

EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif")
VERB = "PicPress.Quick"
BASE = r"Software\Classes\SystemFileAssociations"

SHCNE_ASSOCCHANGED = 0x08000000
SHCNF_IDLIST = 0x0000


# ---------------------------------------------------------------- 体积标签


def size_label(mb: float) -> str:
    """把体积换算成文件名里用的短标签：2 → 2MB，0.5 → 0.5MB。"""
    return f"{float(mb):g}MB"


def menu_text(mb: float) -> str:
    return f"压缩到 {size_label(mb)} (PicPress)"


# ---------------------------------------------------------------- 启动命令


def launch_prefix() -> list[str]:
    """返回启动 PicPress 的命令前缀（不含待处理文件）。"""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    # 源码运行时用 pythonw，避免每次右键都闪一个黑框
    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe")
    exe = str(pyw) if pyw.exists() else str(py)
    return [exe, str(Path(__file__).resolve().parent / "app.py")]


def command_line() -> str:
    quoted = " ".join(f'"{p}"' for p in launch_prefix())
    return f'{quoted} --quick "%1"'


def icon_spec() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}",0'
    ico = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"
    return f'"{ico}",0' if ico.exists() else ""


# ---------------------------------------------------------------- 注册 / 卸载


def _notify_shell() -> None:
    """通知资源管理器刷新图标与关联，省得用户重启桌面。"""
    try:
        import ctypes

        ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
    except Exception:
        pass


def install(mb: float) -> None:
    """为所有图片扩展名注册右键菜单。"""
    cmd = command_line()
    icon = icon_spec()
    text = menu_text(mb)
    for ext in EXTS:
        root = f"{BASE}\\{ext}\\shell\\{VERB}"
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, root, 0, winreg.KEY_WRITE) as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, text)
            if icon:
                winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, icon)
            # Player 模型：允许多选，最多 100 个文件
            winreg.SetValueEx(k, "MultiSelectModel", 0, winreg.REG_SZ, "Player")
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, root + r"\command", 0,
                                winreg.KEY_WRITE) as k:
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, cmd)
    _notify_shell()


def _delete_tree(path: str) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    sub = winreg.EnumKey(k, 0)
                except OSError:
                    break
                _delete_tree(path + "\\" + sub)
    except FileNotFoundError:
        return
    except OSError:
        return
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        pass


def uninstall() -> None:
    for ext in EXTS:
        _delete_tree(f"{BASE}\\{ext}\\shell\\{VERB}")
    _notify_shell()


def current() -> dict | None:
    """读取已注册的命令与文字，用于判断是否需要重新注册。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            f"{BASE}\\.jpg\\shell\\{VERB}") as k:
            text = winreg.QueryValueEx(k, None)[0]
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            f"{BASE}\\.jpg\\shell\\{VERB}\\command") as k:
            cmd = winreg.QueryValueEx(k, None)[0]
        return {"text": text, "command": cmd}
    except OSError:
        return None


def is_installed() -> bool:
    return current() is not None


def sync(mb: float) -> bool:
    """
    让注册表与实际状态一致：命令或文字变了就重写一遍。
    返回 True 表示已注册。
    """
    cur = current()
    want_cmd = command_line()
    want_text = menu_text(mb)
    if cur and cur["command"] == want_cmd and cur["text"] == want_text:
        return True
    install(mb)
    return True


if __name__ == "__main__":
    # 便于手动调试：python shellmenu.py install 2
    if len(sys.argv) > 1 and sys.argv[1] == "uninstall":
        uninstall()
        print("已移除右键菜单")
    else:
        mb = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
        install(mb)
        print(f"已注册：{menu_text(mb)}")
        print(f"命令：{command_line()}")

# -*- coding: utf-8 -*-
"""一键打包成单文件 EXE：dist/PicPress.exe"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"

VERSION_FILE = ROOT / "build" / "version.txt"
VERSION_TEXT = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(1, 0, 0, 0), prodvers=(1, 0, 0, 0),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404B0', [
        StringStruct('CompanyName', 'PicPress'),
        StringStruct('FileDescription', 'PicPress 图片批量压缩'),
        StringStruct('FileVersion', '1.0.0'),
        StringStruct('InternalName', 'PicPress'),
        StringStruct('LegalCopyright', 'PicPress'),
        StringStruct('OriginalFilename', 'PicPress.exe'),
        StringStruct('ProductName', 'PicPress'),
        StringStruct('ProductVersion', '1.0.0')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""

ARGS = [
    "--noconfirm", "--clean", "--windowed",
    "--icon", str(ROOT / "assets" / "icon.ico"),
    "--add-data", f"{ROOT / 'assets' / 'icon.ico'};assets",
    "--version-file", str(VERSION_FILE),
    "--paths", str(ROOT / "src"),
    # 裁剪无关模块，减小体积
    "--exclude-module", "numpy",
    "--exclude-module", "pytest",
    "--exclude-module", "matplotlib",
    "--exclude-module", "PyQt5",
    "--exclude-module", "PySide2",
    "--exclude-module", "PySide6",
    "--exclude-module", "IPython",
    "--exclude-module", "notebook",
    "--exclude-module", "PIL.ImageQt",
    "--exclude-module", "PIL.ImageTk",
    "--exclude-module", "tkinter.test",
    "--exclude-module", "unittest",
    "--exclude-module", "pydoc_data",
    str(ROOT / "src" / "app.py"),
]


def build_file() -> Path:
    """单文件版：便于分发，但每次启动要解包，约 4 秒。"""
    cmd = [str(PY), "-m", "PyInstaller", "--onefile", "--name", "PicPress-Portable"] + ARGS
    subprocess.run(cmd, cwd=str(ROOT), check=True)
    return ROOT / "dist" / "PicPress-Portable.exe"


def build_dir() -> Path:
    """文件夹版：启动约 0.4 秒，右键菜单用它体验最好。"""
    cmd = [str(PY), "-m", "PyInstaller", "--onedir", "--name", "PicPress"] + ARGS
    subprocess.run(cmd, cwd=str(ROOT), check=True)
    return ROOT / "dist" / "PicPress" / "PicPress.exe"


def main():
    VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    VERSION_FILE.write_text(VERSION_TEXT, "utf-8")

    only = sys.argv[1] if len(sys.argv) > 1 else "both"
    out = []
    if only in ("dir", "both"):
        out.append(("文件夹版", build_dir()))
    if only in ("file", "both"):
        out.append(("单文件版", build_file()))

    print()
    for label, p in out:
        print(f"{label}：{p}  ({p.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()

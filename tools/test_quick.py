# -*- coding: utf-8 -*-
"""右键快速压缩端到端测试：测量窗口出现耗时、验证副本命名与体积。"""
import ctypes
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
EXE = ROOT / "dist" / "PicPress" / "PicPress.exe"
user32 = ctypes.windll.user32


def find_window(keyword="PicPress · 压缩到"):
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n > 0:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if keyword in buf.value:
                    found.append(hwnd)
                    return False
        return True

    user32.EnumWindows(cb, None)
    return found[0] if found else 0


def clear(folder: Path):
    for f in folder.glob("*_2MB*.jpg"):
        f.unlink(missing_ok=True)
    q = Path(__import__("tempfile").gettempdir()) / "picpress_quick.jobs"
    q.unlink(missing_ok=True)


def wait_window(t0, limit=20.0):
    while time.time() - t0 < limit:
        if find_window():
            return time.time() - t0
        time.sleep(0.03)
    return None


def case_one():
    folder = ROOT / "_qtest"
    clear(folder)
    src = folder / "照片_原图.jpg"
    print(f"\n[单文件] {src.name} {src.stat().st_size/1048576:.2f} MB")
    t0 = time.time()
    subprocess.Popen([str(EXE), "--quick", str(src)])
    dt = wait_window(t0)
    print(f"  窗口出现：{dt*1000:.0f} ms" if dt else "  窗口未出现（失败）")
    time.sleep(6)
    out = folder / "照片_原图_2MB.jpg"
    ok = out.exists()
    print(f"  生成副本：{out.name} {'✓' if ok else '✗'}"
          + (f"  {out.stat().st_size/1048576:.2f} MB" if ok else ""))
    if ok:
        assert out.stat().st_size <= 2 * 1024 * 1024, "超出 2MB"
        assert out.read_bytes()[:2] == b"\xff\xd8", "不是有效 JPEG"
    return ok


def case_multi():
    folder = ROOT / "_qtest"
    clear(folder)
    a = folder / "照片_原图.jpg"
    b = folder / "小图.jpg"
    print(f"\n[多选两个文件] 模拟资源管理器各启动一次")
    t0 = time.time()
    subprocess.Popen([str(EXE), "--quick", str(a)])
    subprocess.Popen([str(EXE), "--quick", str(b)])
    dt = wait_window(t0)
    print(f"  窗口出现：{dt*1000:.0f} ms" if dt else "  窗口未出现（失败）")
    time.sleep(7)
    outs = sorted(p.name for p in folder.glob("*_2MB*.jpg"))
    print(f"  生成副本：{outs}")
    ok = len(outs) == 2
    print("  结果：" + ("通过（只弹一个窗口，两个文件都处理了）" if ok else "失败"))
    return ok


def case_size_too_big():
    """目标体积大于原图时，应生成原样副本（不重压、不掉画质）。"""
    folder = ROOT / "_qtest"
    clear(folder)
    src = folder / "小图.jpg"
    print(f"\n[原图小于目标] {src.name} {src.stat().st_size/1024:.0f} KB → 目标 2MB")
    subprocess.Popen([str(EXE), "--quick", str(src)])
    time.sleep(5)
    out = folder / "小图_2MB.jpg"
    ok = out.exists() and out.stat().st_size == src.stat().st_size
    print(f"  副本 {out.name if out.exists() else '缺失'} "
          f"{out.stat().st_size if out.exists() else 0} bytes = 原样复制：{'✓' if ok else '✗'}")
    return ok


if __name__ == "__main__":
    if not EXE.exists():
        print("请先打包")
        sys.exit(1)
    results = [case_one(), case_multi(), case_size_too_big()]
    print("\n全部通过" if all(results) else "\n有失败项")
    sys.exit(0 if all(results) else 1)

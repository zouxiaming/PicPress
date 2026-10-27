# -*- coding: utf-8 -*-
"""抓取 PicPress 窗口截图（用 PrintWindow，窗口被遮挡也能正确抓取）。
用法：shot.py <名字> [窗口标题关键字]
"""
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageGrab

OUT = Path(__file__).parent.parent / "_shots"
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
user32.PrintWindow.restype = ctypes.c_bool
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                           ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
user32.GetWindowDC.restype = ctypes.c_void_p
user32.GetWindowDC.argtypes = [ctypes.c_void_p]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


def find_window(keyword: str) -> int:
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
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


def capture(hwnd) -> Image.Image:
    r = wintypes.RECT()
    ok = ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r))
    if ok != 0 or r.right - r.left <= 0:
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top

    hdc = user32.GetWindowDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mdc, bmp)
    # PW_RENDERFULLCONTENT = 2，能拿到 DWM 合成后的内容
    drawn = user32.PrintWindow(hwnd, mdc, 2)
    gdi32.SelectObject(mdc, bmp)
    if not drawn:
        gdi32.DeleteObject(bmp); gdi32.DeleteDC(mdc); user32.ReleaseDC(hwnd, hdc)
        return ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom), all_screens=True)

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth, bi.biHeight = w, -h        # 负高度 = 自上而下
    bi.biPlanes, bi.biBitCount = 1, 32
    bi.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)

    img = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1).convert("RGB")
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mdc)
    user32.ReleaseDC(hwnd, hdc)
    return img


def main():
    OUT.mkdir(exist_ok=True)
    name = sys.argv[1] if len(sys.argv) > 1 else "shot"
    keyword = sys.argv[2] if len(sys.argv) > 2 else "PicPress"

    hwnd = 0
    for _ in range(40):
        hwnd = find_window(keyword)
        if hwnd:
            break
        time.sleep(0.25)
    if not hwnd:
        print(f"未找到窗口（关键字 {keyword}）")
        sys.exit(1)

    user32.ShowWindow(hwnd, 9)
    time.sleep(0.6)
    img = capture(hwnd)
    p = OUT / f"{name}.png"
    img.save(p)
    print(f"已保存 {p} 尺寸={img.size}")


if __name__ == "__main__":
    main()

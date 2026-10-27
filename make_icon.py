# -*- coding: utf-8 -*-
"""生成应用图标 assets/icon.ico（多尺寸，纯矢量绘制，不用外部素材）。"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).parent / "assets"
SS = 8  # 超采样倍数，抗锯齿


def draw_mark(size: int) -> Image.Image:
    S = size * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角底板（墨黑）
    r = int(S * 0.235)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=r, fill=(23, 23, 23, 255))

    white = (255, 255, 255, 255)
    # 小尺寸要减少元素、加粗笔画，否则缩到 16px 会糊成一坨
    simple = size <= 24
    lw = int(S * (0.135 if simple else 0.082))
    cap = lw // 2

    if simple:
        # 极简版：两个相对的粗箭头，中间留缝 → 一眼看出「向中间挤压」
        ty, tm = S * 0.235, S * 0.405
        by, bm = S * 0.765, S * 0.595
        d.line([(S * 0.255, ty), (S * 0.5, tm)], fill=white, width=lw)
        d.line([(S * 0.745, ty), (S * 0.5, tm)], fill=white, width=lw)
        d.line([(S * 0.255, by), (S * 0.5, bm)], fill=white, width=lw)
        d.line([(S * 0.745, by), (S * 0.5, bm)], fill=white, width=lw)
        for px_, py_ in [(S * 0.255, ty), (S * 0.745, ty), (S * 0.5, tm),
                         (S * 0.255, by), (S * 0.745, by), (S * 0.5, bm)]:
            d.ellipse([px_ - cap, py_ - cap, px_ + cap, py_ + cap], fill=white)
        return img.resize((size, size), Image.LANCZOS)

    # 上箭头（朝下的 V）
    ay1, ay2 = int(S * 0.30), int(S * 0.415)
    d.line([(S * 0.30, ay1), (S * 0.50, ay2)], fill=white, width=lw)
    d.line([(S * 0.70, ay1), (S * 0.50, ay2)], fill=white, width=lw)
    # 中横条
    by_ = int(S * 0.50)
    d.line([(S * 0.28, by_), (S * 0.72, by_)], fill=white, width=lw)
    # 下箭头（朝上的 V）
    cy1, cy2 = int(S * 0.70), int(S * 0.585)
    d.line([(S * 0.30, cy1), (S * 0.50, cy2)], fill=white, width=lw)
    d.line([(S * 0.70, cy1), (S * 0.50, cy2)], fill=white, width=lw)

    # 圆头端点，让线条更柔和
    for px_, py_ in [
        (S * 0.30, ay1), (S * 0.70, ay1), (S * 0.50, ay2),
        (S * 0.28, by_), (S * 0.72, by_),
        (S * 0.30, cy1), (S * 0.70, cy1), (S * 0.50, cy2),
    ]:
        d.ellipse([px_ - cap, py_ - cap, px_ + cap, py_ + cap], fill=white)

    return img.resize((size, size), Image.LANCZOS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [draw_mark(s) for s in sizes]
    ico = OUT / "icon.ico"
    imgs[-1].save(ico, format="ICO", sizes=[(s, s) for s in sizes])
    imgs[-1].save(OUT / "icon.png")
    print(f"已生成 {ico} ({ico.stat().st_size} bytes)")
    for s in (16, 32, 48):
        draw_mark(s).save(OUT / f"preview_{s}.png")


if __name__ == "__main__":
    main()

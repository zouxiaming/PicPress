# -*- coding: utf-8 -*-
"""引擎自测：造几张测试图，压到指定体积，验证结果 == 目标以内。"""
import os
import random
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).parent / "src"))
from compressor import Options, compress_one, human_size, mb_to_bytes  # noqa

ROOT = Path(__file__).parent / "_test"
IN = ROOT / "in"
OUT = ROOT / "out"


def make_images():
    IN.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(7)

    # 1) 大照片：噪声细节 + 渐变，模拟真实照片的体积（用 C 层生成，速度快）
    w, h = 4200, 2800
    noise = Image.effect_noise((w, h), 46).convert("RGB")
    grad = Image.linear_gradient("L").resize((w, h)).convert("RGB")
    tint = Image.new("RGB", (w, h), (60, 120, 190))
    im = Image.blend(Image.blend(noise, grad, 0.45), tint, 0.25)
    im.save(IN / "photo_big.jpg", quality=96)

    # 2) 带透明通道的 PNG（UI 素材），大量半透明
    p = Image.new("RGBA", (1600, 1200), (0, 0, 0, 0))
    d = ImageDraw.Draw(p)
    for i in range(160):
        x0 = rnd.randint(0, 1500)
        y0 = rnd.randint(0, 1100)
        d.ellipse([x0, y0, x0 + rnd.randint(40, 200), y0 + rnd.randint(40, 200)],
                  fill=(rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(60, 255)))
    p = p.filter(ImageFilter.GaussianBlur(1))
    p.save(IN / "alpha.png")

    # 3) 已经很小、本来就达标的 jpg
    s = Image.new("RGB", (640, 480), (240, 244, 248))
    ImageDraw.Draw(s).text((30, 30), "already small", fill=(20, 20, 20))
    s.save(IN / "tiny.jpg", quality=80)

    # 4) 多帧 GIF
    frames = []
    for i in range(12):
        f = Image.new("RGB", (600, 400), (30, i * 18 % 256, 200 - i * 12))
        ImageDraw.Draw(f).ellipse([i * 40, 120, i * 40 + 160, 280], fill=(255, 200 - i * 10, 60))
        frames.append(f)
    frames[0].save(IN / "anim.gif", save_all=True, append_images=frames[1:], duration=90, loop=0)

    # 5) BMP
    s.resize((1400, 1400)).save(IN / "flat.bmp")


def run(target_mb, fmt="keep", replace=False):
    opts = Options(target_bytes=mb_to_bytes(target_mb), fmt=fmt, replace=replace, out_dir=OUT)
    print(f"\n=== 目标 {target_mb} MB / 格式 {fmt} / 替换原图 {replace} ===")
    ok = True
    for f in sorted(IN.iterdir()):
        t0 = time.perf_counter()
        r = compress_one(f, opts)
        dt = time.perf_counter() - t0
        flag = "OK " if r.ok and (r.reached or r.skipped) else "BAD"
        if flag == "BAD":
            ok = False
        print(f"  [{flag}] {f.name:14s} {human_size(r.src_size):>9s} -> "
              f"{human_size(r.out_size) if r.out_size else '-':>9s}  "
              f"{dt:5.2f}s q={r.quality} scale={r.scale:g}  {r.note} {r.error}")
        if r.ok and not r.skipped:
            assert r.out_size <= opts.target_bytes, f"{f.name} 超出目标：{r.out_size}"
            assert Path(r.out).exists(), "输出文件不存在"
    return ok


if __name__ == "__main__":
    t_all = time.perf_counter()
    make_images()
    print("源图：")
    for f in sorted(IN.iterdir()):
        print(f"  {f.name:14s} {human_size(f.stat().st_size)}")
    good = True
    good &= run(0.5)
    good &= run(2, fmt="webp")
    good &= run(0.05)
    print(f"\n总耗时 {time.perf_counter() - t_all:.2f}s")
    print("全部通过" if good else "有失败项")

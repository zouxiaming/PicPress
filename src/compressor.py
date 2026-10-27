# -*- coding: utf-8 -*-
"""
PicPress 压缩引擎
=================
把图片压缩到「指定体积」以内（不是指定质量），并尽量保留最高画质。

核心思路
--------
1. 二分搜索质量参数（quality），找到「体积刚好不超过目标」的最高质量；
2. 若在最低质量下仍然超标，就逐级缩小分辨率再重复第 1 步；
3. 每一轮都会记录结果，用「质量 × 分辨率」的取舍规则挑最优解；
4. PNG 是无损格式，没有 quality 概念，改用「无损优化 → 调色板量化降色」的路径；
5. 原图本来就小于目标体积时直接跳过（不重压，画质无损）。

本模块不依赖任何 GUI 库，可单独用于命令行或单元测试。
"""

from __future__ import annotations

import io
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageFile, ImageOps

# 允许读取截断的图片（网络下载不完整的图很常见），尽量抢救而不是直接报错
ImageFile.LOAD_TRUNCATED_IMAGES = True
try:
    Image.MAX_IMAGE_PIXELS = None  # 取消「超大图」保护，允许处理巨幅图
except Exception:
    pass


# ---------------------------------------------------------------- 常量

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}

EXT_OF_FMT = {"jpeg": ".jpg", "webp": ".webp", "png": ".png"}

# (缩放比例, 该比例下「可以接受」的最低质量)
# 原则：优先保住分辨率。只有原尺寸下质量已经烂到 38 以下（画面出现明显块状）
# 才考虑缩小尺寸换取更高画质。这符合「压到 x MB」时用户对尺寸不变的预期。
SCALES = [
    (1.00, 38),
    (0.90, 52),
    (0.78, 62),
    (0.64, 72),
    (0.50, 80),
    (0.38, 86),
    (0.28, 1),
]

# PNG 尝试的调色板色数阶梯（从高到低，命中即停）
PNG_COLORS = (256, 192, 144, 96, 64, 48, 32, 16, 8)

ALPHA_MODES = ("RGBA", "LA", "PA", "RGBa", "La")


# ---------------------------------------------------------------- 数据结构


@dataclass
class Options:
    """一次压缩任务的参数。"""

    target_bytes: int          # 目标体积（字节），压缩结果必须 <= 这个值
    fmt: str = "keep"          # keep / jpeg / webp / png
    replace: bool = False      # True = 压缩后替换原图；False = 输出到 out_dir
    out_dir: str | Path | None = None
    backup: bool = True        # 替换模式下先把原图备份出来
    keep_exif: bool = False    # 是否保留 EXIF（默认剥离，省体积且保护隐私）
    name_suffix: str = ""      # 输出文件名追加的后缀，如 "_2MB"（右键快速压缩用）


@dataclass
class Result:
    ok: bool
    src: Path
    out: Path
    src_size: int
    out_size: int
    quality: int | None        # None = 无损 / 调色板路径
    scale: float
    note: str = ""
    error: str = ""
    reached: bool = True       # 是否真的达到了目标体积
    skipped: bool = False      # 原图已达标，未重新编码


# ---------------------------------------------------------------- 小工具


def human_size(n: int | float | None) -> str:
    """把字节数格式化成人类可读的体积。"""
    if n is None:
        return "-"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(n)} B"
            return f"{n:.2f} {unit}" if n < 10 else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def mb_to_bytes(mb: float) -> int:
    return max(1, int(round(float(mb) * 1024 * 1024)))


def resolve_fmt(src_ext: str, user_fmt: str) -> str:
    """决定实际输出格式。"""
    src_ext = src_ext.lower()
    if user_fmt in EXT_OF_FMT:
        return user_fmt
    # keep：尽量保持原容器，无法保持的（BMP/TIFF）转 PNG
    if src_ext in (".jpg", ".jpeg"):
        return "jpeg"
    if src_ext == ".png":
        return "png"
    if src_ext == ".webp":
        return "webp"
    if src_ext in (".bmp", ".tif", ".tiff"):
        return "png"
    if src_ext == ".gif":
        return "png"
    return "jpeg"


def _resize(img: Image.Image, scale: float) -> Image.Image:
    if scale >= 0.999:
        return img
    w = max(1, int(round(img.width * scale)))
    h = max(1, int(round(img.height * scale)))
    return img.resize((w, h), Image.LANCZOS)


def _has_alpha(img: Image.Image) -> bool:
    if img.mode in ALPHA_MODES:
        return True
    if img.mode == "P" and "transparency" in img.info:
        return True
    return False


def _flatten(img: Image.Image, bg=(255, 255, 255)) -> Image.Image:
    """把带透明通道的图压平到纯色背景（JPEG 不支持透明）。"""
    rgba = img.convert("RGBA")
    canvas = Image.new("RGB", rgba.size, bg)
    canvas.paste(rgba, mask=rgba.split()[-1])
    return canvas


def _prepare(img: Image.Image, fmt: str) -> Image.Image:
    """按目标格式把图像调整成可编码的模式。"""
    if img.mode in ("I", "I;16", "I;16B", "I;16L", "F"):
        img = img.convert("L")
    if fmt == "jpeg":
        if _has_alpha(img):
            return _flatten(img)
        if img.mode in ("CMYK", "YCbCr", "LAB", "HSV"):
            return img.convert("RGB")
        if img.mode == "P":
            return img.convert("RGB")
        if img.mode == "L":
            return img
        return img.convert("RGB")
    if fmt == "webp":
        if _has_alpha(img):
            return img.convert("RGBA")
        if img.mode in ("CMYK", "YCbCr", "LAB", "HSV"):
            return img.convert("RGB")
        if img.mode == "P":
            return img.convert("RGB")
        return img if img.mode in ("RGB", "L") else img.convert("RGB")
    # png
    if img.mode in ("CMYK", "YCbCr", "LAB", "HSV"):
        return img.convert("RGB")
    if img.mode == "P" and "transparency" not in img.info:
        return img.convert("RGB")
    if img.mode not in ("RGB", "RGBA", "L", "LA", "P"):
        return img.convert("RGBA") if _has_alpha(img) else img.convert("RGB")
    return img


def _save(img: Image.Image, fmt: str, quality: int | None, icc, exif) -> bytes:
    """把图像编码成字节流。quality=None 表示 PNG 无损路径。"""
    buf = io.BytesIO()
    kw: dict = {}
    if icc:
        kw["icc_profile"] = icc
    if exif:
        kw["exif"] = exif
    if fmt == "jpeg":
        img.save(buf, "JPEG", quality=int(quality or 90), optimize=True, progressive=True, **kw)
    elif fmt == "webp":
        # method 只影响编码耗时：实测大图上 method=6 比 method=0 慢 13 倍，体积仅小 5%
        pixels = img.width * img.height
        if pixels <= 800_000:
            method = 6
        elif pixels <= 1_500_000:
            method = 4
        elif pixels <= 8_000_000:
            method = 2
        else:
            method = 0
        img.save(buf, "WEBP", quality=int(quality or 90), method=method, **kw)
    else:
        # 大图上 optimize=True 会多跑几轮滤波试验，耗时成倍增长，按像素量取舍
        pixels = img.width * img.height
        img.save(buf, "PNG", optimize=pixels <= 2_500_000,
                 compress_level=9 if pixels <= 4_000_000 else 6, **kw)
    return buf.getvalue()


def _quantize(img: Image.Image, colors: int) -> Image.Image:
    """把图量化到指定色数（PNG 想瘦身的核心手段）。"""
    colors = max(2, min(256, int(colors)))
    if img.mode in ("RGBA", "LA"):
        try:
            return img.convert("RGBA").quantize(colors=colors, method=Image.FASTOCTREE)
        except Exception:
            return _flatten(img).quantize(colors=colors, method=Image.MEDIANCUT)
    if img.mode == "P":
        img = img.convert("RGBA") if "transparency" in img.info else img.convert("RGB")
        return _quantize(img, colors)
    src = img if img.mode == "RGB" else img.convert("RGB")
    return src.quantize(colors=colors, method=Image.MEDIANCUT, dither=Image.FLOYDSTEINBERG)


# ---------------------------------------------------------------- 核心拟合


def _fit_lossy(img: Image.Image, fmt: str, target: int, icc, exif):
    """在给定分辨率下，二分搜索「不超过目标体积」的最高质量。"""
    data = _save(img, fmt, 1, icc, exif)
    if len(data) > target:
        return None, None          # 这个分辨率下就算质量 1 也超标，需要缩图
    best_data, best_q = data, 1
    lo, hi = 2, 95
    while lo <= hi:
        mid = (lo + hi) // 2
        d = _save(img, fmt, mid, icc, exif)
        if len(d) <= target:
            best_data, best_q = d, mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best_data, best_q


def _fit_png(img: Image.Image, target: int, icc, exif):
    """
    PNG 的两条路径：
      1) 无损重压（optimize + compress_level）—— 画质零损失
      2) 调色板量化，从高色数往低色数走，命中即停（色数越多画质越好）
    """
    data = _save(img, "PNG", None, icc, exif)
    if len(data) <= target:
        return data, None, "无损"

    for colors in PNG_COLORS:
        d = _save(_quantize(img, colors), "PNG", None, icc, exif)
        if len(d) <= target:
            return d, colors, f"{colors} 色"
    return None, None, ""


def _exif_bytes(im: Image.Image, keep: bool):
    if not keep:
        return None
    try:
        e = im.getexif()
        if not e:
            return None
        e.pop(274, None)  # 方向已经通过 exif_transpose 烘焙进像素，去掉标签防止二次旋转
        return e.tobytes()
    except Exception:
        return None


def _compress_static(im: Image.Image, fmt: str, target: int, keep_exif: bool):
    """
    静态图的主循环：逐级缩放 × 二分质量，挑最优组合。
    返回 (data, quality, scale, note) 或 None。
    """
    icc = im.info.get("icc_profile")
    exif = _exif_bytes(im, keep_exif)
    cache: dict[float, Image.Image] = {}

    def at(scale: float) -> Image.Image:
        if scale not in cache:
            cache[scale] = _prepare(_resize(im, scale), fmt)
        return cache[scale]

    # ① 探测：用最低成本的一次编码估算体积，直接跳到最可能的缩放档，
    #    避免在明显压不进去的档位上反复做二分搜索。
    start = 0
    try:
        base = at(1.0)
        probe = len(_save(base, "PNG", None, icc, exif)) if fmt == "png" \
            else len(_save(base, fmt, 1, icc, exif))
        if probe > target > 0:
            # 体积大致与像素数成正比，所以缩放系数约等于面积比的平方根
            est = (target / probe) ** 0.5 * 1.03
            while start < len(SCALES) - 1 and SCALES[start][0] > est:
                start += 1
    except Exception:
        start = 0

    # ② 从这个档位开始逐个尝试
    best_fallback = None
    for scale, need_q in SCALES[start:]:
        work = at(scale)
        if fmt == "png":
            data, _colors, note = _fit_png(work, target, icc, exif)
            if data is None:
                continue
            return data, None, scale, note
        data, q = _fit_lossy(work, fmt, target, icc, exif)
        if data is None:
            continue
        if q >= need_q:
            return data, q, scale, f"q{q}"
        # 质量偏低时先记下，继续看缩图能不能换到更好的画质
        if best_fallback is None or q > best_fallback[1]:
            best_fallback = (data, q, scale, f"q{q}")
    return best_fallback


ANIM_Q_STEPS = (90, 75, 60, 45, 30, 20, 10, 5, 1)


def _compress_animated_webp(im: Image.Image, target: int, keep_exif: bool):
    """
    动图（多帧 GIF）转 WebP 动图，按画质阶梯从高到低试，命中即停。
    用阶梯而不是二分，是因为每帧都要编码，二分固定 7 次太贵。
    """
    frames, durations = [], []
    for frame in range(getattr(im, "n_frames", 1)):
        im.seek(frame)
        frames.append(im.convert("RGBA"))
        durations.append(im.info.get("duration", 100))
    if len(frames) < 2:
        return None

    loop = im.info.get("loop", 0)

    def encode(fs, q):
        buf = io.BytesIO()
        fs[0].save(
            buf, "WEBP", save_all=True, append_images=fs[1:],
            duration=durations[:len(fs)], loop=loop, quality=q, method=2,
        )
        return buf.getvalue()

    best = None
    for q in ANIM_Q_STEPS:
        try:
            d = encode(frames, q)
        except Exception:
            return None
        if len(d) <= target:
            return d, q
        best = d
    # 整帧都超标 → 缩一半再试一轮
    w, h = frames[0].size
    small = [f.resize((max(1, w // 2), max(1, h // 2)), Image.LANCZOS) for f in frames]
    for q in (70, 50, 30, 15, 5):
        try:
            d = encode(small, q)
        except Exception:
            return None
        if len(d) <= target:
            return d, q
    return None


# ---------------------------------------------------------------- 对外主函数


def _unique_path(dst: Path) -> Path:
    """同名文件已存在时追加 (2)(3)…，绝不覆盖已有文件。"""
    if not dst.exists():
        return dst
    stem, suf = dst.stem, dst.suffix
    for i in range(2, 1000):
        cand = dst.with_name(f"{stem}({i}){suf}")
        if not cand.exists():
            return cand
    return dst.with_name(f"{stem}({os.getpid()}){suf}")


def _dest_path(src: Path, new_ext: str, opts: Options) -> Path:
    if opts.replace:
        dst = src.with_suffix(new_ext)
        if dst != src and dst.exists():
            dst = src.with_name(f"{src.stem}_min{new_ext}")
        return dst
    out_dir = Path(opts.out_dir) if opts.out_dir else src.parent / "PicPress输出"
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"{src.stem}{opts.name_suffix}{new_ext}"
    if opts.name_suffix:
        # 右键快速压缩：同目录生成副本，撞名就编号，绝不动原图
        return _unique_path(dst)
    try:
        if dst.resolve() == src.resolve():
            dst = out_dir / f"{src.stem}_min{new_ext}"
    except OSError:
        pass
    return dst


def _write_atomic(dst: Path, data: bytes, st: os.stat_result) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".pptmp")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dst)
    try:
        os.utime(dst, (st.st_atime, st.st_mtime))
    except OSError:
        pass


def _backup(src: Path, src_size: int, backup_root: Path | None) -> tuple[Path | None, str]:
    if backup_root is None:
        return None, ""
    try:
        backup_root.mkdir(parents=True, exist_ok=True)
        target = backup_root / src.name
        n = 1
        while target.exists():
            target = backup_root / f"{src.stem}_{n}{src.suffix}"
            n += 1
        shutil.copy2(src, target)
        return target, ""
    except Exception as e:  # 备份失败就不允许替换原图，避免数据丢失
        return None, f"备份失败：{e}"


def compress_one(
    src: str | Path,
    opts: Options,
    backup_root: Path | None = None,
) -> Result:
    """压缩单个文件。线程安全：不共享可变状态。"""
    src = Path(src)
    try:
        st = src.stat()
    except OSError as e:
        return Result(False, src, src, 0, 0, None, 1.0, error=str(e), reached=False)
    src_size = st.st_size

    fmt = resolve_fmt(src.suffix, opts.fmt)
    new_ext = EXT_OF_FMT[fmt]
    src_ext = src.suffix.lower()
    fmt_changed = new_ext != src_ext and not (src_ext == ".jpeg" and new_ext == ".jpg")

    # GIF 在「保持原格式」下不超标就直接放过：转成 WebP 动图反而可能变大
    if src_ext == ".gif" and opts.fmt == "keep" and src_size <= opts.target_bytes:
        fmt_changed = False

    # ① 原图本来就在目标以内，且不需要转格式 —— 直接放过，零画质损失
    if src_size <= opts.target_bytes and not fmt_changed:
        dst = _dest_path(src, src.suffix.lower(), opts)
        note = "已达标，未重压"
        if dst != src:
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            except Exception as e:
                return Result(False, src, src, src_size, 0, None, 1.0, error=str(e), reached=False)
        return Result(True, src, dst, src_size, src_size, None, 1.0,
                      note=note, reached=True, skipped=True)

    # ② 真正开始压缩
    try:
        with Image.open(src) as im:
            im.load()
            animated = getattr(im, "n_frames", 1) > 1 and im.format == "GIF"
            payload = None
            quality = None
            scale = 1.0
            tag = ""

            if animated and opts.fmt in ("keep", "webp"):
                res = _compress_animated_webp(im, opts.target_bytes, opts.keep_exif)
                if res:
                    payload, quality = res
                    fmt, new_ext = "webp", ".webp"
                    fmt_changed = True
                    tag = "动图·WebP"
                else:
                    im.seek(0)
                    out = _compress_static(im.convert("RGBA"), fmt, opts.target_bytes, opts.keep_exif)
                    if out:
                        payload, quality, scale, tag = out
                        tag += "·仅首帧，动画已丢失"
            elif animated:
                im.seek(0)
                out = _compress_static(ImageOps.exif_transpose(im.convert("RGBA")), fmt,
                                       opts.target_bytes, opts.keep_exif)
                if out:
                    payload, quality, scale, tag = out
                    tag += "·仅首帧，动画已丢失"
            else:
                work = ImageOps.exif_transpose(im)
                out = _compress_static(work, fmt, opts.target_bytes, opts.keep_exif)
                if out:
                    payload, quality, scale, tag = out
    except Exception as e:
        return Result(False, src, src, src_size, 0, None, 1.0, error=f"{type(e).__name__}: {e}", reached=False)

    if not payload:
        return Result(False, src, src, src_size, 0, None, 1.0,
                      error="无法压缩到目标体积", reached=False)

    reached = len(payload) <= opts.target_bytes
    notes = [tag]
    if scale < 0.999:
        notes.append(f"缩至 {int(round(scale * 100))}%")
    if fmt_changed:
        notes.append(f"转 {fmt.upper()}")

    # ③ 落盘
    dst = _dest_path(src, new_ext, opts)
    try:
        _write_atomic(dst, payload, st)
    except Exception as e:
        return Result(False, src, src, src_size, 0, None, scale, error=f"写入失败：{e}", reached=False)

    # ④ 替换原图：先备份，再删掉旧文件（扩展名变了才会有旧文件残留）
    if opts.replace:
        if dst != src:
            if opts.backup:
                bak, err = _backup(src, src_size, backup_root)
                if bak is None:
                    try:
                        dst.unlink(missing_ok=True)
                    except OSError:
                        pass
                    return Result(False, src, src, src_size, 0, None, scale,
                                  error=err or "备份失败，已取消覆盖", reached=False)
            try:
                src.unlink(missing_ok=True)
            except OSError as e:
                return Result(False, src, dst, src_size, len(payload), quality, scale,
                              error=f"旧文件删除失败：{e}", reached=reached)
        elif opts.backup:
            _backup(src, src_size, backup_root)

    return Result(True, src, dst, src_size, len(payload), quality, scale,
                  note=" · ".join(notes), reached=reached)


def make_backup_root(base: str | Path | None = None) -> Path:
    """建立本次批次的备份目录。"""
    root = Path(base) if base else Path.home() / "PicPress备份"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    p = root / stamp
    p.mkdir(parents=True, exist_ok=True)
    return p


def collect_images(paths, recursive: bool = True) -> list[Path]:
    """把「文件 + 文件夹」混合列表展开成受支持的图片文件列表（去重保序）。"""
    out: list[Path] = []
    seen = set()
    for p in paths:
        p = Path(p)
        if p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            for f in it:
                if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS:
                    rp = str(f.resolve()).lower()
                    if rp not in seen:
                        seen.add(rp)
                        out.append(f)
        elif p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
            rp = str(p.resolve()).lower()
            if rp not in seen:
                seen.add(rp)
                out.append(p)
    return out

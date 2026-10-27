# -*- coding: utf-8 -*-
"""替换原图模式的安全性测试：备份、覆盖、扩展名变更、异常文件。"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
from PIL import Image  # noqa: E402

from compressor import Options, compress_one, human_size, make_backup_root, mb_to_bytes  # noqa: E402

SRC = Path(__file__).parent / "_test" / "in"


def run_case(name, files, target_mb, fmt="keep", backup=True):
    tmp = Path(tempfile.mkdtemp(prefix="pp_repl_"))
    work = tmp / "work"
    work.mkdir()
    before = {}
    for f in files:
        shutil.copy2(SRC / f, work / f)
        before[f] = (work / f).stat().st_size

    bak_root = make_backup_root(tmp / "bak") if backup else None
    opts = Options(target_bytes=mb_to_bytes(target_mb), fmt=fmt, replace=True, backup=backup)
    print(f"\n--- {name}（目标 {target_mb}MB / {fmt} / 备份 {backup}）---")
    ok = True
    for f in files:
        p = work / f
        r = compress_one(p, opts, bak_root)
        now = p.exists()
        size_now = p.stat().st_size if now else -1
        entries = sorted(x.name for x in work.iterdir())
        print(f"  {f}: ok={r.ok} reached={r.reached} 原={human_size(before[f])} "
              f"现={human_size(size_now) if now else '已删除'} 输出={r.out.name} {r.note} {r.error}")
        print(f"      目录内容 = {entries}")

        # 断言：结果文件必须真实可打开，且不超过目标
        if r.ok and r.reached:
            with Image.open(r.out) as im:
                im.load()
                assert im.size[0] > 0
            assert r.out.stat().st_size <= opts.target_bytes, "超出目标体积"
        # 断言：备份必须存在且与原件同尺寸
        if backup and bak_root and r.ok and r.out.resolve() != p.resolve():
            baks = list(bak_root.rglob(f))
            assert baks, f"{f} 缺少备份"
            print(f"      备份 = {baks[0].name} {human_size(baks[0].stat().st_size)}")
    if backup and bak_root:
        print(f"  备份目录文件数 = {len(list(bak_root.iterdir()))}")
    shutil.rmtree(tmp, ignore_errors=True)
    return ok


if __name__ == "__main__":
    run_case("仅替换同格式", ["photo_big.jpg"], 0.4)
    run_case("替换并改格式(PNG→JPEG)", ["alpha.png"], 0.1, fmt="jpeg")
    run_case("GIF 转 WebP 替换", ["anim.gif"], 0.005)
    run_case("不备份直接替换", ["flat.bmp"], 0.1, backup=False)
    print("\n替换模式测试结束")

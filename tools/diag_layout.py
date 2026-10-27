# -*- coding: utf-8 -*-
"""运行时布局诊断：打印关键控件所需高度与实际分配高度。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
import app as A  # noqa: E402

a = A.App()
a.update_idletasks()
a.update()

print(f"window {a.winfo_width()}x{a.winfo_height()}")
b = a._right_body
print(f"right body: req={b.winfo_reqheight()} actual={b.winfo_height()}")

total = 0
for s in sorted(b.grid_slaves(), key=lambda w: int(w.grid_info()["row"])):
    gi = s.grid_info()
    rh, ah = s.winfo_reqheight(), s.winfo_height()
    total += rh
    flag = "  << 被压缩" if ah < rh - 1 else ""
    print(f"  row{int(gi['row']):2d} {type(s).__name__:12s} req={rh:4d} act={ah:4d} "
          f"pady={gi.get('pady')}{flag}")
print(f"内容总需求约 {total}px，body 实际 {b.winfo_height()}px")

for name in ("card_folder", "card_replace", "seg", "chk_backup", "chk_exif"):
    w = getattr(a, name)
    print(f"{name}: h={w.winfo_height()} req={w.winfo_reqheight()} _h={getattr(w, '_h', None)}")

print(f"left tree h={a.tree.winfo_height()} bar h={a.bar.winfo_height()}")
a.destroy()

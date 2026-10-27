# -*- coding: utf-8 -*-
"""演示运行：自动载入测试图片并开跑，用于截图验收真实交互状态。"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))
import app as A  # noqa: E402

a = A.App()
src = ROOT / "_test" / "in"
files = sorted(str(p) for p in src.iterdir()) if src.exists() else []
a.after(500, lambda: a._ingest(files))
a.after(1200, a._start)
a.mainloop()

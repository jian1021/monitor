"""LP 区间宽度页面：已注册到侧边栏，并复用 lprange 包（不自己重复实现统计）。"""

import ast
import pathlib

from app.infrastructure.db.navigation import NAVIGATION_GROUPS

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_lp_range_page_registered():
    assert "lp_range" in NAVIGATION_GROUPS["策略与数据"]
    index_src = (ROOT / "index.py").read_text(encoding="utf-8")
    assert '"pages/lp_range.py"' in index_src and '"lp_range": lp_range' in index_src
    settings_src = (ROOT / "pages" / "monitor_settings.py").read_text(encoding="utf-8")
    assert '"lp_range"' in settings_src


def test_lp_range_page_reuses_package():
    tree = ast.parse((ROOT / "pages" / "lp_range.py").read_text(encoding="utf-8"))
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert {"lprange.cli", "lprange.stats", "lprange.watchlist"} <= mods

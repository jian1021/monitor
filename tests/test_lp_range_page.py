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


def test_lp_range_page_offers_robinhood():
    src = (ROOT / "pages" / "lp_range.py").read_text(encoding="utf-8")
    assert '"robinhood": "Robinhood Chain"' in src


def test_lp_range_page_defaults_one_week_and_mult_one():
    src = (ROOT / "pages" / "lp_range.py").read_text(encoding="utf-8")
    assert "DEFAULT_HOLDS = [168]" in src and "DEFAULT_HISTORY_MULT = 1.0" in src
    assert "一键分析全部" in src and "分析选中" in src


def test_lp_range_page_reloads_stale_modules_before_import():
    """Streamlit 只重跑页面脚本，lprange.* 会留在 sys.modules；推送部署后
    新页面引用旧模块没有的函数会 ImportError（Cloud 上实际发生过）。
    守卫必须在导入 lprange.cli 之前执行。"""
    src = (ROOT / "pages" / "lp_range.py").read_text(encoding="utf-8")
    guard = "\n_reload_lprange_if_changed()\n"
    assert guard in src
    assert "importlib.reload" in src
    assert src.index(guard) < src.index("from lprange.cli import")

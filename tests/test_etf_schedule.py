"""ETF 周线 RSI 独立模块的守卫测试。

背景：ETF 原先挂在 "rsi" 模块（Meteora 池 / 可转债 / ETF 共用），
间隔 24 小时。周线 RSI 一周才收一根 K 线，日更属于无效取数，且会与
可转债的日更节奏互相牵制。现拆成独立的 "etf_rsi" 模块，默认 7 天一次。
"""

import ast
from pathlib import Path

from app.infrastructure.db.intervals import DEFAULT_INTERVALS


MAIN_PY = Path(__file__).resolve().parents[1] / "main.py"


def _function_source(name: str) -> ast.FunctionDef:
    tree = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"main.py 里找不到函数 {name}")


def test_etf_module_runs_weekly():
    assert DEFAULT_INTERVALS["etf_rsi"] == 7 * 24 * 60
    # rsi 模块回到纯「Meteora 池 / 可转债」的日更职责
    assert DEFAULT_INTERVALS["rsi"] == 24 * 60


def test_etf_rsi_module_registered():
    src = MAIN_PY.read_text(encoding="utf-8")
    assert 'MonitorSpec("etf_rsi", "etf_rsi", _run_etf_rsi)' in src
    assert "def run_etf_monitor(" in src


def test_rsi_monitor_no_longer_scans_etf():
    """run_rsi_monitor 内不得再出现 etfs，否则 ETF 会被两个模块重复扫。"""
    node = _function_source("run_rsi_monitor")
    segment = ast.get_source_segment(MAIN_PY.read_text(encoding="utf-8"), node) or ""
    assert "etfs" not in segment
    assert "convertible_bonds" in segment


def test_etf_monitor_scans_etf_only():
    node = _function_source("run_etf_monitor")
    segment = ast.get_source_segment(MAIN_PY.read_text(encoding="utf-8"), node) or ""
    assert 'config.get("etfs", [])' in segment
    assert "convertible_bonds" not in segment


def test_module_settings_default_allows_etf_rsi(monkeypatch):
    """模块启停表缺省应视为启用，否则新模块上线后静默不跑。"""
    from app.infrastructure.db import module_settings

    monkeypatch.setattr(module_settings, "get_db_client", lambda: None)
    monkeypatch.setattr(module_settings, "ensure_module_settings", lambda: True)
    assert module_settings.get_module_settings()["etf_rsi"] is True

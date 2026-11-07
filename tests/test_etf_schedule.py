"""ETF 周线 RSI 独立模块的守卫测试。

背景：ETF 原先挂在 "rsi" 模块（Meteora 池 / 可转债 / ETF 共用），
间隔 24 小时。周线 RSI 一周才收一根 K 线，日更属于无效取数，且会与
可转债的日更节奏互相牵制。现拆成独立的 "etf_rsi" 模块，默认 7 天一次。

结构上用例已下沉到 app/application/monitors/runners.py（main.py 只做装配），
因此这里直接对用例函数的源码做断言，避免重构后守卫失效却没人发现。
"""

import inspect
from pathlib import Path

from app.application.monitors import runners
from app.infrastructure.db.intervals import DEFAULT_INTERVALS


MAIN_PY = Path(__file__).resolve().parents[1] / "main.py"


def test_etf_module_runs_weekly():
    assert DEFAULT_INTERVALS["etf_rsi"] == 7 * 24 * 60
    # rsi 模块回到纯「Meteora 池 / 可转债」的日更职责
    assert DEFAULT_INTERVALS["rsi"] == 24 * 60


def test_etf_rsi_module_registered():
    source = MAIN_PY.read_text(encoding="utf-8")
    assert '"etf_rsi", "etf_rsi"' in source
    assert "runners.run_etf_monitor" in source


def test_rsi_monitor_no_longer_scans_etf():
    """run_rsi_monitor 内不得再出现 etfs，否则 ETF 会被两个模块重复扫。"""
    body = inspect.getsource(runners.run_rsi_monitor)
    assert "etfs" not in body
    assert "convertible_bonds" in body


def test_etf_monitor_scans_etf_only():
    body = inspect.getsource(runners.run_etf_monitor)
    assert 'config.get("etfs", [])' in body
    assert "convertible_bonds" not in body


def test_etf_monitor_uses_weekly_settings():
    """ETF 必须用周线配置，别被可转债/加密的日线或小时线设置串了。"""
    body = inspect.getsource(runners.run_etf_monitor)
    assert 'DEFAULT_SETTINGS["etf"]' in body


def test_module_settings_default_allows_etf_rsi(monkeypatch):
    """模块启停表缺省应视为启用，否则新模块上线后静默不跑。"""
    from app.infrastructure.db import module_settings

    monkeypatch.setattr(module_settings, "get_db_client", lambda: None)
    monkeypatch.setattr(module_settings, "ensure_module_settings", lambda: True)
    assert module_settings.get_module_settings()["etf_rsi"] is True

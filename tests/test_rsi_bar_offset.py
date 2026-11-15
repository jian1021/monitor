"""周线取数必须用最新一根，否则「反转突破」会整体滞后一周。

背景：RSI_BAR_OFFSET 原本是全局常量（=2，取倒数第二根），本意是避开
日内尚未收线的那根 K 线。但 A 股周线在周五收盘后即定型，再回退一根
等于拿上一周的数据做判定——本周 RSI 已经从超卖区上穿，代码却认为
"还在 10 以下"，突破要等到下一次扫描才报出来，表现为「第二周才报警」。
"""

import pandas as pd

from app.application.monitors import rsi as monitor_rsi


# ================= _bar_offset：按周期决定取第几根 =================
def test_weekly_takes_latest_bar():
    """周线取最新一根。"""
    assert monitor_rsi._bar_offset("w") == 1


def test_weekly_aliases_all_map_to_latest():
    """baostock 用小写 w，OKX 用 1W，都要识别成周线。"""
    for key in ("w", "W", "1w", "1W", "week", " week "):
        assert monitor_rsi._bar_offset(key) == 1, key


def test_intraday_keeps_previous_bar():
    """日线/小时线最新一根可能未收线，继续取倒数第二根。"""
    for key in ("d", "D", "1d", "1h", "4h", "hour", "", None):
        assert monitor_rsi._bar_offset(key) == monitor_rsi.RSI_BAR_OFFSET, key


# ================= _calc_rsi：offset 行为 =================
def test_calc_rsi_offset_selects_expected_bar():
    """offset=1 返回最后一根，offset=2 返回倒数第二根。"""
    close = pd.Series([10, 11, 12, 11, 10, 9, 8, 9, 10, 11], dtype=float)
    _r1, p1 = monitor_rsi._calc_rsi(close, 3, offset=1)
    _r2, p2 = monitor_rsi._calc_rsi(close, 3, offset=2)
    assert p1 == 11.0
    assert p2 == 10.0


# ================= 端到端：周线 / 日线分别落到哪一根 =================
class _FakeRS:
    error_code = "0"
    error_msg = ""

    def __init__(self, frame):
        self._frame = frame

    def get_data(self):
        return self._frame


def _patch_baostock(monkeypatch):
    """最后两根价格故意拉开，便于区分取到哪一根。"""
    frame = pd.DataFrame({
        "date": ["2026-10-02", "2026-10-09", "2026-10-16",
                 "2026-10-23", "2026-10-30", "2026-11-06", "2026-11-13"],
        "close": ["0.90", "0.95", "0.92", "0.98", "1.00", "1.00", "2.00"],
    })
    monkeypatch.setattr(monitor_rsi.bs, "query_history_k_data_plus",
                        lambda *a, **kw: _FakeRS(frame))
    monkeypatch.setattr(monitor_rsi, "_to_bs_code", lambda c: f"sh.{c}")


def test_weekly_returns_latest_close(monkeypatch):
    """周线必须返回 11-13 那根（2.00），而不是 11-06（1.00）。"""
    _patch_baostock(monkeypatch)
    _rsi, price = monitor_rsi.get_a_share_rsi("515880", True, length=3, frequency="w")
    assert price == 2.00


def test_daily_still_returns_previous_close(monkeypatch):
    """日线保持原行为，取倒数第二根（1.00）。"""
    _patch_baostock(monkeypatch)
    _rsi, price = monitor_rsi.get_a_share_rsi("515880", True, length=3, frequency="d")
    assert price == 1.00


# ================= 回调语义：只在第一次上穿时报 =================
def test_rebound_fires_on_first_crossing(monkeypatch):
    """上一轮 <10、本轮回到 10 以上 → 报，且只报一次。"""
    from app.application.monitors import rsi_alerts

    monkeypatch.setattr(rsi_alerts, "update_asset_alert_state",
                        lambda *a, **kw: True)
    settings = {"rsi_low": 10, "rsi_high": 90, "alert_on_rebound": True}

    item = {"id": 1, "last_rsi": 2.99}
    assert rsi_alerts.should_push_rebound(item, 15.80, 0.418, settings) is True
    # 同一段回升的下一轮（前值已变成 15.80）不再报
    item2 = {"id": 1, "last_rsi": 15.80}
    assert rsi_alerts.should_push_rebound(item2, 20.0, 0.42, settings) is False

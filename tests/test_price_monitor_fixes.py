"""价格监控逻辑修复回归测试。"""
from datetime import datetime, timedelta, timezone

from app.application.monitors import price

BJ = timezone(timedelta(hours=8))


def _rule(**kw):
    base = {"id": 1, "address": "0x" + "a" * 40, "chain": "base", "asset_type": "token",
            "symbol": "T", "target_price": 100.0, "direction": "gte", "alerted": False,
            "last_checked_at": None, "daily_check_at": None}
    base.update(kw)
    return base


def _setup(monkeypatch, rule, px, send_ok=True):
    calls = {"price": [], "mark": [], "clear": [], "send": []}
    monkeypatch.setattr(price, "load_enabled_rules", lambda: [rule])
    monkeypatch.setattr(price, "fetch_price", lambda *a, **k: (px, "T"))
    monkeypatch.setattr(price, "update_rule_symbol", lambda *a: None)
    monkeypatch.setattr(price, "update_rule_price",
                        lambda rid, p, touch_checked=True: calls["price"].append(touch_checked))
    monkeypatch.setattr(price, "mark_alerted", lambda rid: calls["mark"].append(rid))
    monkeypatch.setattr(price, "clear_alerted", lambda rid: calls["clear"].append(rid))
    monkeypatch.setattr(price, "resolve_webhook", lambda k: "hook")
    monkeypatch.setattr(price, "send_feishu_msg",
                        lambda h, m: calls["send"].append(m) or send_ok)
    monkeypatch.setattr(price, "ashare_market_open_day", lambda now=None: True)
    return calls


def test_send_failure_does_not_consume_daily_check(monkeypatch):
    calls = _setup(monkeypatch, _rule(), 101.0, send_ok=False)
    price.run_once()
    assert calls["mark"] == []
    assert True not in calls["price"]          # last_checked_at 未推进 → 下一轮重试


def test_success_marks_and_touches(monkeypatch):
    calls = _setup(monkeypatch, _rule(), 101.0)
    price.run_once()
    assert calls["mark"] == [1] and calls["price"][-1] is True


def test_hysteresis_prevents_flapping(monkeypatch):
    calls = _setup(monkeypatch, _rule(alerted=True), 99.8)   # 仅回撤 0.2%
    price.run_once()
    assert calls["clear"] == []


def test_rearm_after_leaving_band(monkeypatch):
    calls = _setup(monkeypatch, _rule(alerted=True), 99.0)   # 回撤 1% > 0.5%
    price.run_once()
    assert calls["clear"] == [1]


def test_lte_hysteresis():
    assert price.should_rearm("lte", 100.4, 100.0) is False
    assert price.should_rearm("lte", 100.6, 100.0) is True


def test_unknown_direction_never_triggers():
    assert price.is_triggered("eq", 100.0, 100.0) is False


def test_ashare_skipped_on_closed_day(monkeypatch):
    calls = _setup(monkeypatch, _rule(asset_type="ashare", address="159967"), 101.0)
    monkeypatch.setattr(price, "ashare_market_open_day", lambda now=None: False)
    price.run_once()
    assert calls["price"] == [] and calls["send"] == []


def test_weekend_is_closed():
    assert price.ashare_market_open_day(datetime(2026, 11, 28, 10, 0, tzinfo=BJ)) is False


def test_manual_check_page_does_not_touch_checked():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "pages/st_monitor_price.py").read_text(encoding="utf-8")
    assert "touch_checked=False" in src

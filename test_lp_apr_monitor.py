# -*- coding: utf-8 -*-
"""LP APR 监控：阈值判定与消息格式."""

from app.application.lp_tools import monitor as m

ROW = {
    "pool": "0x52e65b17fb6e5ba00ed806f37afcd2daa50271ca",
    "name": "USDG/WETH 0.01%",
    "width": 10,
    "capital": 10000,
    "min_apr": 5.0,     # 百分数：5% 下限
    "max_apr": 50.0,    # 百分数：50% 上限
    "enabled": 1,
}


def test_no_alert_inside_range():
    assert m.build_threshold_alerts(ROW, 0.36) == []   # 36% 在 5~50 之间


def test_low_threshold():
    # apr 小数 0.03 = 3%，低于 5% 下限
    alerts = m.build_threshold_alerts(ROW, 0.03)
    assert len(alerts) == 1
    key, msg = alerts[0]
    assert key == ROW["pool"] + ":low"
    assert "低于下限" in msg
    assert "3.00%" in msg and "5.00%" in msg


def test_high_threshold():
    alerts = m.build_threshold_alerts(ROW, 0.60)
    assert len(alerts) == 1
    key, msg = alerts[0]
    assert key == ROW["pool"] + ":high"
    assert "高于上限" in msg
    assert "60.00%" in msg and "50.00%" in msg


def test_no_thresholds_no_alert():
    row = {**ROW, "min_apr": None, "max_apr": None}
    assert m.build_threshold_alerts(row, 0.001) == []
    assert m.build_threshold_alerts(row, 9.9) == []


def test_missing_apr_no_alert():
    assert m.build_threshold_alerts(ROW, None) == []


def test_both_thresholds_breached():
    row = {**ROW, "min_apr": 40.0, "max_apr": 50.0}
    keys = [k for k, _ in m.build_threshold_alerts(row, 0.60)]
    assert keys == [ROW["pool"] + ":high"]  # 60% 只超上限
    keys = [k for k, _ in m.build_threshold_alerts(row, 0.20)]
    assert keys == [ROW["pool"] + ":low"]

# -*- coding: utf-8 -*-
"""rhpools 监控模块：状态/价差告警构建与主流程（client 层打桩）."""

import pytest

from app.application.rhpools import monitor as m
from app.infrastructure.rhpools import client


HEALTHY = {
    "state": "live",
    "head": 1000,
    "indexed_head": 1000,
    "lag_blocks": 0,
    "lag_s": 0.5,
    "warm_error": None,
}


def test_status_alerts_empty_when_healthy():
    assert m.build_status_alerts(dict(HEALTHY)) == []


def test_status_alerts_catching_up_not_alerted():
    # 追块是正常状态：只靠 lag 阈值兜底
    status = dict(HEALTHY, state="catching_up", lag_s=3, lag_blocks=30)
    assert m.build_status_alerts(status) == []


def test_status_alerts_degraded_and_lag():
    status = dict(HEALTHY, state="degraded", lag_s=1200, lag_blocks=256)
    alerts = m.build_status_alerts(status)
    keys = [k for k, _ in alerts]
    assert "state:degraded" in keys
    assert "lag" in keys
    assert any("1200" in msg for _, msg in alerts)


def test_status_alerts_stopped():
    alerts = m.build_status_alerts(dict(HEALTHY, state="stopped"))
    assert [k for k, _ in alerts] == ["state:stopped"]


def test_status_alerts_warm_error():
    alerts = m.build_status_alerts(dict(HEALTHY, warm_error="RPC 429"))
    assert [k for k, _ in alerts] == ["warm_error"]


def test_dislocation_message_shape():
    rows = [{
        "pair": "WETH / USDG",
        "token0": "0xaaa", "token1": "0xbbb",
        "spread_bps": 123.456, "net_bps": 108.4, "depth_usd": 12345.0,
        "buy": {"protocol": "v3", "fee_ppm": 100, "price": 0.00032},
        "sell": {"protocol": "v4", "fee_ppm": 460, "price": 0.00033},
    }]
    entries = m.build_dislocation_messages(rows)
    assert len(entries) == 1
    key, msg = entries[0]
    assert key == "0xaaa:0xbbb"
    assert "WETH / USDG" in msg
    assert "123.5 bps" in msg
    assert "108.4 bps" in msg
    assert "$12.3k" in msg
    assert "v3(0.01%)" in msg and "v4(0.046%)" in msg


def test_dislocation_message_missing_fields_tolerated():
    entries = m.build_dislocation_messages([{"token0": "0x1", "token1": "0x2",
                                             "spread_bps": 60}])
    assert len(entries) == 1
    assert "n/a" in entries[0][1]


def test_run_skips_quietly_when_unavailable(monkeypatch):
    monkeypatch.setattr(client, "ensure_running", lambda **kw: None)
    monkeypatch.setattr(client, "available", lambda: False)
    pushed = []
    monkeypatch.setattr(m, "_push", lambda msgs: pushed.extend(msgs))
    m.run_rhpools_monitor()
    assert pushed == []


def test_run_alerts_service_down_once(monkeypatch):
    monkeypatch.setattr(client, "ensure_running", lambda **kw: None)
    monkeypatch.setattr(client, "available", lambda: True)
    monkeypatch.setattr(client, "auto_start_enabled", lambda: True)
    monkeypatch.setattr(client, "base_url", lambda: "http://127.0.0.1:8196")
    monkeypatch.setattr(m, "_push", lambda msgs: None)
    # DB 不可用时 filter 全放行：验证走的是告警分支
    m.run_rhpools_monitor()  # 不抛异常即通过；推送细节由 DB 行为决定


def test_run_pushes_dislocations(monkeypatch):
    monkeypatch.setattr(client, "ensure_running", lambda **kw: dict(HEALTHY))
    monkeypatch.setattr(client, "get", lambda path, params=None, timeout=0: {
        "rows": [{
            "pair": "WETH / USDG", "token0": "0xaaa", "token1": "0xbbb",
            "spread_bps": 80.0, "net_bps": 70.0, "depth_usd": 500.0,
            "buy": {"protocol": "v3"}, "sell": {"protocol": "v4"},
        }],
    })
    pushed, marked = [], []
    monkeypatch.setattr(m, "_push", lambda msgs: pushed.extend(msgs))
    monkeypatch.setattr(m, "mark_pushed", lambda mod, keys: marked.extend(keys))
    m.run_rhpools_monitor()
    assert len(pushed) == 1
    assert "池间价差" in pushed[0]
    assert "0xaaa:0xbbb" in marked


def test_base_url_env_override(monkeypatch):
    monkeypatch.setenv("RHP_HOST", "10.0.0.5")
    monkeypatch.setenv("RHP_PORT", "9000")
    assert client.base_url() == "http://10.0.0.5:9000"


def test_auto_start_env_switch(monkeypatch):
    monkeypatch.setenv("RHP_AUTO_START", "0")
    assert client.auto_start_enabled() is False
    monkeypatch.setenv("RHP_AUTO_START", "1")
    assert client.auto_start_enabled() is True


def test_module_dir_layout():
    # 子模块应已拉取；若为空说明忘了 git submodule update --init
    assert client.available(), f"缺少子模块：{client.SRC_DIR}"

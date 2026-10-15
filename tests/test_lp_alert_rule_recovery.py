# -*- coding: utf-8 -*-
"""回归：LP 告警规则上一次取数失败（status=error）后仍必须继续被监控.

历史 bug：load_rules(enabled_only=True) 只取 status='open'，而 run_once 在
取数失败时会把 status 写成 'error'，于是任何一次网络抖动都会让该规则永远
不再被选出来，规则从此静默停止告警（页面上只显示「🔴 取数失败」）。
"""

from unittest.mock import patch

from app.infrastructure.db import lp_alert


def test_load_rules_retries_rules_whose_last_check_failed():
    captured = {}

    def fake_execute(sql, params=None, fetch=False):
        captured["sql"] = sql
        captured["params"] = list(params or [])
        captured["fetch"] = fetch
        return []

    with patch.object(lp_alert, "_execute", side_effect=fake_execute):
        assert lp_alert.load_rules(enabled_only=True) == []

    assert "enabled = 1" in captured["sql"]
    assert "status IN (?, ?)" in captured["sql"]
    assert set(captured["params"]) == {"open", "error"}
    assert captured["fetch"] is True


def test_load_rules_without_enabled_filter_keeps_no_status_gate():
    captured = {}

    def fake_execute(sql, params=None, fetch=False):
        captured["sql"] = sql
        return []

    with patch.object(lp_alert, "_execute", side_effect=fake_execute):
        lp_alert.load_rules(enabled_only=False)

    assert "WHERE" not in captured["sql"]

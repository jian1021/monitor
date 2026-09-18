"""主循环睡眠计算（compute_sleep_seconds）测试."""
from main import compute_sleep_seconds, POLL_INTERVAL

SIX = {"rsi": 86400, "crypto": 1800, "onchain_token": 1800,
       "meteora_pump": 300, "robinhood_pump": 300, "lp_alert": 300}


def test_disabled_module_does_not_pin_sleep_to_1s():
    """回归：停用模块的 last_run 停在 0，旧逻辑会把 0+interval 算进 min → 1 秒空转。"""
    now = 1_770_000_000.0
    last_run = {k: now - 60 for k in SIX}
    last_run["rsi"] = 0.0        # 停用 → 从未执行
    settings = {k: True for k in SIX}
    settings["rsi"] = False
    assert compute_sleep_seconds(SIX, settings, last_run, now) == POLL_INTERVAL


def test_sleep_caps_at_poll_interval():
    now = 1_770_000_000.0
    last_run = {k: now - 10 for k in SIX}
    settings = {k: True for k in SIX}
    assert compute_sleep_seconds(SIX, settings, last_run, now) == POLL_INTERVAL


def test_sleep_respects_soonest_due_module():
    now = 1_770_000_000.0
    last_run = {k: now - 50 for k in SIX}
    last_run["lp_alert"] = now - 280   # 20 秒后到期，应胜出
    settings = {k: True for k in SIX}
    s = compute_sleep_seconds(SIX, settings, last_run, now)
    assert 19.5 < s <= 20.0


def test_all_disabled_returns_poll_interval():
    now = 1_770_000_000.0
    last_run = {k: 0.0 for k in SIX}
    settings = {k: False for k in SIX}
    assert compute_sleep_seconds(SIX, settings, last_run, now) == POLL_INTERVAL
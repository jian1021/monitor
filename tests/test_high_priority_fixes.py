"""高优先级逻辑修复的回归测试。"""
from unittest.mock import patch

from app.application.lp_alert import service
from app.domain.lp_alert.evaluator import evaluate
from app.domain.lp_alert.models import STATUS_CLOSED, STATUS_ERROR, STATUS_OPEN

RULE = {"id": 42, "pool_address": "P", "wallet": "W", "position_address": "POS"}


def test_missing_position_needs_consecutive_confirmations():
    service._dlmm_miss_counts.clear()
    with patch.object(service, "fetch_meteora_positions", return_value=[]):
        for _ in range(service.CLOSE_CONFIRM_MISSES - 1):
            assert service._cur_dlmm(RULE) == (None, STATUS_ERROR)
        assert service._cur_dlmm(RULE) == (None, STATUS_CLOSED)


def test_reappearing_position_resets_miss_counter():
    service._dlmm_miss_counts.clear()
    pos = {"position_address": "POS", "is_closed": False}
    with patch.object(service, "fetch_meteora_positions", return_value=[]):
        service._cur_dlmm(RULE)
    with patch.object(service, "fetch_meteora_positions", return_value=[pos]):
        assert service._cur_dlmm(RULE) == (pos, STATUS_OPEN)
    assert 42 not in service._dlmm_miss_counts


def test_explicit_closed_flag_is_immediate():
    service._dlmm_miss_counts.clear()
    pos = {"position_address": "POS", "is_closed": True}
    with patch.object(service, "fetch_meteora_positions", return_value=[pos]):
        assert service._cur_dlmm(RULE) == (None, STATUS_CLOSED)


def test_api_failure_is_error_not_closed():
    with patch.object(service, "fetch_meteora_positions", return_value=None):
        assert service._cur_dlmm(RULE) == (None, STATUS_ERROR)


def test_dlmm_price_mode_uses_active_price():
    rule = {"kind": "dlmm", "enable_target_alert": 1, "target_pct": 10,
            "target_mode": "price_pct", "entry_price": 100.0}
    assert evaluate(rule, {"price": None, "active_price": 110.0})["target"] is True
    assert evaluate(rule, {"price": None, "active_price": 109.0})["target"] is False

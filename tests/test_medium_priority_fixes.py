"""中优先级修复：退出策略 RSI 周期/确认、单位校验、跌穿比较统一。"""
from app.application.exit_strategy.monitor import _evaluate
from app.domain.lp_alert.evaluator import evaluate


def _c(closes):
    return [{"close": v} for v in closes]


def test_single_spike_does_not_fire_with_confirmation():
    closes = [1.0] * 30 + [0.99, 1.2]            # 仅最后一根暴涨
    _, hits = _evaluate(_c(closes), None, 0, 90, rsi_period=6, confirm_bars=2)
    assert hits["rsi_high"] is False


def test_sustained_rally_fires():
    closes = [1.0] * 20 + [1.0 + 0.05 * i for i in range(1, 12)]
    _, hits = _evaluate(_c(closes), None, 0, 90, rsi_period=6, confirm_bars=2)
    assert hits["rsi_high"] is True


def test_floor_unit_mismatch_suppresses_floor_and_flags():
    closes = [0.0001] * 25                       # 价格 ~1e-4，下限误填为 USD 0.5
    values, hits = _evaluate(_c(closes), None, 0.5, 90)
    assert hits["pool_floor"] is False
    assert hits["units_mismatch"] is True and values["units_ok"] is False


def test_floor_fires_when_units_consistent():
    closes = [1.0] * 25
    _, hits = _evaluate(_c(closes), 0.9, 0.95, 90)
    assert hits["pool_floor"] is True and hits["units_mismatch"] is False


def test_evm_v4_floor_inclusive():
    rule = {"kind": "evm_v4", "enable_floor_alert": 1, "floor_price": 1.0}
    assert evaluate(rule, {"active_price": 1.0})["floor"] is True

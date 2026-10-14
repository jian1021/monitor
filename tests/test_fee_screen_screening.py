# -*- coding: utf-8 -*-
"""fee-screen 筛选逻辑单测（纯函数，无网络）。

重点验证需求书的关键口径：
- 缺失字段一律 None（数据不可用），不冒充 0；
- 实际平均手续费率 = 手续费/成交量，而非基础费率；
- Active TVL 缺失时绝不用总 TVL 冒充；
- 净收益不重复扣除已含在资产价值变化中的损失。
"""

from app.domain.fee_screen.models import (
    FeeEstimate,
    PoolSnapshot,
    RiskLevel,
    ScreenConfig,
    TokenInfo,
    TrendAnalysis,
)
from app.domain.fee_screen.screening import (
    actual_fee_rate,
    analyze_trend,
    estimate_fee_income,
    estimate_net_return,
    evaluate_pool,
    fees_active_tvl_ratio,
    normalize_pool,
    persistence_ratio,
    rank_pools,
)


def make_pool(**kwargs) -> PoolSnapshot:
    defaults = dict(
        pool_id="0x" + "a" * 40,
        pair="TEST / USDG",
        protocol="v3",
        token0=TokenInfo("0x1111111111111111111111111111111111111111", "TEST", 18),
        token1=TokenInfo("0x2222222222222222222222222222222222222222", "USDG", 6),
        fee_ppm=500,
        tvl_usd=1_000_000.0,
        active_tvl_usd=100_000.0,
        volume_usd=1_000_000.0,
        fees_usd=5_000.0,
        window="24h",
        window_complete=True,
    )
    defaults.update(kwargs)
    return PoolSnapshot(**defaults)


CFG = ScreenConfig()


# ---------------------------------------------------------------- 归一化
def test_normalize_missing_fields_stay_none():
    pool = normalize_pool({"id": "0x" + "b" * 64, "pair": "X / Y",
                           "protocol": "v4", "token0": {}, "token1": {}})
    assert pool is not None
    assert pool.volume_usd is None          # 缺失 ≠ 0
    assert pool.fees_usd is None
    assert pool.active_tvl_usd is None
    assert pool.pool_address is None        # v4 是 32 字节 id，无池地址
    assert pool.window_complete is False


def test_normalize_v3_address():
    row = {"id": "0x" + "c" * 40, "pair": "A / B", "protocol": "v3",
           "token0": {"address": "0x" + "1" * 40, "symbol": "A"},
           "token1": {"address": "0x" + "2" * 40, "symbol": "B"},
           "volume_usd": "1234.5", "fee_ppm": 500,
           "coverage": {"complete": True, "window": "24h", "observed_s": 86400}}
    pool = normalize_pool(row)
    assert pool.pool_address == "0x" + "c" * 40
    assert pool.volume_usd == 1234.5
    assert pool.window_complete is True


# ---------------------------------------------------------------- 指标
def test_actual_fee_rate_is_volume_weighted():
    pool = make_pool(volume_usd=1_000_000.0, fees_usd=5_000.0)
    assert actual_fee_rate(pool) == 0.005


def test_actual_fee_rate_none_when_missing():
    assert actual_fee_rate(make_pool(volume_usd=None)) is None
    assert actual_fee_rate(make_pool(fees_usd=None)) is None
    assert actual_fee_rate(make_pool(volume_usd=0.0)) is None


def test_active_tvl_missing_never_uses_total_tvl():
    """Active TVL 不可用时返回 None——绝不用总 TVL 冒充。"""
    pool = make_pool(tvl_usd=1_000_000.0, active_tvl_usd=None, fees_usd=5_000.0)
    assert fees_active_tvl_ratio(pool) is None


def test_fees_active_tvl_ratio():
    pool = make_pool(active_tvl_usd=100_000.0, fees_usd=5_000.0)
    assert fees_active_tvl_ratio(pool) == 0.05


def test_persistence_ratio():
    # 参数为 (24h 成交量, 7 天总成交量)：7d 总量 4900 → 日均 700
    assert persistence_ratio(700.0, 4900.0) == 1.0          # 24h == 7d 日均
    assert persistence_ratio(350.0, 4900.0) == 0.5
    assert persistence_ratio(700.0, 700.0) == 7.0           # 7d 总量本身很小
    assert persistence_ratio(None, 4900.0) is None           # 数据不可用
    assert persistence_ratio(700.0, None) is None


# ---------------------------------------------------------------- 评估
def test_evaluate_pool_passes_all_conditions():
    pool = make_pool(volume_usd=600_000.0, fees_usd=6_000.0,
                     active_tvl_usd=50_000.0)  # 费率 1%，F/ATVL 12%
    pool.ds_price_change = {"h1": 0.5, "h6": 1.0, "h24": 2.0}
    ev = evaluate_pool(pool, CFG, make_pool(volume_usd=600_000.0 * 7))
    assert ev.passed
    assert ev.failures == []
    assert ev.actual_fee_rate == 0.01
    assert ev.fees_active_tvl == 0.12
    assert ev.persistence_ratio == 1.0
    assert ev.risk_level == RiskLevel.LOW


def test_evaluate_pool_marks_missing_data_unavailable():
    pool = make_pool(volume_usd=None, fees_usd=None, active_tvl_usd=None)
    ev = evaluate_pool(pool, CFG, None)
    assert not ev.passed
    assert any("数据不可用" in f for f in ev.failures)
    assert any("成交量" in f for f in ev.failures)
    assert any("手续费" in f for f in ev.failures)
    assert any("Active TVL" in f for f in ev.failures)
    assert "Active TVL 不可用，无法计算 Fees/Active TVL（-15 分）" in ev.deductions


def test_evaluate_pool_flags_downtrend():
    pool = make_pool()
    pool.ds_price_change = {"h1": -10.0, "h6": -20.0, "h24": -25.0}
    ev = evaluate_pool(pool, CFG, None)
    assert ev.trend.one_sided_downtrend
    assert ev.risk_level == RiskLevel.HIGH
    assert any("-30 分" in d for d in ev.deductions)


def test_trend_counts_consecutive_big_red_candles():
    pool = make_pool()
    pool.candles_5m = [
        {"ts": 300 * i, "open": 100, "high": 100, "low": 90, "close": 100}
        for i in range(10)
    ]
    # 最后 3 根连续跌 2%
    pool.candles_5m[-1]["close"] = pool.candles_5m[-1]["open"] = 95.0
    pool.candles_5m[-2]["close"] = pool.candles_5m[-2]["open"] = 96.9
    pool.candles_5m[-3]["close"] = pool.candles_5m[-3]["open"] = 98.838
    cfg = ScreenConfig(big_candle_drop_pct=1.0, big_candle_count=3)
    trend = analyze_trend(pool, cfg)
    assert trend.consecutive_big_red == 3
    assert any("连续大阴线" in r for r in trend.reasons)


def test_trend_lower_highs_lows_detection():
    pool = make_pool()
    # 4 个周期的高-中-低锯齿：H=[100,98,96,94]，T=[95,93,91,89]
    # 严格分形：peak 高点 > 两侧，trough 低点 < 两侧，且逐周期走低
    candles = []
    ts = 0
    for i in range(4):
        h, t = 100.0 - 2 * i, 95.0 - 2 * i
        m = (h + t) / 2
        candles.append({"ts": ts, "open": h, "high": h, "low": m, "close": h})
        ts += 300
        candles.append({"ts": ts, "open": m, "high": h - 0.4, "low": t + 0.4,
                        "close": m})
        ts += 300
        candles.append({"ts": ts, "open": m, "high": m, "low": t, "close": t})
        ts += 300
    pool.candles_5m = candles
    trend = analyze_trend(pool, CFG)
    assert trend.lower_highs_lows


# ---------------------------------------------------------------- 排名
def test_rank_pools_orders_by_weighted_score():
    strong = make_pool(pool_id="0x" + "1" * 40, fees_usd=20_000.0,
                       volume_usd=2_000_000.0, active_tvl_usd=50_000.0)
    weak = make_pool(pool_id="0x" + "2" * 40, fees_usd=6_000.0,
                     volume_usd=600_000.0, active_tvl_usd=100_000.0)
    strong.ds_price_change = {"h1": 0.1, "h6": 0.2, "h24": 0.5}
    weak.ds_price_change = {"h1": 0.1, "h6": 0.2, "h24": 0.5}
    week = make_pool(volume_usd=700.0 * 7)   # 7d 总量 4900 → 日均 700
    evs = [evaluate_pool(p, CFG, week) for p in (weak, strong)]
    ranked = rank_pools(evs, CFG)
    assert len(ranked) == 2
    assert ranked[0].pool.pool_id == strong.pool_id
    assert 0 <= ranked[0].score <= 100
    assert ranked[0].score_components["Fees/Active TVL"] >= \
        ranked[1].score_components["Fees/Active TVL"]


def test_rank_pools_with_single_pool_uses_anchor():
    pool = make_pool(fees_usd=10_000.0, volume_usd=1_000_000.0,
                     active_tvl_usd=100_000.0)
    pool.ds_price_change = {"h1": 0.0, "h6": 0.0, "h24": 0.0}
    week = make_pool(volume_usd=200_000.0 * 7)
    ev = evaluate_pool(pool, CFG, week)
    ranked = rank_pools([ev], CFG)
    assert len(ranked) == 1
    assert ranked[0].score > 0


def test_rank_pools_applies_deductions():
    complete = make_pool(pool_id="0x" + "3" * 40, fees_usd=10_000.0)
    incomplete = make_pool(pool_id="0x" + "4" * 40, fees_usd=10_000.0,
                           window_complete=False)
    complete.ds_price_change = {"h1": 0, "h6": 0, "h24": 0}
    incomplete.ds_price_change = {"h1": 0, "h6": 0, "h24": 0}
    week = make_pool(volume_usd=200_000.0 * 7)
    e1 = evaluate_pool(complete, CFG, week)
    e2 = evaluate_pool(incomplete, CFG, week)
    assert e2.deductions, "窗口不完整应有扣分记录"
    ranked = rank_pools([e1, e2], CFG)
    scores = {e.pool.pool_id: e.score for e in ranked}
    assert scores[complete.pool_id] > scores[incomplete.pool_id]


# ---------------------------------------------------------------- 手续费收入
def test_fee_income_share_formula():
    """share = capital / (active_tvl + capital)，收入 = 池子手续费 × share。"""
    pool = make_pool(active_tvl_usd=99_000.0, fees_usd=1_000.0)
    est = estimate_fee_income(pool, capital_usd=1_000.0, in_range_ratio=1.0)
    assert est.computable
    assert abs(est.effective_share - 1_000 / 100_000) < 1e-9
    assert abs(est.daily_income - 1_000 * 0.01) < 1e-9
    assert abs(est.income_7d - 0.01 * 1_000 * 7) < 1e-6
    assert abs(est.income_30d - 0.01 * 1_000 * 30) < 1e-6


def test_fee_income_scales_with_in_range_ratio():
    pool = make_pool(active_tvl_usd=100_000.0, fees_usd=1_000.0)
    full = estimate_fee_income(pool, 1_000.0, 1.0)
    half = estimate_fee_income(pool, 1_000.0, 0.5)
    assert half.daily_income < full.daily_income


def test_fee_income_uncomputable_without_active_tvl():
    pool = make_pool(active_tvl_usd=None, fees_usd=1_000.0, tvl_usd=1_000_000.0)
    est = estimate_fee_income(pool, 1_000.0, 1.0)
    assert not est.computable
    assert any("Active TVL" in a for a in est.assumptions)
    assert est.daily_income is None


# ---------------------------------------------------------------- 净收益
def test_net_return_zero_price_change_equals_fees():
    """价格不变时：LP=基准，相对净收益 = 手续费 - 成本。"""
    pool = make_pool(active_tvl_usd=99_000.0, fees_usd=1_000.0)
    fee_est = estimate_fee_income(pool, 1_000.0, 1.0)
    net = estimate_net_return(pool, 1_000.0, fee_est, days=1,
                              price_change_pct=0.0)
    assert net.computable
    assert net.lp_final_value == net.baseline_final_value
    assert abs(net.relative_net_return - net.fee_income) < 1e-9


def test_net_return_down_price_loss_shown_not_double_counted():
    """价格下跌：损失体现在 LP−基准差值中，IL 不再单独扣一次。"""
    pool = make_pool(active_tvl_usd=99_000.0, fees_usd=0.0)
    fee_est = FeeEstimate(1000.0, 0.0, 0.01, 0.0, 0.0, 0.0,
                          assumptions=[], computable=True)
    net = estimate_net_return(pool, 1_000.0, fee_est, days=1,
                              price_change_pct=-19.0)
    assert net.computable
    assert net.lp_final_value < net.baseline_final_value
    assert net.il_pct < 0
    # 净收益 = LP − 基准 + 手续费(0) − 成本(0)，IL 未被重复扣除
    expected = net.lp_final_value - net.baseline_final_value
    assert abs(net.relative_net_return - expected) < 1e-9


def test_net_return_costs_subtracted_once():
    pool = make_pool(active_tvl_usd=99_000.0, fees_usd=1_000.0)
    fee_est = estimate_fee_income(pool, 1_000.0, 1.0)
    base = estimate_net_return(pool, 1_000.0, fee_est, 1, 0.0)
    with_costs = estimate_net_return(pool, 1_000.0, fee_est, 1, 0.0,
                                     entry_cost=1.0, exit_cost=2.0,
                                     rebalance_cost=3.0)
    assert abs(with_costs.relative_net_return
               - (base.relative_net_return - 6.0)) < 1e-9


def test_net_return_uncomputable_without_price_change():
    pool = make_pool()
    fee_est = estimate_fee_income(pool, 1_000.0, 1.0)
    net = estimate_net_return(pool, 1_000.0, fee_est, 1, price_change_pct=None)
    assert not net.computable
    assert "无法准确计算" in net.unavailable_reason
    assert net.relative_net_return is None


def test_net_return_uncomputable_without_fees():
    pool = make_pool(active_tvl_usd=None)
    fee_est = estimate_fee_income(pool, 1_000.0, 1.0)
    net = estimate_net_return(pool, 1_000.0, fee_est, 1, price_change_pct=1.0)
    assert not net.computable

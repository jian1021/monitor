"""lprange 统计口径与验收标准 1~4、7 的单元测试。"""

import math

import numpy as np
import pytest

from lprange import stats


# ---------- 验收 1：恒定价格 ----------
def test_constant_price_full_time_in_range():
    close = np.full(1000, 5.0)
    for hold in stats.DEFAULT_HOLDS:
        tir = stats.symmetric_tir(stats.build_ratios(close, hold), stats.width_grid())
        assert np.all(tir == 1.0)


# ---------- 验收 2：每小时 ±1% 来回震荡 ----------
def test_oscillation_threshold_at_one_percent():
    """序列 100, 101, 100, 101 …：开仓价 P 之后的价格只有 P 本身和偏离约 1% 两种。

    宽度 ≥ 1%：全部在区间内 → 100%；
    宽度 < 1%：偏离 1% 的那一半小时全部出界 → 50%。
    （窗口里一半小时就是开仓价本身，按第 6 节口径永远在区间内，所以不可能是 0%。）
    """
    close = np.array([100.0, 101.0] * 500)
    ratios = stats.build_ratios(close, 168)
    tir = stats.symmetric_tir(ratios, np.array([0.005, 0.0099, 0.01, 0.02, 0.5]))
    assert np.allclose(tir[:, 2:], 1.0)
    assert np.allclose(tir[:, :2], 0.5)


# ---------- 验收 3：单边上涨 ----------
def test_monotonic_rise_exits_up_and_never_returns():
    close = 100 * 1.01 ** np.arange(1000)
    ratios = stats.build_ratios(close, 168)
    e = stats.exit_stats(ratios, 0.05, 0.05)
    assert e["up_share"] == 1.0 and e["down_share"] == 0.0
    assert e["never_return_ratio"] == 1.0
    assert e["avg_exits"] == 1.0
    assert e["avg_return_hours"] is None


# ---------- 验收 4：手工小样本 ----------
def test_manual_small_sample():
    close = np.array([100.0, 102.0, 98.0, 101.0, 99.0])
    ratios = stats.build_ratios(close, 2)
    # 样本：P=100 → [102, 98]；P=102 → [98, 101]；P=98 → [101, 99]
    assert ratios.shape == (3, 2)
    tir = stats.time_in_range(ratios, 0.02, 0.02)
    assert np.allclose(tir, [1.0, 0.5, 0.5])          # 102 与 98 正好压在 ±2% 边界上，边界含
    s = stats.summarize(tir[:, None], [0.7])
    assert s["mean"][0] == pytest.approx(2 / 3)
    assert s["median"][0] == 0.5
    assert s["p10"][0] == 0.5                          # 不插值的下侧样本值
    assert s["frac_ge"][0.7][0] == pytest.approx(1 / 3)


def test_p10_means_ninety_percent_of_samples_reach_it():
    values = np.arange(1, 101) / 100                   # 0.01 … 1.00
    p = stats.p10(values)
    assert (values >= p).mean() >= 0.9


def test_vectorized_matches_naive():
    rng = np.random.default_rng(7)
    close = np.exp(np.cumsum(rng.normal(0, 0.02, 2000)))
    widths = stats.width_grid()[::5]
    for hold in (168, 720):
        r = stats.build_ratios(close, hold)
        fast = stats.symmetric_tir(r, widths)
        naive = np.stack([stats.time_in_range(r, w, w) for w in widths], axis=1)
        assert np.abs(fast - naive).max() == 0
        counts = stats.range_counts(r, widths, widths)
        for m, dn in enumerate(widths):
            for k, up in enumerate(widths):
                assert np.array_equal(counts[:, m, k],
                                      np.round(stats.time_in_range(r, dn, up) * hold).astype(int))


def test_recommendation_and_skew_are_consistent():
    rng = np.random.default_rng(1)
    close = np.exp(np.cumsum(rng.normal(0, 0.01, 24 * 180)))
    res = stats.analyze(close, history_days=180)
    for h in res:
        rec = h.recommend[0.8]
        assert rec["typical"] is not None and rec["conservative"] is not None
        assert rec["typical"] <= rec["conservative"]
        d, u = h.skew[0.8]["typical"]
        # 偏斜最优的总宽度不会比对称典型档更宽
        assert stats.log_width(d, u) <= stats.log_width(rec["typical"], rec["typical"]) + 1e-12


def test_performance_180_days_under_a_few_seconds():
    import time
    close = np.exp(np.cumsum(np.random.default_rng(2).normal(0, 0.02, 24 * 180)))
    t0 = time.perf_counter()
    stats.analyze(close, history_days=180)
    assert time.perf_counter() - t0 < 10


# ---------- 第 7 节 / 验收 7：数据质量降级 ----------
def test_quality_rules():
    # 默认倍数 3：历史不足 90 天时 1 个月一档样本不足（验收第 7 条）
    assert stats.DEFAULT_MIN_HISTORY_MULT == 3
    assert stats.quality_status(40, 720) == stats.STATUS_INSUFFICIENT
    assert stats.quality_status(89, 720) == stats.STATUS_INSUFFICIENT
    assert stats.quality_status(90, 720) == stats.STATUS_OK
    # 改回第 7 节原规则 1.5 倍：45 天起给推荐，独立窗口 < 3 标「仅供参考」
    assert stats.quality_status(40, 720, min_history_mult=1.5) == stats.STATUS_INSUFFICIENT
    assert stats.quality_status(60, 720, min_history_mult=1.5) == stats.STATUS_REFERENCE
    assert stats.quality_status(90, 720, min_history_mult=1.5) == stats.STATUS_OK


def test_short_history_gives_no_recommendation_for_one_month():
    close = np.exp(np.cumsum(np.random.default_rng(3).normal(0, 0.01, 24 * 60)))
    res = {h.label: h for h in stats.analyze(close, history_days=60)}
    assert res["1m"].status == stats.STATUS_INSUFFICIENT and not res["1m"].recommend   # 60 < 90
    assert res["3w"].status == stats.STATUS_INSUFFICIENT                               # 60 < 63
    assert res["2w"].recommend and res["1w"].recommend


# ---------- bin 换算 ----------
def test_bin_counts():
    b = stats.bin_counts(0.26, 0.26, 100, 69)
    step = math.log(1.01)
    assert b["bins_down"] == math.ceil(math.log(1 / 0.74) / step)
    assert b["bins_up"] == math.ceil(math.log(1.26) / step)
    assert b["bins_total"] == b["bins_down"] + b["bins_up"] + 1
    assert stats.bin_counts(0.5, 0.5, 10, 69)["positions"] > 1

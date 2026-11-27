"""策略回测模块测试（全部离线）。"""

import math

import numpy as np
import pytest

from lprange import backtest as bt

N = 24 * 60
FEE, TVL = 0.01, 100_000.0


def _sim(close, vol=None, **kw):
    vol = np.full(len(close), 1000.0) if vol is None else vol
    args = dict(fee_rate=FEE, tvl_usd=TVL, width_mode="fixed", width_value=0.1, rule="out:1",
                hold=168, start=72)
    args.update(kw)
    return bt.simulate(np.asarray(close, float), vol, **args)


# ---------- 无常损失公式 ----------
def test_lp_hodl_ratio_basic_properties():
    assert bt.lp_hodl_ratio(np.array([100.0]), 100, 0.1)[0] == pytest.approx(1.0)
    p = np.exp(np.random.default_rng(1).normal(np.log(100), 0.4, 5000))
    r = bt.lp_hodl_ratio(p, 100, 0.2)
    assert np.all(r <= 1 + 1e-12) and np.all(r > 0)          # LP 永远不跑赢持币（不含手续费）
    # 越窄的区间，同样的价格偏离下无常损失越大
    assert bt.lp_hodl_ratio(np.array([95.0]), 100, 0.05)[0] < bt.lp_hodl_ratio(np.array([95.0]), 100, 0.5)[0]


# ---------- 恒定价格 ----------
def test_constant_price_no_il_full_share_and_exact_fees():
    r = _sim(np.full(N, 2.0))
    assert r.time_share == 1.0 and r.volume_share == 1.0
    assert r.rebalances == 0 and r.il_log == pytest.approx(0.0, abs=1e-12)
    per_hour = 1000 * FEE * bt.DEFAULT_LIQ_WIDTH / (math.log(1.1 / 0.9) * TVL)
    assert r.fee_log == pytest.approx((N - 1 - 72) * math.log1p(per_hour))


def test_hold_rule_rebalances_every_h_and_pays_cost():
    r = _sim(np.full(N, 2.0), rule="hold")
    assert r.rebalances == (N - 1 - 72) // 168
    assert r.cost_log == pytest.approx(r.rebalances * math.log1p(-(0.5 * FEE + bt.DEFAULT_REBALANCE_EXTRA)))


def test_narrower_range_earns_more_fees_when_always_in_range():
    flat = np.full(N, 2.0)
    assert _sim(flat, width_value=0.05).fee_log > _sim(flat, width_value=0.2).fee_log


# ---------- 单边行情 ----------
def test_trend_triggers_out_rebalances_and_negative_il():
    c = 100 * 1.003 ** np.arange(N)
    fast = _sim(c, width_value=0.05, rule="out:1")
    slow = _sim(c, width_value=0.05, rule="out:24")
    assert fast.rebalances > slow.rebalances > 0
    assert fast.il_log < 0 and fast.net_log < fast.fee_log
    assert slow.time_share < fast.time_share                  # 容忍出界越久，在区间外的时间越多


def test_volume_share_counts_only_in_range_hours():
    c = np.full(N, 100.0)
    c[500:600] = 150.0                                         # 这 100 小时出界；out:1000 不会在期间调仓
    vol = np.zeros(N)
    vol[500:600] = 10.0
    vol[1000] = 10.0
    r = _sim(c, vol, rule="out:1000")
    assert r.volume_share == pytest.approx(10 / 1010)


# ---------- 波动率定宽 ----------
def test_vol_rule_half_width_is_k_sigma_sqrt_h():
    c = 100 * np.exp(np.cumsum(np.tile([0.01, -0.01], N // 2)))
    sigma = bt.ewma_sigma(c, 72)
    assert sigma[-1] == pytest.approx(0.01, rel=1e-3)
    r = _sim(c, width_mode="vol", width_value=1.5, rule="hold", sigma=sigma)
    assert r.avg_half_width == pytest.approx(1.5 * 0.01 * math.sqrt(168), rel=1e-2)


def test_ewma_sigma_has_no_lookahead():
    c = np.full(500, 100.0)
    c[300:] = 200.0                                            # t=300 跳涨
    s = bt.ewma_sigma(c, 24, warmup=24)
    assert s[299] == 0 and s[300] > 0


# ---------- 网格与排序 ----------
def test_grid_size_sorting_and_windows():
    rng = np.random.default_rng(3)
    c = np.exp(np.cumsum(rng.normal(0, 0.02, 24 * 90)))
    v = rng.lognormal(8, 1, c.size)
    res = bt.run_grid(c, v, fee_rate=0.02, tvl_usd=2e5)
    assert len(res.combos) == (len(bt.DEFAULT_FIXED_WIDTHS) + len(bt.DEFAULT_KS)) * len(bt.DEFAULT_RULES)
    nets = [x.net_annual for x in res.sorted()]
    assert nets == sorted(nets, reverse=True)
    c0 = res.combos[0]
    assert c0.independent_windows == pytest.approx(res.hours / 168)
    assert c0.window_net.size == res.hours // 168
    row = bt.combo_row(c0)
    for key in ("净收益年化", "手续费年化", "无常损失年化", "调仓次数", "调仓成本年化", "时间占比",
                "成交量占比", "独立窗口"):
        assert key in row


def test_backtest_cli_uses_pool_ratio_price(tmp_path, monkeypatch, capsys):
    from lprange import cli, data

    hour = 3600
    now = 1_790_000_000 // hour * hour
    seen = []

    class Fake:
        requests_made = 0

        def pool_info(self, network, address):
            return {"network": network, "address": address, "name": "T / WETH 0.3%", "reserve_usd": 2e5}

        def ohlcv_page(self, network, pool, before, currency, token):
            seen.append(currency)
            top = now if before is None else int(before) // hour * hour
            oldest = now - (24 * 40 - 1) * hour
            rows, t = [], top
            while t >= oldest and len(rows) < 1000:
                k = (t - oldest) // hour
                px = 1 + 0.02 * math.sin(k / 5) if currency == "quote" else 150.0   # 美元价恒定、比价在动
                rows.append([t, px, px, px, px, 3000.0])
                t -= hour
            return rows

    monkeypatch.setattr(cli, "GeckoClient", lambda interval=2.5: Fake())
    monkeypatch.setattr(data.time, "time", lambda: now)
    rc = cli.main(["backtest", "0x" + "ab" * 20, "--network", "robinhood", "--days", "40",
                   "--db", str(tmp_path / "w.db"), "--no-cache"])
    out = capsys.readouterr().out
    assert rc == 0 and {"quote", "usd"} <= set(seen)
    assert "费率 0.3%" in out and "策略回测" in out

# -*- coding: utf-8 -*-
"""robinhood-chain-lp-tools 输出解析（样例取自脚本真实输出格式）."""

import pytest

from app.infrastructure.lp_tools import runner

APR_SAMPLE = """
=== WETH / USDG  (Uniswap v4, Robinhood Chain) ===
pool          0x1234567890abcdef1234567890abcdef12345678
hooks         0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef  <- can change fees/behaviour
tokens        WETH (18d) / USDG (6d)
fee           0.0460%   LPs keep 75.00% of it
tick / spacing 12345 / 1
spot          0.00032000  USDG per WETH
active liq    1.2345e+18
TVL           $1.23M
volume        24h $12.23M | 6h $1.00M | 1h $0.10M

--- headline APR (what a pool screener shows) ---
annualised LP fees   $5.60k   (from h24 volume)
pool-average APR     101.00%   = LP fees / total TVL
  ^ this is a blended number over ALL liquidity, in range or not.
    A real position earns its share of fees among ACTIVE liquidity only.

--- your range, $10.0k position ---
range            0.00028800 - 0.00035200  USDG per WETH
ticks            [12300, 12400]  (aligned to spacing 1)
your liquidity   1.2345e+22  -> 5.00% of active liquidity
APR while in range   32.00%   (0.3x the pool-average APR)
fees/day in range    $0.87

realised vol     5.00%/day  (91.29% annualised)
time in range    80.00% expected over 30d (GBM, no rebalancing)
vol-adjusted APR 25.60%   <-- the number to compare against alternatives
  Excludes impermanent loss and rebalancing costs; it is fee yield only.

"""

SWEEP_SAMPLE = APR_SAMPLE + """
--- APR vs range width, $10.0k position ---
width    range (USDG per WETH)                 share    APR in-range   x pool avg   in-range 30d   vol-adj APR
+/- 1%   0.00031680 - 0.00032320    50.00%      1.20%       0.1x        10.00%        0.12%
+/-10%   0.00028800 - 0.00035200     5.00%     32.00%       0.3x        80.00%       25.60%
+/-50%   0.00016000 - 0.00048000     1.00%      4.00%       0.0x          n/a           n/a
"""

REPLAY_SAMPLE = """
=== WETH / USDG  (Uniswap v4, Robinhood Chain) ===
pool     0x1234567890abcdef1234567890abcdef12345678
window   last 24h  (blocks 1,234,567 - 1,240,000)
spot     0.00032000 USDG per WETH
range    0.00028800 - 0.00035200   ticks [12300, 12400]
capital  $10.0k  ->  liquidity 1.2345e+22
protocol fee 0/0 ppm of input, deducted before the LP share

--- what actually happened, last 24h ---
swaps            50,010  (48,000 inside your range)
volume (in-legs) $12.23M    GeckoTerminal says $12.02M
LP fees, whole pool $5.6k
mean fee rate    0.0577%  (per-swap, from the event)
time in range    96.00%  (block-weighted, measured not modelled)

--- your $10.0k position over that window ---
fees earned      $29.32   = WETH 0.012345 + USDG 12.345
return           0.29% over 24h
annualised APR   107.00%   <-- realised, not extrapolated
share of pool fees 0.52%

--- validation: replay vs feeGrowthGlobal, blocks 1237001-1240000 ---
fee growth WETH:  replay 6.383207e-3  chain 6.383799e-3  ratio 0.9999
fee growth USDG:  replay 1.781253e-11 chain 1.781048e-11 ratio 1.0001
  Ratios near 1.0 confirm the fee maths, sign convention and protocol-fee cut.

"""


def test_parse_apr_single_range():
    out = runner.parse_apr(APR_SAMPLE)
    assert out["name"] == "WETH / USDG"
    assert out["version"] == "v4"
    assert out["pool"].startswith("0x1234")
    assert out["fee_pct"] == pytest.approx(0.046)
    assert out["lp_fee_share"] == pytest.approx(0.75)
    assert out["pool_avg_apr"] == pytest.approx(1.01)
    assert out["apr_in_range"] == pytest.approx(0.32)
    assert out["vol_adjusted_apr"] == pytest.approx(0.256)
    assert out["time_in_range_modelled"] == pytest.approx(0.80)
    assert out["liquidity_share"] == pytest.approx(0.05)
    assert out["fees_per_day_usd"] == pytest.approx(0.87)
    assert out["vs_pool_avg"] == pytest.approx(0.3)
    assert out["dynamic_fee"] is False


def test_parse_apr_dynamic_fee_flag():
    text = APR_SAMPLE.replace(
        "fee           0.0460%   LPs keep 75.00% of it",
        "fee           0.0123% (DYNAMIC - set by hook, current value)   LPs keep 75.00% of it",
    )
    out = runner.parse_apr(text)
    assert out["dynamic_fee"] is True
    assert out["lp_fee_share"] == pytest.approx(0.75)


def test_parse_sweep_rows():
    rows = runner.parse_sweep(SWEEP_SAMPLE)
    assert len(rows) == 3
    assert rows[0]["width_pct"] == 1
    assert rows[1]["width_pct"] == 10
    assert rows[1]["apr_in_range"] == pytest.approx(0.32)
    assert rows[1]["vol_adjusted_apr"] == pytest.approx(0.256)
    assert rows[1]["x_pool_avg"] == pytest.approx(0.3)
    assert rows[1]["time_in_range"] == pytest.approx(0.80)
    assert rows[2]["time_in_range"] is None  # n/a
    assert rows[2]["vol_adjusted_apr"] is None


def test_parse_replay():
    out = runner.parse_replay(REPLAY_SAMPLE)
    assert out["name"] == "WETH / USDG"
    assert out["hours"] == 24
    assert out["from_block"] == 1234567
    assert out["to_block"] == 1240000
    assert out["swaps"] == 50010
    assert out["swaps_in_range"] == 48000
    assert out["volume_usd"] == pytest.approx(12.23e6)
    assert out["pool_fees_usd"] == pytest.approx(5.6e3)
    assert out["time_in_range"] == pytest.approx(0.96)
    assert out["fees_earned_usd"] == pytest.approx(29.32)
    assert out["return_pct"] == pytest.approx(0.0029)
    assert out["annualised_apr"] == pytest.approx(1.07)
    assert out["share_of_pool_fees"] == pytest.approx(0.0052)
    assert out["validation_ratios"] == [0.9999, 1.0001]
    assert "validation_warning" not in out


def test_parse_empty_text():
    assert runner.parse_apr("") == {}
    assert runner.parse_sweep("") == []
    assert runner.parse_replay("") == {}


def test_usd_to_float():
    assert runner._usd_to_float("$12.23M") == pytest.approx(12.23e6)
    assert runner._usd_to_float("$5.6k") == pytest.approx(5.6e3)
    assert runner._usd_to_float("$29.32") == pytest.approx(29.32)
    assert runner._usd_to_float("n/a") is None
    assert runner._usd_to_float("-") is None


def test_toolchain_layout():
    # 子模块应已拉取且 npm install 已执行
    assert runner.tools_available(), f"缺少子模块：{runner.TOOLS_DIR}"

"""价格区间计算测试（页面 ⑨ 的纯计算部分，不依赖网络）。

ATR 由现成技术指标库 `ta` 计算，此处验证：
  - 上价/下价公式与倍数的拼接正确；
  - ATR 结果与直接调用 ta.volatility.AverageTrueRange 一致；
  - 样本数超出可用K线时自动下调；
  - 无效输入与 K 线不足的防护。
"""

import unittest

import pandas as pd
from ta.volatility import AverageTrueRange

from app.application.meteora_scanner.price_range import _aggregate, compute_range


def _candles(n: int = 30) -> list[dict]:
    """趋势上行、高=昨收+1 / 低=昨收-1 的 n 根 K 线（ATR 稳定可预期）。"""
    out = []
    for i in range(n):
        close = 100 + i
        out.append({"timestamp": i * 3600, "open": close, "high": close + 1.0,
                    "low": close - 1.0, "close": close})
    return out


class TestComputeRange(unittest.TestCase):
    def test_upper_lower_with_multiplier(self):
        res = compute_range(130.0, _candles(), multiplier=1.5, period=14)
        self.assertIsNotNone(res["atr"])
        self.assertGreater(res["atr"], 0)
        self.assertAlmostEqual(res["upper"], 130.0 + 1.5 * res["atr"])
        self.assertAlmostEqual(res["lower"], 130.0 - 1.5 * res["atr"])
        self.assertEqual(res["period_used"], 14)

    def test_atr_matches_ta_library(self):
        candles = _candles()
        res = compute_range(130.0, candles, multiplier=1.5, period=14)
        df = pd.DataFrame(candles)
        expected = float(AverageTrueRange(
            high=df["high"], low=df["low"], close=df["close"],
            window=14, fillna=False).average_true_range().iloc[-1])
        self.assertAlmostEqual(res["atr"], expected)

    def test_period_reduced_when_candles_insufficient(self):
        candles = _candles(20)
        res = compute_range(120.0, candles, multiplier=1.5, period=60)
        self.assertEqual(res["period_used"], 19)
        self.assertIsNotNone(res["atr"])
        self.assertIsNotNone(res["upper"])
        self.assertIsNotNone(res["lower"])

    def test_invalid_price(self):
        res = compute_range(None, _candles(), multiplier=1.5, period=14)
        self.assertIsNone(res["atr"])
        self.assertIsNone(res["upper"])
        self.assertIsNone(res["lower"])

    def test_invalid_multiplier(self):
        res = compute_range(130.0, _candles(), multiplier=0.0, period=14)
        self.assertIsNone(res["atr"])
        self.assertIsNone(res["upper"])
        self.assertIsNone(res["lower"])

    def test_insufficient_candles(self):
        res = compute_range(10.0, [{"high": 1, "low": 1, "close": 1}], multiplier=1.5, period=14)
        self.assertIsNone(res["atr"])
        self.assertIsNone(res["upper"])
        self.assertEqual(res["candle_count"], 1)


class TestAggregate(unittest.TestCase):
    def test_aggregate_15min_from_5m(self):
        # 相邻 5m K 线（时间戳对齐 15min 窗口），应聚合为 2 根 15min K 线
        raw = [
            {"timestamp": 0, "open": 10, "high": 10, "low": 9, "close": 9.5, "volume": 10},
            {"timestamp": 300, "open": 9.5, "high": 11, "low": 9, "close": 10, "volume": 20},
            {"timestamp": 600, "open": 10, "high": 10.5, "low": 10, "close": 10.2, "volume": 5},
            {"timestamp": 1800, "open": 10.2, "high": 11, "low": 10, "close": 10.8, "volume": 30},
        ]
        out = _aggregate(raw, 900)
        self.assertEqual(len(out), 2)
        first, second = out[0], out[1]
        self.assertEqual(first["timestamp"], 0)
        self.assertEqual(first["open"], 10)
        self.assertEqual(first["close"], 10.2)
        self.assertEqual(first["high"], 11)
        self.assertEqual(first["low"], 9)
        self.assertAlmostEqual(first["volume"], 35)
        self.assertEqual(second["timestamp"], 1800)
        self.assertEqual(second["open"], 10.2)
        self.assertEqual(second["close"], 10.8)

    def test_aggregate_1h_from_30m(self):
        # 48 根 30m K 线应聚合为 24 根 1h K 线（按整点窗口）
        raw = []
        for i in range(48):
            raw.append({"timestamp": i * 1800, "open": float(i), "high": float(i) + 1,
                        "low": float(i) - 1, "close": float(i)})
        out = _aggregate(raw, 3600)
        self.assertEqual(len(out), 24)
        self.assertEqual(out[0]["open"], 0)
        self.assertEqual(out[0]["close"], 1)   # 相邻两根 30m 聚合为整点收盘
        self.assertEqual(out[1]["open"], 2)

    def test_aggregate_ignores_missing_fields(self):
        raw = [
            {"timestamp": 0, "open": 10, "high": 10, "low": 9, "close": 9.5},
            {"timestamp": 300, "open": None, "high": None, "low": None, "close": None},
            {"timestamp": None, "open": 1, "high": 1, "low": 1, "close": 1},
        ]
        out = _aggregate(raw, 900)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["close"], 9.5)


if __name__ == "__main__":
    unittest.main()
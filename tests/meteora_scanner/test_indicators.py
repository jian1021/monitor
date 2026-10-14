import unittest

from app.application.meteora_scanner.indicators import (TREND_DOWN, TREND_INSUFFICIENT, TREND_SIDEWAYS, TREND_UP,
                            compute_signals, compute_trend, rsi)


class TestIndicators(unittest.TestCase):
    def test_rsi_uptrend_high(self):
        self.assertGreater(rsi(list(range(1, 20)), 3), 90)

    def test_rsi_downtrend_low(self):
        self.assertLess(rsi(list(range(20, 1, -1)), 3), 10)

    def test_rsi_insufficient(self):
        self.assertIsNone(rsi([1, 2], 3))

    def test_trend(self):
        up = [{"close": 100 + i} for i in range(10)]
        down = [{"close": 100 - i} for i in range(10)]
        flat = [{"close": 100} for i in range(10)]
        self.assertEqual(compute_trend(up), TREND_UP)
        self.assertEqual(compute_trend(down), TREND_DOWN)
        self.assertEqual(compute_trend(flat), TREND_SIDEWAYS)
        self.assertEqual(compute_trend([]), TREND_INSUFFICIENT)
        self.assertEqual(compute_trend([{"close": 1}]), TREND_INSUFFICIENT)

    def test_signals_keys(self):
        candles = [{"close": 10 - i, "low": 10 - i, "high": 11 - i, "volume": 100.0}
                   for i in range(10)]
        sig = compute_signals(candles)
        self.assertEqual(
            set(sig.keys()),
            {"rsi3_oversold", "stop_new_low", "break_local_high", "volume_recovery"},
        )

    def test_volume_recovery_signal(self):
        candles = []
        for i in range(5):
            candles.append({"close": 10, "low": 9, "high": 11, "volume": 10.0})
        for i in range(3):
            candles.append({"close": 10, "low": 9, "high": 11, "volume": 100.0})
        self.assertTrue(compute_signals(candles)["volume_recovery"])


if __name__ == "__main__":
    unittest.main()

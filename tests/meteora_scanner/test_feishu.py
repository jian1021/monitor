import os
import tempfile
import unittest

from app.application.meteora_scanner.alerts import evaluate_after_scan
from app.application.meteora_scanner.db import Database
from app.application.meteora_scanner.feishu import FeishuAlerter


class TestFeishu(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.path)
        self.db = Database(self.path)

    def tearDown(self):
        self.db.close()
        for suffix in ("", "-wal", "-shm"):
            p = self.path + suffix
            if os.path.exists(p):
                os.remove(p)

    def _alerter(self):
        a = FeishuAlerter(self.db, "https://example.invalid/hook", "")
        self.sent = []

        def fake_post(text):
            self.sent.append(text)
            return True, "success"

        a._post = fake_post
        return a

    def test_message_contains_fields(self):
        a = FeishuAlerter(self.db, "", "")
        row = {"pool_type": "DLMM", "pool_address": "A", "token_x_symbol": "SOL",
               "token_x_name": "Wrapped SOL", "token_x_mint": "MX", "token_y_symbol": "USDC",
               "token_y_name": "USD Coin", "token_y_mint": "MY", "token_x_market_cap": 1e9,
               "token_y_market_cap": 5e6, "tvl": 1e6, "volume_24h": 2e6, "fees_24h": 5000,
               "fee_tvl_ratio_24h": 0.5, "fee_rate_pct": 0.04}
        msg = a.build_pool_message("mcap_reached", row, "原因", score=80)
        for token in ["池地址", "池类型", "Mint", "市值", "TVL", "成交量", "手续费", "Fees/TVL", "评分", "更新时间"]:
            self.assertIn(token, msg)

    def test_dedup_prevents_resend(self):
        a = self._alerter()
        row = {"pool_address": "A", "pool_type": "DLMM", "token_x_symbol": "SOL"}
        ok1, _ = a.send_pool_alert("mcap_reached", row, "r")
        ok2, msg2 = a.send_pool_alert("mcap_reached", row, "r")
        self.assertTrue(ok1)
        self.assertFalse(ok2)
        self.assertEqual(msg2, "duplicate")
        self.assertEqual(len(self.sent), 1)

    def test_failure_logged(self):
        a = FeishuAlerter(self.db, "https://example.invalid/hook", "")
        a._post = lambda text: (False, "网络错误")
        ok, msg = a.send_pool_alert("mcap_reached", {"pool_address": "A"}, "r")
        self.assertFalse(ok)
        alerts = self.db.get_alerts()
        self.assertEqual(alerts[0]["status"], "fail")

    def test_not_configured(self):
        a = FeishuAlerter(self.db, "", "")
        self.assertFalse(a.configured)
        ok, msg = a._post("hi")
        self.assertFalse(ok)
        self.assertIn("未配置", msg)


class TestAlertsOrchestration(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.path)
        self.db = Database(self.path)

    def tearDown(self):
        self.db.close()
        for suffix in ("", "-wal", "-shm"):
            p = self.path + suffix
            if os.path.exists(p):
                os.remove(p)

    def test_evaluate_skips_when_unconfigured(self):
        a = FeishuAlerter(self.db, "", "")
        rows = [{"pool_address": "A", "passed": True, "first_seen": "2100-01-01T00:00:00+00:00",
                 "volume_24h": 1e9, "fee_tvl_ratio_24h": 99.0}]
        stats = evaluate_after_scan(self.db, a, rows, volume_threshold=1.0, fee_tvl_threshold=1.0)
        self.assertFalse(stats["configured"])
        self.assertEqual(stats["sent"], 0)

    def test_evaluate_sends_for_new_and_thresholds(self):
        a = FeishuAlerter(self.db, "https://example.invalid/hook", "")
        calls = []
        a._post = lambda text: (calls.append(text) or (True, "success"))
        rows = [{"pool_address": "A", "passed": True, "first_seen": "2100-01-01T00:00:00+00:00",
                 "pool_type": "DLMM", "token_x_symbol": "SOL", "token_y_symbol": "USDC",
                 "tvl": 1e6, "volume_24h": 5e6, "fee_tvl_ratio_24h": 2.0}]
        stats = evaluate_after_scan(self.db, a, rows, volume_threshold=1e6, fee_tvl_threshold=1.0)
        self.assertTrue(stats["configured"])
        self.assertGreaterEqual(stats["sent"], 1)
        self.assertGreater(len(calls), 0)


if __name__ == "__main__":
    unittest.main()

import os
import tempfile
import unittest

from app.application.meteora_scanner.db import Database
from app.application.meteora_scanner.models import PoolRecord, TokenInfo


class TestDatabase(unittest.TestCase):
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

    def _rec(self, addr="A1", mcap=2_000_000.0):
        return PoolRecord(
            pool_address=addr, pool_type="DLMM",
            token_x=TokenInfo(mint="MX", symbol="SOL", market_cap=mcap),
            token_y=TokenInfo(mint="MY", symbol="USDC", market_cap=100_000.0),
            tvl=100_000.0, volume_24h=100_000.0, fees_24h=100.0, fee_tvl_ratio_24h=0.1,
        )

    def test_upsert_and_first_seen(self):
        self.assertTrue(self.db.upsert_pool(self._rec()))
        self.assertFalse(self.db.upsert_pool(self._rec()))
        row = self.db.get_pool("A1")
        self.assertIsNotNone(row["first_seen"])
        self.assertEqual(self.db.count_pools().get("DLMM"), 1)

    def test_watchlist_lifecycle(self):
        self.db.add_watch("A1", "DLMM")
        self.assertTrue(self.db.is_watched("A1"))
        self.db.set_watch_state("A1", paused=True)
        self.assertEqual(self.db.list_watch()[0]["paused"], 1)
        self.db.remove_watch("A1")
        self.assertFalse(self.db.is_watched("A1"))

    def test_settings_roundtrip(self):
        self.db.set_setting("filters", {"min_market_cap": 123.0})
        self.assertEqual(self.db.get_setting("filters")["min_market_cap"], 123.0)
        self.assertEqual(self.db.get_setting("missing", "default"), "default")

    def test_snapshot_roundtrip(self):
        self.db.add_snapshot("A1", tvl=100.0, fees_24h=1.0, signals=["rsi3_oversold"])
        self.assertEqual(len(self.db.get_snapshots("A1")), 1)
        self.assertEqual(self.db.latest_snapshot("A1")["tvl"], 100.0)

    def test_alert_dedup(self):
        self.db.log_alert(pool_address="A1", pool_type="DLMM", event_type="mcap_reached",
                          dedup_key="mcap_reached:A1", message="m", status="ok")
        self.assertTrue(self.db.alert_sent_since("mcap_reached:A1", "2000-01-01T00:00:00+00:00"))
        self.assertFalse(self.db.alert_sent_since("mcap_reached:B2", "2000-01-01T00:00:00+00:00"))
        # 失败记录不应视为“已发送”
        self.db.log_alert(pool_address="A2", pool_type="DLMM", event_type="x",
                          dedup_key="k2", message="m", status="fail")
        self.assertFalse(self.db.alert_sent_since("k2", "2000-01-01T00:00:00+00:00"))

    def test_scan_status(self):
        self.db.update_scan_status("DLMM", pages_scanned=5, pools_discovered=10, state="running")
        s = {r["pool_type"]: r for r in self.db.get_scan_status()}
        self.assertEqual(s["DLMM"]["pages_scanned"], 5)
        self.assertIsNone(s["DLMM"]["total_available"])

    def test_token_cache(self):
        self.db.upsert_tokens({"MX": TokenInfo(mint="MX", symbol="SOL", market_cap=1e6, source="jupiter")})
        got = self.db.get_tokens(["MX", "UNKNOWN"])
        self.assertEqual(got["MX"].symbol, "SOL")
        self.assertNotIn("UNKNOWN", got)

    def test_api_error_log(self):
        self.db.log_api_error(pool_type="DLMM", endpoint="/pools", attempt=1, message="boom")
        errors = self.db.get_api_errors()
        self.assertEqual(errors[0]["message"], "boom")
        self.db.clear_api_errors()
        self.assertEqual(self.db.get_api_errors(), [])


if __name__ == "__main__":
    unittest.main()

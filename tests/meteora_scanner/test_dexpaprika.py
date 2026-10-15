"""DEX Paprika 数据源测试（价格区间计算的池级 K 线，不回放网络请求，使用伪客户端）。"""

import unittest
from datetime import datetime, timezone
from unittest import mock

from app.application.meteora_scanner.api_client import ApiError
from app.application.meteora_scanner.price_range import fetch_pool_market, resolve_pool
from app.application.meteora_scanner.providers import dexpaprika


def _epoch(y, m, d, hh, mm=0):
    return int(datetime(y, m, d, hh, mm, tzinfo=timezone.utc).timestamp())


class FakeClient:
    """实现 get_json(path, params) 的伪客户端。"""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_json(self, path, params=None, **kwargs):
        self.calls.append((path, params))
        if callable(self.payload):
            return self.payload(path, params)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class TestParseHelpers(unittest.TestCase):
    def test_parse_ts_iso_with_z(self):
        # 2026-10-13T14:00:00Z
        ts = dexpaprika._parse_ts("2026-10-13T14:00:00Z")
        self.assertEqual(ts, _epoch(2026, 10, 13, 14))

    def test_parse_ts_epoch(self):
        self.assertEqual(dexpaprika._parse_ts(1234567890), 1234567890)

    def test_parse_ts_invalid(self):
        self.assertIsNone(dexpaprika._parse_ts("not-a-date"))
        self.assertIsNone(dexpaprika._parse_ts(None))

    def test_to_float(self):
        self.assertEqual(dexpaprika._to_float("1.5"), 1.5)
        self.assertIsNone(dexpaprika._to_float(None))
        self.assertIsNone(dexpaprika._to_float("nan-str"))


class TestFetchOhlcv(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"time_open": "2026-10-13T13:30:00Z", "time_close": "2026-10-13T14:00:00Z",
             "open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1, "volume": 100.0},
            {"time_open": "2026-10-13T13:00:00Z", "time_close": "2026-10-13T13:30:00Z",
             "open": 0.9, "high": 1.1, "low": 0.8, "close": 1.0, "volume": 80.0},
        ]

    def test_parses_list_payload(self):
        client = FakeClient(self.rows)
        out = dexpaprika.fetch_ohlcv(client, "pool1", "30m")
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["timestamp"], _epoch(2026, 10, 13, 13))  # 13:00Z
        self.assertEqual(out[-1]["timestamp"], _epoch(2026, 10, 13, 13, 30))  # 13:30Z
        self.assertEqual(out[-1]["close"], 1.1)
        self.assertEqual(out[-1]["volume"], 100.0)

    def test_passes_interval_and_start_params(self):
        client = FakeClient(self.rows)
        dexpaprika.fetch_ohlcv(client, "pool1", "15m", start="-7d", limit=500)
        path, params = client.calls[0]
        self.assertTrue(path.endswith("/pools/pool1/ohlcv"))
        self.assertEqual(params["interval"], "15m")
        self.assertEqual(params["limit"], 500)
        self.assertEqual(params["start"], "-7d")

    def test_skips_invalid_rows(self):
        payload = [
            {"time_open": "2026-10-13T13:00:00Z", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": "1"},
            {"time_open": "bad-ts", "open": 1, "high": 2, "low": 0.5, "close": 1.5},
            {"time_open": "2026-10-13T12:00:00Z", "high": 1},          # 缺 open/close
            "not-a-dict",
        ]
        client = FakeClient(payload)
        out = dexpaprika.fetch_ohlcv(client, "pool1", "1h")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["timestamp"], _epoch(2026, 10, 13, 13))

    def test_accepts_dict_payload_with_data(self):
        client = FakeClient({"data": self.rows})
        out = dexpaprika.fetch_ohlcv(client, "pool1", "30m")
        self.assertEqual(len(out), 2)


class TestFetchPool(unittest.TestCase):
    def test_parses_pool(self):
        payload = {
            "id": "2YaGc2iYZt44B5zn5Tvk59APbAn51xGekzNQAPF2PGn1",
            "dex_id": "meteora",
            "base_token_id": "5tCju6YNxHq5zrA6tGndr6F7TK42mpUFmeE31cSFpump",
            "quote_token_id": "So11111111111111111111111111111111111111112",
            "last_price_usd": "0.003045491118298662",
            "tokens": [
                {"id": "5tCju6YNxHq5zrA6tGndr6F7TK42mpUFmeE31cSFpump", "symbol": "swordcat"},
                {"id": "So11111111111111111111111111111111111111112", "symbol": "SOL"},
            ],
        }
        info = dexpaprika.fetch_pool(FakeClient(payload), "pool1")
        self.assertEqual(info["pool_address"], "2YaGc2iYZt44B5zn5Tvk59APbAn51xGekzNQAPF2PGn1")
        self.assertEqual(info["name"], "swordcat/SOL")
        self.assertEqual(info["dex"], "meteora")
        self.assertEqual(info["token_symbol"], "swordcat")
        self.assertAlmostEqual(info["price_usd"], 0.003045491118298662)

    def test_empty_tokens_fallback(self):
        info = dexpaprika.fetch_pool(FakeClient({"id": "p", "dex_id": "meteora", "tokens": []}), "p")
        self.assertEqual(info["name"], "p…")
        self.assertEqual(info["token_symbol"], "")

    def test_none_payload(self):
        self.assertIsNone(dexpaprika.fetch_pool(FakeClient({}), "p"))


class TestResolvePool(unittest.TestCase):
    def test_success_path(self):
        payload = {"id": "p1", "dex_id": "meteora", "tokens": [{"symbol": "AAA"}, {"symbol": "SOL"}],
                   "last_price_usd": "1.23"}
        info = resolve_pool(FakeClient(payload), "  p1  ")
        self.assertIsNone(info["error"])
        self.assertEqual(info["name"], "AAA/SOL")

    def test_404_reports_not_found(self):
        def boom(path, params=None):
            raise ApiError("HTTP 404", url=path, status=404)
        info = resolve_pool(FakeClient(boom), "feed0000")
        self.assertIsNotNone(info["error"])
        self.assertIn("未找到", info["error"])


class TestFetchPoolMarket(unittest.TestCase):
    def test_price_falls_back_to_last_close(self):
        rows = [
            {"time_open": "2026-10-13T12:00:00Z", "open": 1, "high": 2, "low": 0.5, "close": 1.2, "volume": "1"},
            {"time_open": "2026-10-13T13:00:00Z", "open": 1, "high": 2, "low": 0.5, "close": 1.8, "volume": "2"},
        ]
        with mock.patch.object(dexpaprika, "fetch_ohlcv", return_value=dexpaprika.fetch_ohlcv(FakeClient(rows), "p1", "1h")), \
             mock.patch.object(dexpaprika, "fetch_pool", return_value={"price_usd": None}):
            market = fetch_pool_market(FakeClient([]), "p1", "1h")
        self.assertEqual(market["price"], 1.8)
        self.assertEqual(market["candle_count"], 2)

    def test_price_from_pool_details(self):
        with mock.patch.object(dexpaprika, "fetch_ohlcv", return_value=[]), \
             mock.patch.object(dexpaprika, "fetch_pool", return_value={"price_usd": 9.99}):
            market = fetch_pool_market(FakeClient([]), "p1", "1h")
        self.assertEqual(market["price"], 9.99)
        self.assertEqual(market["candle_count"], 0)


if __name__ == "__main__":
    unittest.main()
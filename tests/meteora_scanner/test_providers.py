import unittest

from app.application.meteora_scanner.providers import dlmm


DLMM_SAMPLE = {
    "pool_address": "POOL1",
    "name": "SOL-USDC",
    "pool_type": "dlmm",
    "token_x": {"address": "So11111111111111111111111111111111111111112", "name": "Wrapped SOL",
                "symbol": "SOL", "price": 100.0, "market_cap": 1_000_000_000.0, "fdv": 1_200_000_000.0,
                "holders": 15000, "top_holders_pct": 22.5, "dev_balance_pct": 0.0,
                "has_freeze_authority": False, "has_mint_authority": False},
    "token_y": {"address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "name": "USD Coin",
                "symbol": "USDC", "price": 1.0, "market_cap": 5_000_000.0},
    "tvl": 1_000_000.0,
    "volume": 200_000.0,
    "fee": 5000.0,
    "fee_tvl_ratio": 0.5,
    "fee_pct": 0.04,
    "pool_price": 100.0,
    "pool_price_change_pct": -1.5,
    "open_positions": 123,
    "dlmm_params": {"bin_step": 80, "collect_fee_mode": "both"},
    "pool_created_at": 1711766862000,  # 毫秒
}



class TestProviders(unittest.TestCase):
    def test_dlmm_parse(self):
        rec = dlmm.parse_pool(DLMM_SAMPLE)
        self.assertEqual(rec.pool_type, "DLMM")
        self.assertEqual(rec.pool_address, "POOL1")
        self.assertEqual(rec.token_x.symbol, "SOL")
        self.assertEqual(rec.token_x.market_cap, 1_000_000_000.0)
        self.assertAlmostEqual(rec.fee_tvl_ratio_24h, 0.5, places=6)
        self.assertEqual(rec.volume_24h, 200_000.0)
        self.assertEqual(rec.fees_24h, 5000.0)
        self.assertEqual(rec.fee_rate_pct, 0.04)
        self.assertEqual(rec.bin_step, 80)
        self.assertEqual(rec.open_positions, 123)
        # 毫秒时间戳转秒
        self.assertEqual(rec.created_at, 1711766862)
        # Token Info 面板字段（Meteora 官方一次返回）
        self.assertEqual(rec.token_x.holders, 15000)
        self.assertEqual(rec.token_x.top10_holders, 22.5)
        self.assertEqual(rec.token_x.dev_holders, 0.0)
        self.assertIs(rec.token_x.freeze_auth, False)
        self.assertIs(rec.token_x.mint_auth, False)

    def test_dlmm_freeze_authority_true(self):
        raw = dict(DLMM_SAMPLE)
        raw["token_x"] = dict(DLMM_SAMPLE["token_x"])
        raw["token_x"]["has_freeze_authority"] = True
        rec = dlmm.parse_pool(raw)
        self.assertIs(rec.token_x.freeze_auth, True)

    def test_dlmm_missing_mcap(self):
        raw = dict(DLMM_SAMPLE)
        raw["token_x"] = dict(DLMM_SAMPLE["token_x"])
        raw["token_x"].pop("market_cap")
        raw["token_x"].pop("fdv")
        rec = dlmm.parse_pool(raw)
        self.assertIsNone(rec.token_x.market_cap)
        self.assertIsNone(rec.token_x.best_market_cap())



if __name__ == "__main__":
    unittest.main()

import unittest

from app.application.meteora_scanner import config
from app.application.meteora_scanner.filters import check_integrity, evaluate_pool


def base_row(**kw):
    r = {
        "pool_type": "DLMM",
        "token_x_market_cap": 2_000_000.0,
        "token_y_market_cap": 500_000.0,
        "token_x_fdv": None,
        "token_y_fdv": None,
        "tvl": 100_000.0,
        "volume_24h": 200_000.0,
        "fees_24h": 1000.0,
        "fee_tvl_ratio_24h": 1.0,
        "fee_rate_pct": 0.05,  # Add default fee rate to pass filter
        "bin_step": 80,  # Add default bin_step
        "token_x_holders": 15000,  # Add default holders
        "token_x_top10_holders": 25.0,  # Add default top10
        "token_x_dev_holders": 0.0,  # Add default dev holders
        "token_x_freeze_auth": False,  # Add default freeze auth
        "token_x_mint_auth": False,  # Add default mint auth
        "open_positions": 150,  # Add default open positions
    }
    r.update(kw)
    return r


class TestFilters(unittest.TestCase):
    def setUp(self):
        self.f = dict(config.DEFAULT_FILTERS)

    def test_any_mode_pass(self):
        res = evaluate_pool(base_row(), self.f)
        self.assertTrue(res["passed"])
        self.assertEqual(res["mcap_which"], "x")

    def test_both_mode_fail(self):
        f = dict(self.f, market_cap_mode="both")
        res = evaluate_pool(base_row(), f)
        self.assertFalse(res["mcap_pass"])
        self.assertFalse(res["passed"])

    def test_both_mode_pass(self):
        f = dict(self.f, market_cap_mode="both")
        res = evaluate_pool(base_row(token_y_market_cap=3_000_000.0), f)
        self.assertTrue(res["mcap_pass"])
        self.assertEqual(res["mcap_which"], "both")

    def test_missing_mcap_not_pass_by_default(self):
        res = evaluate_pool(base_row(token_x_market_cap=None, token_y_market_cap=None), self.f)
        self.assertFalse(res["mcap_pass"])
        self.assertIn("市值数据不足", res["reasons"])

    def test_fdv_used_when_mcap_missing(self):
        res = evaluate_pool(base_row(token_x_market_cap=None, token_x_fdv=5_000_000.0), self.f)
        self.assertTrue(res["mcap_pass"])

    def test_volume_threshold(self):
        res = evaluate_pool(base_row(volume_24h=50_000.0), self.f)
        self.assertFalse(res["volume_pass"])

    def test_tvl_threshold(self):
        res = evaluate_pool(base_row(tvl=10_000.0), self.f)
        self.assertFalse(res["tvl_pass"])

    def test_fee_tvl_threshold(self):
        res = evaluate_pool(base_row(fee_tvl_ratio_24h=0.1), self.f)
        self.assertFalse(res["fee_tvl_pass"])

        self.assertFalse(res["passed"])

    def test_integrity_flags_missing(self):
        ok, missing = check_integrity(base_row(tvl=None, token_x_market_cap=None))
        self.assertFalse(ok)
        self.assertIn("tvl", missing)
        self.assertIn("token_x_market_cap", missing)

    def test_bin_step_range_pass(self):
        f = dict(self.f, bin_step_min=5, bin_step_max=10)
        res = evaluate_pool(base_row(bin_step=8), f)
        self.assertTrue(res["bin_step_pass"])
        self.assertTrue(res["passed"])

    def test_bin_step_range_fail(self):
        f = dict(self.f, bin_step_min=5, bin_step_max=10)
        res = evaluate_pool(base_row(bin_step=3), f)
        self.assertFalse(res["bin_step_pass"])
        self.assertFalse(res["passed"])

    def test_non_dlmm_type_is_excluded(self):
        res = evaluate_pool(base_row(pool_type="DAMM v1"), self.f)
        self.assertFalse(res["type_pass"])


    def test_fee_rate_range_pass(self):
        f = dict(self.f, min_fee_rate_pct=0.05, max_fee_rate_pct=0.10)
        res = evaluate_pool(base_row(fee_rate_pct=0.08), f)
        self.assertTrue(res["fee_rate_pass"])
        self.assertTrue(res["passed"])

    def test_fee_rate_range_fail(self):
        f = dict(self.f, min_fee_rate_pct=0.05, max_fee_rate_pct=0.10)
        res = evaluate_pool(base_row(fee_rate_pct=0.03), f)
        self.assertFalse(res["fee_rate_pass"])
        self.assertFalse(res["passed"])

    def test_fee_rate_missing_fail(self):
        f = dict(self.f, min_fee_rate_pct=0.05, max_fee_rate_pct=0.10)
        res = evaluate_pool(base_row(fee_rate_pct=None), f)
        self.assertFalse(res["fee_rate_pass"])
        self.assertIn("费率数据不足", res["reasons"])

    def test_fee_rate_no_limit(self):
        f = dict(self.f, min_fee_rate_pct=0.0, max_fee_rate_pct=0.0)
        res = evaluate_pool(base_row(fee_rate_pct=0.03), f)
        self.assertTrue(res["fee_rate_pass"])

    def test_holders_min_pass(self):
        f = dict(self.f, min_token_x_holders=10000)
        res = evaluate_pool(base_row(token_x_holders=15000), f)
        self.assertTrue(res["holders_pass"])
        self.assertTrue(res["passed"])

    def test_holders_min_fail(self):
        f = dict(self.f, min_token_x_holders=20000)
        res = evaluate_pool(base_row(token_x_holders=15000), f)
        self.assertFalse(res["holders_pass"])
        self.assertFalse(res["passed"])

    def test_top10_holders_pass(self):
        f = dict(self.f, max_token_x_top10_holders=30.0)
        res = evaluate_pool(base_row(token_x_top10_holders=25.0), f)
        self.assertTrue(res["holders_pass"])

    def test_top10_holders_fail(self):
        f = dict(self.f, max_token_x_top10_holders=20.0)
        res = evaluate_pool(base_row(token_x_top10_holders=25.0), f)
        self.assertFalse(res["holders_pass"])

    def test_dev_holders_pass(self):
        f = dict(self.f, max_token_x_dev_holders=0.0)
        res = evaluate_pool(base_row(token_x_dev_holders=0.0), f)
        self.assertTrue(res["holders_pass"])

    def test_dev_holders_fail(self):
        f = dict(self.f, max_token_x_dev_holders=0.0)
        # Create a new row without the default dev_holders
        row = base_row()
        row["token_x_dev_holders"] = 1.0
        res = evaluate_pool(row, f)
        self.assertFalse(res["holders_pass"])

    def test_dev_holders_tiny_value_passes(self):
        # 显示为 0% 的极小真实值（如 0.00002%）不应被误判超限
        res = evaluate_pool(base_row(token_x_dev_holders=0.00002), self.f)
        self.assertTrue(res["holders_pass"])

    def test_dev_holders_clearly_over_fails(self):
        res = evaluate_pool(base_row(token_x_dev_holders=0.5), self.f)
        self.assertFalse(res["holders_pass"])

    def test_auth_freeze_no(self):
        f = dict(self.f, token_x_freeze_auth="no")
        res = evaluate_pool(base_row(token_x_freeze_auth=False), f)
        self.assertTrue(res["holders_pass"])

    def test_auth_mint_no(self):
        f = dict(self.f, token_x_mint_auth="no")
        res = evaluate_pool(base_row(token_x_mint_auth=False), f)
        self.assertTrue(res["holders_pass"])

    def test_auth_any_pass(self):
        f = dict(self.f, token_x_freeze_auth="any", token_x_mint_auth="any")
        res = evaluate_pool(base_row(token_x_freeze_auth=None, token_x_mint_auth=None), f)
        self.assertTrue(res["holders_pass"])

    def test_open_positions_min_pass(self):
        f = dict(self.f, min_open_positions=100)
        res = evaluate_pool(base_row(open_positions=150, token_x_holders=15000), f)
        self.assertTrue(res["open_positions_pass"])
        self.assertTrue(res["passed"])

    def test_open_positions_min_fail(self):
        # 开仓人数来自 Meteora 池发现接口（open_positions），低于下限应淘汰。
        f = dict(self.f, min_open_positions=200)
        res = evaluate_pool(base_row(open_positions=150), f)
        self.assertFalse(res["open_positions_pass"])
        self.assertTrue(res["open_positions_available"])
        self.assertFalse(res["passed"])

    def test_open_positions_data_missing_skipped(self):
        f = dict(self.f, min_open_positions=100)
        res = evaluate_pool(base_row(open_positions=None), f)
        self.assertTrue(res["open_positions_pass"])
        self.assertFalse(res["open_positions_available"])

    def test_auth_text_coercion_pass(self):
        # DLMM 写入为 bool，经 SQLite TEXT 列存储后读回为 '1'/'0' 字符串；
        # 归一化后应正确判定通过（修复 '0' == False 恒为假的 bug）。
        f = dict(self.f, token_x_freeze_auth="no", token_x_mint_auth="no")
        res = evaluate_pool(base_row(token_x_freeze_auth="0", token_x_mint_auth="0"), f)
        self.assertTrue(res["holders_pass"])
        self.assertTrue(res["passed"])

    def test_auth_text_coercion_fail(self):
        f = dict(self.f, token_x_freeze_auth="no")
        res = evaluate_pool(base_row(token_x_freeze_auth="1"), f)
        self.assertFalse(res["holders_pass"])
        self.assertIn("冻结授权不匹配（期望: no）", res["reasons"])

    def test_auth_missing_skipped(self):
        f = dict(self.f, token_x_freeze_auth="no", token_x_mint_auth="no")
        res = evaluate_pool(base_row(token_x_freeze_auth=None, token_x_mint_auth=None), f)
        self.assertTrue(res["holders_pass"])
        self.assertIn("冻结授权数据不足（已跳过）", res["reasons"])

    def test_holders_missing_skipped(self):
        f = dict(self.f, min_token_x_holders=10000)
        res = evaluate_pool(base_row(token_x_holders=None), f)
        self.assertTrue(res["holders_pass"])
        self.assertIn("代币X持仓数数据不足（已跳过）", res["reasons"])

    def test_top10_missing_skipped(self):
        res = evaluate_pool(base_row(token_x_top10_holders=None), self.f)
        self.assertTrue(res["holders_pass"])
        self.assertIn("前10持仓占比数据不足（已跳过）", res["reasons"])

    def test_dev_missing_skipped(self):
        res = evaluate_pool(base_row(token_x_dev_holders=None), self.f)
        self.assertTrue(res["holders_pass"])
        self.assertIn("开发方持仓占比数据不足（已跳过）", res["reasons"])

    def test_open_positions_no_limit(self):
        f = dict(self.f, min_open_positions=0)
        res = evaluate_pool(base_row(open_positions=50), f)
        self.assertTrue(res["open_positions_pass"])

    def test_open_positions_data_missing(self):
        f = dict(self.f, min_open_positions=100)
        res = evaluate_pool(base_row(open_positions=None), f)
        # 现已改为“不可用并跳过”，见 test_open_positions_data_missing_skipped
        self.assertTrue(res["open_positions_pass"])


if __name__ == "__main__":
    unittest.main()

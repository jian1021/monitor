import unittest

from app.application.meteora_scanner.scoring import DEFAULT_WEIGHTS, compute_score, formula_text, log_score, stability_score


class TestScoring(unittest.TestCase):
    def test_log_score_bounds(self):
        self.assertEqual(log_score(0, 1e4, 1e7), 0.0)
        self.assertEqual(log_score(1e3, 1e4, 1e7), 0.0)
        self.assertEqual(log_score(1e7, 1e4, 1e7), 100.0)
        self.assertEqual(log_score(1e9, 1e4, 1e7), 100.0)
        self.assertTrue(0 < log_score(1e5, 1e4, 1e7) < 100)

    def test_log_score_none(self):
        self.assertIsNone(log_score(None, 1, 10))

    def test_score_in_range(self):
        row = {"volume_24h": 1e6, "tvl": 5e5, "fee_tvl_ratio_24h": 1.0}
        sc = compute_score(row, DEFAULT_WEIGHTS)
        self.assertGreaterEqual(sc["score"], 0.0)
        self.assertLessEqual(sc["score"], 100.0)

    def test_missing_components_marked(self):
        row = {"volume_24h": 1e6, "tvl": 5e5, "fee_tvl_ratio_24h": 1.0}
        sc = compute_score(row, DEFAULT_WEIGHTS)
        self.assertIn("stability", sc["missing_components"])
        self.assertTrue(any("稳定性" in n for n in sc["notes"]))

    def test_renormalize_when_missing(self):
        # 三项均达到上限，缺失的稳定性/波动被剔除后应约为 100
        row = {"volume_24h": 1e9, "tvl": 1e9, "fee_tvl_ratio_24h": 50.0}
        sc = compute_score(row, DEFAULT_WEIGHTS)
        self.assertAlmostEqual(sc["score"], 100.0, places=1)

    def test_stability(self):
        self.assertIsNone(stability_score([1, 2]))
        self.assertGreater(stability_score([10, 10, 10, 10]), 99.0)
        self.assertLess(stability_score([1, 100, 1, 100]), 90.0)

    def test_formula_text(self):
        self.assertIn("评分 =", formula_text(DEFAULT_WEIGHTS))


if __name__ == "__main__":
    unittest.main()

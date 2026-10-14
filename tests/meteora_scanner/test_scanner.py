import os
import tempfile
import unittest

from app.application.meteora_scanner import config
from app.application.meteora_scanner.db import Database
from app.application.meteora_scanner.scanner import Scanner


class FakeDlmmClient:
    """游标分页假客户端：按 after_key 返回预置响应（None 表示第一页）。"""

    def __init__(self, pages):
        self.pages = pages
        self.max_retries = 1
        self.request_count = 0

    def get_json(self, path, params=None, full_url=None):
        self.request_count += 1
        cursor = (params or {}).get("after_key")
        return self.pages.get(cursor, {"data": [], "total": self.pages.get("_total", 0)})


class FakeClient:
    """通用空响应客户端，避免真实网络请求。"""

    def __init__(self, responses=None, path="/pools"):
        self.responses = responses or {}
        self.path = path
        self.max_retries = 1
        self.request_count = 0

    def get_json(self, path, params=None, full_url=None):
        self.request_count += 1
        return {"data": [], "total": 0}


def _dlmm_raw(addr):
    return {
        "pool_address": addr, "name": "A-B", "pool_type": "dlmm",
        "token_x": {"address": "MX", "symbol": "A", "market_cap": 2_000_000.0,
                    "holders": 20000, "top_holders_pct": 20.0, "dev_balance_pct": 0.0,
                    "has_freeze_authority": False, "has_mint_authority": False},
        "token_y": {"address": "MY", "symbol": "B", "market_cap": 300_000.0},
        "tvl": 100_000.0, "pool_price": 1.0,
        "volume": 100_000.0, "fee": 500.0,
        "fee_tvl_ratio": 0.5, "fee_pct": 0.04,
        "open_positions": 120,
        "dlmm_params": {"bin_step": 80},
        "pool_created_at": 1711766862000,
    }


class TestScannerDlmm(unittest.TestCase):
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

    def test_pagination_and_dedup(self):
        # 游标分页：第 2 页含重复地址，应按池地址去重
        pages = {
            None: {"total": 3, "data": [_dlmm_raw("P1"), _dlmm_raw("P2")],
                   "after_key": "c1", "has_more": True},
            "c1": {"total": 3, "data": [_dlmm_raw("P2"), _dlmm_raw("P3")],
                   "after_key": None, "has_more": False},
        }
        sc = Scanner(self.db, dlmm_client=FakeDlmmClient(pages))
        prog = sc.scan([config.POOL_TYPE_DLMM], resume=False)
        p = prog[config.POOL_TYPE_DLMM]
        self.assertEqual(p["state"], "done")
        self.assertEqual(p["pages_scanned"], 2)
        self.assertEqual(p["discovered"], 3)   # 去重
        self.assertEqual(p["processed"], 4)
        self.assertEqual(self.db.count_pools().get("DLMM"), 3)

    def test_max_pages_stops(self):
        pages = {None: {"total": 100000, "data": [_dlmm_raw("P1")],
                        "after_key": "c1", "has_more": True}}
        sc = Scanner(self.db, dlmm_client=FakeDlmmClient(pages))
        prog = sc.scan([config.POOL_TYPE_DLMM], resume=False, max_pages=1)
        p = prog[config.POOL_TYPE_DLMM]
        self.assertEqual(p["state"], "stopped")
        self.assertEqual(p["pages_scanned"], 1)

    def test_resume_from_cursor(self):
        # 预置上一次中断的游标
        self.db.update_scan_status(config.POOL_TYPE_DLMM, last_position=1, state="error")
        self.db.set_setting("cursor:DLMM", "c1")
        pages = {"c1": {"total": 3, "data": [_dlmm_raw("P3")],
                        "after_key": None, "has_more": False}}
        sc = Scanner(self.db, dlmm_client=FakeDlmmClient(pages))
        prog = sc.scan([config.POOL_TYPE_DLMM], resume=True)
        p = prog[config.POOL_TYPE_DLMM]
        self.assertEqual(p["state"], "done")
        self.assertEqual(self.db.count_pools().get("DLMM"), 1)


if __name__ == "__main__":
    unittest.main()

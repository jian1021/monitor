# LP 仓位 / 池子价格告警 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 链上建池后，在 Streamlit 界面输入池子地址确认「连接成功」，设定盈利目标与「跌穿池子最低价」两个条件，命中时飞书告警。

**Architecture:** 新建独立模块 `lp_position_alert.py`，一张 `lp_position_alert` 表用 `kind` 判别两种业务形态：`dlmm`（Solana / Meteora DLMM，读链上仓位真实区间 `minPrice`，盈利按真实持仓盈亏 `pnlPctChange`）与 `pool_price`（Robinhood Chain / Uniswap，只有池子价格，盈利按价格涨幅、最低价取历史或手填）。取数全是纯 HTTP（`requests`），无 Solana SDK。解析与判定抽成纯函数（可无网络单测），网络与 DB 各自独立成薄层。

**Tech Stack:** Python 3.12（CI）/ 3.13（本地）、Streamlit、pandas、requests、pytest、Turso(libSQL)、飞书 Webhook。

**依据:** `docs/superpowers/specs/2026-09-15-lp-position-alert-design.md`

## Global Constraints

- **不修改任何既有文件**，唯一例外：`index.py` 末尾的导航列表追加一行 `st.Page(...)` 注册（仅追加，不改逻辑）。
- 只读导入复用，不得改动：`db.py`、`config.py`、`send_feishu_msg.py`。不导入 `dex_client.py`、`monitor_price.py`（保持新旧隔离，即便有重复逻辑）。
- 表名固定 `lp_position_alert`；`kind` 取值仅 `dlmm` | `pool_price`；`target_mode` 取值仅 `pnl_pct` | `price_pct`。
- **Dexscreener 的 Robinhood 链标识是字符串 `robinhood`，不是 `4663`**；传 `4663` 返回 200 + 空数组（静默失败）。
- **所有价格/比率字段在 API 中都是字符串**（`minPrice` / `maxPrice` / `pnlPctChange` / `poolActivePrice` / `priceUsd`），一律经 `to_float()` 转换，禁止直接比较。
- Meteora 基址 `https://dlmm.datapi.meteora.ag`；Dexscreener 基址 `https://api.dexscreener.com`；GeckoTerminal 基址 `https://api.geckoterminal.com/api/v2`。
- 取数失败一律只记日志 + 本轮跳过，**绝不发送飞书**。
- `None` 与 `float` 比较会抛 `TypeError`，空值必须显式短路。
- 测试文件放仓库根目录，命名 `test_lp_position_alert.py`，用 `sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))` 导入，网络用 `@patch("lp_position_alert.requests")` 打桩。
- 模块级必须 `import requests`（否则测试无法 patch）。
- 不新增第三方依赖（pytest 本地已装，不入 `requirements.txt`）。

---

### Task 1: 前置验证 — `minPrice` 单位可比性（阻塞门）

这是 spec 标注的「上线前必做、不得跳过」验证。`minPrice` / `poolActivePrice` 的单位方向我未用真实仓位验证过，若不可比，跌穿告警会**静默失效**（比报错更危险）。本任务不产出提交代码，只产出结论。

**Files:**
- Create: `/tmp/verify_minprice.py`（一次性脚本，不进仓库）

**Interfaces:**
- Consumes: 用户的 Solana 钱包地址、一个用户持有仓位的 Meteora DLMM 池子地址
- Produces: 结论 —— `poolActivePrice` 与 `minPrice` 是否同一计价单位；若否则 `<kind=dlmm>` 的跌穿比较需改写（见 Step 5）

- [ ] **Step 1: 写验证脚本**

```python
import json
import sys
import requests

BASE = "https://dlmm.datapi.meteora.ag"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def main(pool, wallet):
    pool_resp = requests.get(f"{BASE}/pools/{pool}", headers=HEADERS, timeout=20)
    print("GET /pools ->", pool_resp.status_code)
    if pool_resp.status_code != 200:
        print(pool_resp.text[:300])
        return
    p = pool_resp.json()
    print("  name           =", p.get("name"))
    print("  current_price  =", p.get("current_price"))
    print("  token_x        =", (p.get("token_x") or {}).get("symbol"),
          "decimals", (p.get("token_x") or {}).get("decimals"))
    print("  token_y        =", (p.get("token_y") or {}).get("symbol"),
          "decimals", (p.get("token_y") or {}).get("decimals"))

    r = requests.get(
        f"{BASE}/positions/{pool}/pnl",
        params={"user": wallet, "status": "open", "page_size": 100},
        headers=HEADERS, timeout=25)
    print("GET /positions ->", r.status_code)
    if r.status_code != 200:
        print(r.text[:300])
        return
    body = r.json()
    print("  totalCount     =", body.get("totalCount"))
    print("  poolActivePrice(from pool level) =", p.get("current_price"))
    for pos in body.get("positions") or []:
        print("  ---- position", pos.get("positionAddress"))
        for k in ("lowerBinId", "upperBinId", "minPrice", "maxPrice",
                  "poolActivePrice", "poolActiveBinId", "isOutOfRange",
                  "pnlPctChange", "pnlUsd", "isClosed"):
            print(f"     {k:18s} = {pos.get(k)!r}")
        mn = float(pos["minPrice"])
        mx = float(pos["maxPrice"])
        act = pos.get("poolActivePrice")
        print(f"     CHECK min < max        : {mn < mx}")
        print(f"     CHECK active vs [min,max] : "
              f"{'ABOVE' if act and float(act) > mx else 'BELOW' if act and float(act) < mn else 'IN RANGE'}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("用法: python /tmp/verify_minprice.py <pool_address> <wallet>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
```

- [ ] **Step 2: 运行并记录输出**

Run: `python /tmp/verify_minprice.py <你的pool地址> <你的钱包地址>`
Expected: 打印出每个仓位的 `minPrice` / `maxPrice` / `poolActivePrice`。

- [ ] **Step 3: 判定（三条必须全过）**

1. `minPrice < maxPrice`（区间方向正确）
2. `poolActivePrice` 落在 `[minPrice, maxPrice]` 之内或紧邻（说明与区间同尺度、同单位）
3. `poolActivePrice` 与 `/pools` 的 `current_price` 量级一致（说明两个端点同一计价基准）

- [ ] **Step 4: 三条全过则继续**

在 Task 7 中 `kind=dlmm` 的跌穿比较直接用 `active_price <= floor_price`，无需换算。删除 `/tmp/verify_minprice.py`。

- [ ] **Step 5: 若第 2 或第 3 条不成立，停止并回报**

不要继续实现。把原始输出贴回来。此时跌穿比较需要换基准（可能是 `minPrice` 以 token Y 计而 `poolActivePrice` 以 token X 计，需取倒数），或退回用 `isOutOfRange` + `poolActiveBinId < lowerBinId` 组合判定。这是设计变更，需要先更新 spec 再改计划。

- [x] **结果：2026-09-15 通过（本任务已关闭）**

用户提供钱包后，经 `GET /portfolio?user=` 发现该钱包有 **209 个已关闭仓位、0 个开放仓位**；关闭仓位同样返回 `minPrice` / `poolActivePrice`，故取关闭仓位完成验证。

样本：池 `AsSyvUnbfaZJPRrNh3kUuvZTeHKoMVWEoHz86f4Q5D9x`（MET-SOL，bin_step=20），仓位 `H6kbrC3NXtrVP1U9YaSMc5zBEGWPPwBxmjacPwyxCRrL`
`minPrice=0.00210697863166232` / `maxPrice=0.002418426632579231` / `lowerBinId=373` / `upperBinId=442` / `poolActivePrice=0.00204068788709657` / `poolActiveBinId=357` / `isOutOfRange=True`

- 判据 1 `minPrice < maxPrice`：**通过**
- 判据 2 `poolActiveBinId(357) < lowerBinId(373)` 与 `poolActivePrice < minPrice` 同向且与 `isOutOfRange` 一致：**通过**
- 判据 3 **`poolActivePrice / /pools.current_price = 1.000000`**（15 位有效数字完全相同）：**通过**
- 加成校验：`(1.002)^69` 与 `maxPrice/minPrice` 吻合到 1.7e-15；`(1.002)^16` 与 `minPrice/poolActivePrice` 吻合到 2.2e-16

⇒ `poolActivePrice <= floor_price` 比较成立，**Step 5 的失败分支不适用，本关通过**。`units_plausible()` 在此真实数据上返回 `True`。

实测中一并验证的 dlmm 真实取数路径：`fetch_meteora_positions(status=open)` → `[]`（该仓位已关闭），`_cur_dlmm` → `(None, 'closed')`，分类正确。

---

### Task 2: `to_float` + `parse_dlmm_position`（纯函数）

**Files:**
- Create: `lp_position_alert.py`
- Test: `test_lp_position_alert.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `to_float(value) -> float | None`
  - `parse_dlmm_position(pos: dict) -> dict`，返回键：
    `position_address, min_price, max_price, lower_bin_id, upper_bin_id, pnl_pct, active_price, is_out_of_range, is_closed`

- [ ] **Step 1: 写失败测试**

```python
# -*- coding: utf-8 -*-
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lp_position_alert as lpa


def test_to_float_accepts_strings_and_rejects_junk():
    assert lpa.to_float("1.25") == 1.25
    assert lpa.to_float(3) == 3.0
    assert lpa.to_float("1e-6") == pytest.approx(1e-6)
    assert lpa.to_float(None) is None
    assert lpa.to_float("") is None
    assert lpa.to_float("abc") is None


def test_parse_dlmm_position_converts_string_price_fields():
    raw = {
        "positionAddress": "POS1",
        "minPrice": "3.4e-05",
        "maxPrice": "5.2e-05",
        "lowerBinId": 123,
        "upperBinId": 456,
        "pnlPctChange": "12.5",
        "poolActivePrice": "4.1e-05",
        "isOutOfRange": False,
        "isClosed": False,
    }
    out = lpa.parse_dlmm_position(raw)
    assert out["position_address"] == "POS1"
    assert out["min_price"] == pytest.approx(3.4e-05)
    assert out["max_price"] == pytest.approx(5.2e-05)
    assert out["lower_bin_id"] == 123
    assert out["upper_bin_id"] == 456
    assert out["pnl_pct"] == 12.5
    assert out["active_price"] == pytest.approx(4.1e-05)
    assert out["is_out_of_range"] is False
    assert out["is_closed"] is False


def test_parse_dlmm_position_tolerates_missing_optional_fields():
    out = lpa.parse_dlmm_position({"positionAddress": "POS2", "isClosed": True})
    assert out["position_address"] == "POS2"
    assert out["min_price"] is None
    assert out["active_price"] is None
    assert out["is_out_of_range"] is None
    assert out["is_closed"] is True
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest test_lp_position_alert.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lp_position_alert'`

- [ ] **Step 3: 最小实现**

```python
"""lp_position_alert.py — LP 仓位 / 池子价格告警.

两种 kind:
  dlmm       Solana / Meteora DLMM，读链上仓位区间（minPrice）+ 真实持仓盈亏（pnlPctChange）
  pool_price Robinhood Chain / Uniswap，只有池子价格，盈利按价格涨幅、最低价取历史或手填

用法:
  python lp_position_alert.py            # 单轮检查后退出（GitHub Actions cron）
  python lp_position_alert.py --loop     # 本地常驻
"""
import sys
import time
import traceback

import requests

from config import FEISHU_WEBHOOK
from db import get_db_client
from send_feishu_msg import send_feishu_msg

METEORA_BASE = "https://dlmm.datapi.meteora.ag"
DEXSCREENER_BASE = "https://api.dexscreener.com"
GECKO_BASE = "https://api.geckoterminal.com/api/v2"
HEADERS = {"User-Agent": "Mozilla/5.0"}
DEFAULT_INTERVAL = 300

DEXSCREENER_CHAIN = {
    "sol": "solana", "solana": "solana",
    "bsc": "bsc", "base": "base",
    "eth": "ethereum", "robinhood": "robinhood",
}
GECKO_NETWORK = {
    "sol": "solana", "solana": "solana",
    "bsc": "bsc", "base": "base",
    "eth": "eth", "robinhood": "robinhood",
}


def to_float(value):
    try:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_dlmm_position(pos):
    return {
        "position_address": pos.get("positionAddress"),
        "min_price": to_float(pos.get("minPrice")),
        "max_price": to_float(pos.get("maxPrice")),
        "lower_bin_id": pos.get("lowerBinId"),
        "upper_bin_id": pos.get("upperBinId"),
        "pnl_pct": to_float(pos.get("pnlPctChange")),
        "active_price": to_float(pos.get("poolActivePrice")),
        "is_out_of_range": pos.get("isOutOfRange"),
        "is_closed": bool(pos.get("isClosed")),
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest test_lp_position_alert.py -v`
Expected: PASS，3 passed

- [ ] **Step 5: 提交**

```bash
git add lp_position_alert.py test_lp_position_alert.py
git commit -m "feat: 新增 LP 仓位告警模块与 dlmm 仓位解析（含 string->float 转换）"
```

---

### Task 3: `parse_dexscreener_pair` + `floor_from_ohlcv`（纯函数）

**Files:**
- Modify: `lp_position_alert.py`
- Test: `test_lp_position_alert.py`

**Interfaces:**
- Consumes: `to_float`（Task 2）
- Produces:
  - `parse_dexscreener_pair(payload: dict) -> dict | None`，返回键：`price, base_symbol, quote_symbol, liquidity_usd, pair_created_at`；`pairs` 为空时返回 `None`
  - `floor_from_ohlcv(ohlcv_list: list) -> float | None`

- [ ] **Step 1: 写失败测试**

```python
def test_parse_dexscreener_pair_reads_first_pair():
    payload = {"pairs": [{
        "priceUsd": "0.000005140",
        "baseToken": {"symbol": "USDG"},
        "quoteToken": {"symbol": "USDG"},
        "liquidity": {"usd": 123456.78},
        "pairCreatedAt": 1785550187000,
    }]}
    out = lpa.parse_dexscreener_pair(payload)
    assert out["price"] == pytest.approx(5.140e-06)
    assert out["base_symbol"] == "USDG"
    assert out["quote_symbol"] == "USDG"
    assert out["liquidity_usd"] == pytest.approx(123456.78)
    assert out["pair_created_at"] == 1785550187000


def test_parse_dexscreener_pair_returns_none_when_no_pairs():
    assert lpa.parse_dexscreener_pair({"pairs": []}) is None
    assert lpa.parse_dexscreener_pair({}) is None
    assert lpa.parse_dexscreener_pair(None) is None


def test_parse_dexscreener_pair_survives_missing_nested_fields():
    out = lpa.parse_dexscreener_pair({"pairs": [{}]})
    assert out["price"] is None
    assert out["base_symbol"] is None
    assert out["liquidity_usd"] is None


def test_floor_from_ohlcv_takes_min_low_from_newest_first_list():
    ohlcv = [
        [1789462800, 1, 1, 5.14e-06, 5.14e-06, 10],
        [1788562800, 1, 1, 9.40e-06, 9.40e-06, 20],
        [1787662800, 1, 1, 3.43e-06, 3.43e-06, 30],
    ]
    assert lpa.floor_from_ohlcv(ohlcv) == pytest.approx(3.43e-06)


def test_floor_from_ohlcv_returns_none_for_empty_or_malformed():
    assert lpa.floor_from_ohlcv([]) is None
    assert lpa.floor_from_ohlcv(None) is None
    assert lpa.floor_from_ohlcv([["only-two", "cols"]]) is None
    assert lpa.floor_from_ohlcv([[1, 2, 3, "junk", 5, 6]]) is None
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest test_lp_position_alert.py -k "dexscreener or floor" -v`
Expected: FAIL — `AttributeError: module 'lp_position_alert' has no attribute 'parse_dexscreener_pair'`

- [ ] **Step 3: 最小实现**

在 `lp_position_alert.py` 的 `parse_dlmm_position` 之后追加：

```python
def parse_dexscreener_pair(payload):
    pairs = (payload or {}).get("pairs") or []
    if not pairs:
        return None
    p = pairs[0] or {}
    return {
        "price": to_float(p.get("priceUsd")),
        "base_symbol": (p.get("baseToken") or {}).get("symbol"),
        "quote_symbol": (p.get("quoteToken") or {}).get("symbol"),
        "liquidity_usd": to_float((p.get("liquidity") or {}).get("usd")),
        "pair_created_at": p.get("pairCreatedAt"),
    }


def floor_from_ohlcv(ohlcv_list):
    lows = []
    for row in ohlcv_list or []:
        if not row or len(row) < 5:
            continue
        low = to_float(row[3])
        if low is not None:
            lows.append(low)
    return min(lows) if lows else None
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest test_lp_position_alert.py -v`
Expected: PASS，8 passed

- [ ] **Step 5: 提交**

```bash
git add lp_position_alert.py test_lp_position_alert.py
git commit -m "feat: 新增 Dexscreener pair 解析与 OHLCV 最低价纯函数"
```

---

### Task 4: `evaluate` + `needs_rearm`（核心判定逻辑）

**Files:**
- Modify: `lp_position_alert.py`
- Test: `test_lp_position_alert.py`

**Interfaces:**
- Consumes: 无（纯字典进出）
- Produces:
  - `evaluate(rule: dict, cur: dict) -> dict`，返回 `{"target": bool, "floor": bool}`
  - `needs_rearm(rule: dict, cur: dict) -> bool`
  - `units_plausible(active_price: float | None, pool_current_price: float | None, tolerance: float = 100.0) -> bool`

  `cur` 统一形状（`run_once` 负责构造）：键 `pnl_pct`、`active_price`、`price`，缺的填 `None`。
  `kind='dlmm'` 跌穿看 `active_price`；`kind='pool_price'` 跌穿看 `price`。

- [ ] **Step 1: 写失败测试**

```python
def _rule(**over):
    base = {
        "kind": "dlmm", "target_mode": "pnl_pct", "target_pct": 10.0,
        "floor_price": 3.0e-05, "entry_price": None,
        "enable_target_alert": 1, "enable_floor_alert": 1, "rearm": 1,
        "floor_alerted": 0, "target_alerted": 0,
    }
    base.update(over)
    return base


def test_evaluate_dlmm_target_fires_on_pnl_pct():
    cur = {"pnl_pct": 10.5, "active_price": 4.0e-05, "price": None}
    assert lpa.evaluate(_rule(), cur) == {"target": True, "floor": False}


def test_evaluate_dlmm_target_does_not_fire_below_threshold():
    cur = {"pnl_pct": 9.99, "active_price": 4.0e-05, "price": None}
    assert lpa.evaluate(_rule(), cur) == {"target": False, "floor": False}


def test_evaluate_dlmm_floor_fires_when_active_price_at_or_below_floor():
    for act in (3.0e-05, 2.9e-05):
        cur = {"pnl_pct": 0.0, "active_price": act, "price": None}
        assert lpa.evaluate(_rule(target_pct=None), cur) == {"target": False, "floor": True}


def test_evaluate_dlmm_floor_uses_active_price_not_pool_price():
    cur = {"pnl_pct": 0.0, "active_price": 4.0e-05, "price": 1.0e-09}
    assert lpa.evaluate(_rule(target_pct=None), cur)["floor"] is False


def test_evaluate_pool_price_target_uses_entry_snapshot():
    rule = _rule(kind="pool_price", target_mode="price_pct", target_pct=10.0,
                 entry_price=100.0, floor_price=50.0)
    assert lpa.evaluate(rule, {"pnl_pct": None, "active_price": None, "price": 110.0})["target"] is True
    assert lpa.evaluate(rule, {"pnl_pct": None, "active_price": None, "price": 109.99})["target"] is False


def test_evaluate_pool_price_floor_uses_price():
    rule = _rule(kind="pool_price", target_mode="price_pct", target_pct=10.0,
                 entry_price=100.0, floor_price=50.0)
    assert lpa.evaluate(rule, {"price": 50.0})["floor"] is True
    assert lpa.evaluate(rule, {"price": 50.01})["floor"] is False


def test_evaluate_skips_disabled_and_null_triggers():
    cur = {"pnl_pct": 99.0, "active_price": 1.0, "price": 1.0}
    assert lpa.evaluate(_rule(enable_target_alert=0), cur)["target"] is False
    assert lpa.evaluate(_rule(enable_floor_alert=0), cur)["floor"] is False
    assert lpa.evaluate(_rule(target_pct=None), cur)["target"] is False
    assert lpa.evaluate(_rule(floor_price=None), cur)["floor"] is False


def test_evaluate_never_raises_on_missing_current_values():
    assert lpa.evaluate(_rule(), {}) == {"target": False, "floor": False}


def test_evaluate_pool_price_ignores_zero_or_missing_entry():
    rule = _rule(kind="pool_price", target_mode="price_pct", target_pct=10.0, entry_price=0.0)
    assert lpa.evaluate(rule, {"price": 999.0})["target"] is False
    rule2 = _rule(kind="pool_price", target_mode="price_pct", target_pct=10.0, entry_price=None)
    assert lpa.evaluate(rule2, {"price": 999.0})["target"] is False


def test_needs_rearm_only_when_floor_alerted_and_price_recovered():
    rule = _rule(floor_alerted=1, rearm=1)
    assert lpa.needs_rearm(rule, {"active_price": 4.0e-05}) is True
    assert lpa.needs_rearm(rule, {"active_price": 2.0e-05}) is False
    assert lpa.needs_rearm(_rule(floor_alerted=0, rearm=1), {"active_price": 4.0e-05}) is False
    assert lpa.needs_rearm(_rule(floor_alerted=1, rearm=0), {"active_price": 4.0e-05}) is False
    assert lpa.needs_rearm(_rule(floor_alerted=1, rearm=1), {}) is False


def test_units_plausible_accepts_matching_values():
    assert lpa.units_plausible(3.56e-05, 3.56e-05) is True
    assert lpa.units_plausible(3.60e-05, 3.56e-05) is True
    assert lpa.units_plausible(3.9e-04, 3.56e-05) is True


def test_units_plausible_rejects_inverted_units():
    assert lpa.units_plausible(28012.0, 3.56e-05) is False
    assert lpa.units_plausible(3.56e-05, 28012.0) is False


def test_units_plausible_passes_when_data_missing():
    assert lpa.units_plausible(None, 3.56e-05) is True
    assert lpa.units_plausible(3.56e-05, None) is True
    assert lpa.units_plausible(None, None) is True


def test_units_plausible_rejects_nonpositive():
    assert lpa.units_plausible(0.0, 3.56e-05) is False
    assert lpa.units_plausible(3.56e-05, 0.0) is False
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest test_lp_position_alert.py -k "evaluate or rearm" -v`
Expected: FAIL — `AttributeError: module 'lp_position_alert' has no attribute 'evaluate'`

- [ ] **Step 3: 最小实现**

在 `floor_from_ohlcv` 之后追加：

```python
def _current_price(rule, cur):
    if rule.get("kind") == "dlmm":
        return cur.get("active_price")
    return cur.get("price")


def evaluate(rule, cur):
    fires = {"target": False, "floor": False}

    if rule.get("enable_target_alert") and rule.get("target_pct") is not None:
        tgt = float(rule["target_pct"])
        if rule.get("target_mode") == "pnl_pct":
            value = cur.get("pnl_pct")
            if value is not None and value >= tgt:
                fires["target"] = True
        else:
            value = cur.get("price")
            base = rule.get("entry_price")
            if value is not None and base is not None and float(base) > 0:
                # 必须用 (100+tgt)/100 而不是 (1+tgt/100)：后者对 base=100、tgt=10
                # 会算出 110.00000000000001，导致「价格正好 +10%」判定为未达标。
                target_price = float(base) * (100.0 + tgt) / 100.0
                if value >= target_price:
                    fires["target"] = True

    if rule.get("enable_floor_alert") and rule.get("floor_price") is not None:
        value = _current_price(rule, cur)
        if value is not None and value <= float(rule["floor_price"]):
            fires["floor"] = True

    return fires


def needs_rearm(rule, cur):
    if not rule.get("rearm") or not rule.get("floor_alerted"):
        return False
    if rule.get("floor_price") is None:
        return False
    value = _current_price(rule, cur)
    if value is None:
        return False
    return value > float(rule["floor_price"])


def units_plausible(active_price, pool_current_price, tolerance=100.0):
    """校验 poolActivePrice 与池子 current_price 同尺度.

    /pools 的 current_price 已实测为「token Y per token X」，而 poolActivePrice
    同为池子活跃 bin 的价格，两者本应相等。若相差超过 tolerance 倍，说明字段单位
    不一致或解析出错 —— 此时静默比较会让跌穿告警永久失效，必须拒绝判定并报警日志。
    任一侧缺失时返回 True（信息不足，不阻断）。
    """
    if active_price is None or pool_current_price is None:
        return True
    if active_price <= 0 or pool_current_price <= 0:
        return False
    ratio = active_price / pool_current_price
    return (1.0 / tolerance) <= ratio <= tolerance
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest test_lp_position_alert.py -v`
Expected: PASS，22 passed

- [ ] **Step 5: 提交**

```bash
git add lp_position_alert.py test_lp_position_alert.py
git commit -m "feat: 新增告警判定与跌穿重武装纯函数"
```

---

### Task 5: 表 DDL 与 CRUD

**Files:**
- Modify: `lp_position_alert.py`
- Test: `test_lp_position_alert.py`

**Interfaces:**
- Consumes: `get_db_client`（`db.py`，只读导入）
- Produces:
  - `ensure_table() -> bool`
  - `add_rule(rule: dict) -> bool`
  - `load_rules(enabled_only: bool = True, kind: str | None = None) -> list[dict]`（字典键与表列名一致）
  - `delete_rule(rule_id: int) -> bool`
  - `set_enabled(rule_id: int, enabled: bool) -> bool`
  - `reset_alerts(rule_id: int) -> bool`（清空两个标记）
  - `clear_alert_flag(rule_id: int, field: str) -> bool`（`field` ∈ `target_alerted` | `floor_alerted`）
  - `update_runtime(rule_id: int, values: dict) -> bool`
  - `set_status(rule_id: int, status: str) -> bool`
  - `STATUS_OPEN` / `STATUS_CLOSED` / `STATUS_ERROR` 常量

- [ ] **Step 1: 写失败测试**

DB 测试需要 Turso 凭据，缺失时自动 skip（沿用本仓库「凭据已在本机 `.env`」的现实）。

```python
from config import LIBSQL_URL, LIBSQL_TOKEN

requires_db = pytest.mark.skipif(
    not (LIBSQL_URL and LIBSQL_TOKEN), reason="未配置 Turso 凭据"
)


@requires_db
def test_ensure_table_and_crud_roundtrip():
    assert lpa.ensure_table() is True
    rule = {
        "kind": "dlmm", "chain": "sol",
        "pool_address": "TESTPOOL_ROUNDTRIP", "wallet": "TESTWALLET",
        "position_address": "TESTPOS", "pool_name": "TEST/SOL",
        "token_x_symbol": "TEST", "token_y_symbol": "SOL",
        "lower_bin_id": 1, "upper_bin_id": 2,
        "min_price": 1.5e-05, "max_price": 2.5e-05, "floor_price": 1.5e-05,
        "target_mode": "pnl_pct", "target_pct": 10.0, "entry_price": None,
    }
    assert lpa.add_rule(rule) is True
    rows = [r for r in lpa.load_rules(enabled_only=False, kind="dlmm")
            if r["pool_address"] == "TESTPOOL_ROUNDTRIP"]
    assert len(rows) == 1
    rid = rows[0]["id"]
    assert rows[0]["target_pct"] == pytest.approx(10.0)
    assert rows[0]["min_price"] == pytest.approx(1.5e-05)
    assert rows[0]["status"] == lpa.STATUS_OPEN
    assert rows[0]["target_alerted"] is False

    assert lpa.update_runtime(rid, {"last_pnl_pct": 12.5, "last_active_price": 2.0e-05}) is True
    assert lpa.set_status(rid, lpa.STATUS_CLOSED) is True
    assert lpa.clear_alert_flag(rid, "floor_alerted") is True
    assert lpa.set_enabled(rid, False) is True

    after = [r for r in lpa.load_rules(enabled_only=False, kind="dlmm") if r["id"] == rid][0]
    assert after["last_pnl_pct"] == pytest.approx(12.5)
    assert after["status"] == lpa.STATUS_CLOSED
    assert after["enabled"] is False

    # pool_price 规则每轮会把 last_pnl_pct 写成 None，必须确认 None 能正常绑定并回读
    assert lpa.update_runtime(rid, {"last_pnl_pct": None}) is True
    assert [r for r in lpa.load_rules(enabled_only=False, kind="dlmm")
            if r["id"] == rid][0]["last_pnl_pct"] is None

    assert lpa.delete_rule(rid) is True
    assert [r for r in lpa.load_rules(enabled_only=False, kind="dlmm") if r["id"] == rid] == []
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest test_lp_position_alert.py -k roundtrip -v`
Expected: FAIL — `AttributeError: module 'lp_position_alert' has no attribute 'ensure_table'`

- [ ] **Step 3: 最小实现**

在 `needs_rearm` 之后追加：

```python
STATUS_OPEN = "open"
STATUS_CLOSED = "closed"
STATUS_ERROR = "error"

COLUMNS = (
    "kind", "chain", "pool_address", "wallet", "position_address",
    "pool_name", "token_x_symbol", "token_y_symbol",
    "lower_bin_id", "upper_bin_id", "min_price", "max_price", "floor_price",
    "target_mode", "target_pct", "entry_price",
    "enable_target_alert", "enable_floor_alert", "rearm", "enabled",
    "target_alerted", "floor_alerted",
    "last_pnl_pct", "last_active_price", "last_checked_at",
    "is_out_of_range", "status",
)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS lp_position_alert (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    kind                TEXT    NOT NULL DEFAULT 'dlmm',
    chain               TEXT    NOT NULL DEFAULT 'sol',
    pool_address        TEXT    NOT NULL,
    wallet              TEXT,
    position_address    TEXT,
    pool_name           TEXT,
    token_x_symbol      TEXT,
    token_y_symbol      TEXT,
    lower_bin_id        INTEGER,
    upper_bin_id        INTEGER,
    min_price           REAL,
    max_price           REAL,
    floor_price         REAL,
    target_mode         TEXT    NOT NULL DEFAULT 'pnl_pct',
    target_pct          REAL,
    entry_price         REAL,
    enable_target_alert INTEGER NOT NULL DEFAULT 1,
    enable_floor_alert  INTEGER NOT NULL DEFAULT 1,
    rearm               INTEGER NOT NULL DEFAULT 1,
    enabled             INTEGER NOT NULL DEFAULT 1,
    target_alerted      INTEGER NOT NULL DEFAULT 0,
    floor_alerted       INTEGER NOT NULL DEFAULT 0,
    last_pnl_pct        REAL,
    last_active_price   REAL,
    last_checked_at     TEXT,
    is_out_of_range     INTEGER,
    status              TEXT    NOT NULL DEFAULT 'open',
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_lp_position_alert_enabled "
    "ON lp_position_alert(enabled, status);"
)


def _row_to_rule(row):
    rule = {name: row[i] for i, name in enumerate(COLUMNS)}
    rule["id"] = row[len(COLUMNS)]
    for flag in ("enable_target_alert", "enable_floor_alert", "rearm",
                 "enabled", "target_alerted", "floor_alerted"):
        rule[flag] = bool(rule[flag])
    if rule.get("is_out_of_range") is not None:
        rule["is_out_of_range"] = bool(rule["is_out_of_range"])
    for numeric in ("min_price", "max_price", "floor_price", "target_pct",
                    "entry_price", "last_pnl_pct", "last_active_price"):
        if rule.get(numeric) is not None:
            rule[numeric] = float(rule[numeric])
    return rule


def _execute(sql, params=None, fetch=False):
    client = get_db_client()
    if not client:
        print("❌ 无法连接数据库")
        return None
    try:
        res = client.execute(sql, params or [])
        return res.rows if fetch else True
    except Exception as e:
        print(f"❌ SQL 执行失败: {e}")
        return None
    finally:
        client.close()


def ensure_table():
    client = get_db_client()
    if not client:
        print("❌ 无法连接数据库，跳过建表")
        return False
    try:
        client.batch([CREATE_TABLE_SQL, CREATE_INDEX_SQL])
        return True
    except Exception as e:
        print(f"❌ 建表失败: {e}")
        return False
    finally:
        client.close()


def add_rule(rule):
    cols = [c for c in COLUMNS if c in rule]
    placeholders = ", ".join("?" for _ in cols)
    sql = (f"INSERT INTO lp_position_alert ({', '.join(cols)}) "
           f"VALUES ({placeholders})")
    return _execute(sql, [rule[c] for c in cols]) is True


def load_rules(enabled_only=True, kind=None):
    cols = ", ".join(COLUMNS)
    sql = f"SELECT {cols}, id FROM lp_position_alert"
    where, params = [], []
    if enabled_only:
        where.append("enabled = 1")
        where.append("status = ?")
        params.append(STATUS_OPEN)
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id"
    rows = _execute(sql, params, fetch=True)
    return [_row_to_rule(r) for r in (rows or [])]


def delete_rule(rule_id):
    return _execute("DELETE FROM lp_position_alert WHERE id = ?", [rule_id]) is True


def set_enabled(rule_id, enabled):
    return _execute("UPDATE lp_position_alert SET enabled = ? WHERE id = ?",
                    [1 if enabled else 0, rule_id]) is True


def reset_alerts(rule_id):
    return _execute(
        "UPDATE lp_position_alert SET target_alerted = 0, floor_alerted = 0 WHERE id = ?",
        [rule_id]) is True


def clear_alert_flag(rule_id, field):
    if field not in ("target_alerted", "floor_alerted"):
        raise ValueError(f"非法字段: {field}")
    return _execute(f"UPDATE lp_position_alert SET {field} = 0 WHERE id = ?",
                    [rule_id]) is True


def set_status(rule_id, status):
    return _execute("UPDATE lp_position_alert SET status = ? WHERE id = ?",
                    [status, rule_id]) is True


def update_runtime(rule_id, values):
    cols = [c for c in values if c in COLUMNS]
    if not cols:
        return False
    sets = ", ".join(f"{c} = ?" for c in cols) + ", last_checked_at = datetime('now')"
    return _execute(f"UPDATE lp_position_alert SET {sets} WHERE id = ?",
                    [values[c] for c in cols] + [rule_id]) is True


def mark_alerted(rule_id, field):
    if field not in ("target_alerted", "floor_alerted"):
        raise ValueError(f"非法字段: {field}")
    return _execute(
        f"UPDATE lp_position_alert SET {field} = 1, "
        f"last_checked_at = datetime('now') WHERE id = ?",
        [rule_id]) is True
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest test_lp_position_alert.py -v`
Expected: PASS，23 passed（若本机无凭据则该条为 skipped）

- [ ] **Step 5: 提交**

```bash
git add lp_position_alert.py test_lp_position_alert.py
git commit -m "feat: 新增 lp_position_alert 表 DDL 与 CRUD"
```

---

### Task 6: 网络层取数

**Files:**
- Modify: `lp_position_alert.py`
- Test: `test_lp_position_alert.py`

**Interfaces:**
- Consumes: `to_float`、`parse_dlmm_position`、`parse_dexscreener_pair`、`floor_from_ohlcv`
- Produces:
  - `http_get_json(url, params=None, timeout=20) -> dict | list | None`
  - `fetch_meteora_pool(pool_address: str) -> dict | None`，返回键：`name, current_price, token_x_symbol, token_y_symbol, tvl, is_blacklisted, created_at`
  - `fetch_meteora_positions(pool_address: str, wallet: str) -> list[dict] | None`（元素为 `parse_dlmm_position` 结果）
  - `fetch_dexscreener_pair(chain: str, pool_address: str) -> dict | None`（`parse_dexscreener_pair` 结果）
  - `fetch_pool_floor(chain: str, pool_address: str) -> float | None`（GeckoTerminal OHLCV 求 `min(low)`）

- [ ] **Step 1: 写失败测试**

```python
def _mock_response(json_data, status=200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = json_data
    return m


@patch("lp_position_alert.requests")
def test_fetch_meteora_pool(mock_requests):
    mock_requests.get.return_value = _mock_response({
        "name": "PUMP-SOL", "current_price": 3.56e-05,
        "token_x": {"symbol": "PUMP", "decimals": 6},
        "token_y": {"symbol": "SOL", "decimals": 9},
        "tvl": 1317645.9, "is_blacklisted": False, "created_at": 1752335747,
    })
    out = lpa.fetch_meteora_pool("POOL1")
    assert out["name"] == "PUMP-SOL"
    assert out["current_price"] == pytest.approx(3.56e-05)
    assert out["token_x_symbol"] == "PUMP"
    assert out["token_y_symbol"] == "SOL"
    assert out["is_blacklisted"] is False
    called = mock_requests.get.call_args[0][0]
    assert called.endswith("/pools/POOL1")


@patch("lp_position_alert.requests")
def test_fetch_meteora_pool_returns_none_on_http_error(mock_requests):
    mock_requests.get.return_value = _mock_response({}, 500)
    assert lpa.fetch_meteora_pool("POOL1") is None


@patch("lp_position_alert.requests")
def test_fetch_meteora_positions_parses_list(mock_requests):
    mock_requests.get.return_value = _mock_response({"positions": [{
        "positionAddress": "POS1", "minPrice": "1.5e-05", "maxPrice": "2.5e-05",
        "lowerBinId": 10, "upperBinId": 20, "pnlPctChange": "8.0",
        "poolActivePrice": "2.0e-05", "isOutOfRange": False, "isClosed": False,
    }], "totalCount": 1})
    out = lpa.fetch_meteora_positions("POOL1", "WALLET1")
    assert len(out) == 1
    assert out[0]["position_address"] == "POS1"
    assert out[0]["pnl_pct"] == 8.0
    _, kwargs = mock_requests.get.call_args
    assert kwargs["params"]["user"] == "WALLET1"


@patch("lp_position_alert.requests")
def test_fetch_meteora_positions_returns_none_on_http_error(mock_requests):
    mock_requests.get.return_value = _mock_response({}, 400)
    assert lpa.fetch_meteora_positions("POOL1", "W1") is None


@patch("lp_position_alert.requests")
def test_fetch_dexscreener_pair_maps_chain_and_parses(mock_requests):
    mock_requests.get.return_value = _mock_response({"pairs": [{
        "priceUsd": "5.14e-06",
        "baseToken": {"symbol": "USDG"}, "quoteToken": {"symbol": "USDG"},
        "liquidity": {"usd": 1000}, "pairCreatedAt": 1,
    }]})
    out = lpa.fetch_dexscreener_pair("robinhood", "0xPAIR")
    assert out["price"] == pytest.approx(5.14e-06)
    called = mock_requests.get.call_args[0][0]
    assert "/pairs/robinhood/0xPAIR" in called
    assert "/pairs/4663/" not in called


@patch("lp_position_alert.requests")
def test_fetch_pool_floor_from_ohlcv(mock_requests):
    mock_requests.get.return_value = _mock_response({"data": {"attributes": {
        "ohlcv_list": [[300, 1, 1, 5.0e-06, 5.0e-06, 1],
                       [200, 1, 1, 3.0e-06, 3.0e-06, 1],
                       [100, 1, 1, 4.0e-06, 4.0e-06, 1]]}}})
    assert lpa.fetch_pool_floor("robinhood", "0xPAIR") == pytest.approx(3.0e-06)
    called = mock_requests.get.call_args[0][0]
    assert "/networks/robinhood/pools/0xPAIR/ohlcv/day" in called


@patch("lp_position_alert.requests")
def test_http_get_json_returns_none_on_network_exception(mock_requests):
    mock_requests.get.side_effect = RuntimeError("boom")
    assert lpa.http_get_json("https://example.invalid") is None
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest test_lp_position_alert.py -k "fetch or http_get" -v`
Expected: FAIL — `AttributeError: module 'lp_position_alert' has no attribute 'fetch_meteora_pool'`

- [ ] **Step 3: 最小实现**

在 `mark_alerted` 之后追加：

```python
def http_get_json(url, params=None, timeout=20):
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        print(f"⚠️ HTTP {resp.status_code}: {url}")
    except Exception as e:
        print(f"⚠️ 请求异常: {url} -> {e}")
    return None


def fetch_meteora_pool(pool_address):
    data = http_get_json(f"{METEORA_BASE}/pools/{pool_address}")
    if not data:
        return None
    x = data.get("token_x") or {}
    y = data.get("token_y") or {}
    return {
        "name": data.get("name"),
        "current_price": to_float(data.get("current_price")),
        "token_x_symbol": x.get("symbol"),
        "token_y_symbol": y.get("symbol"),
        "tvl": to_float(data.get("tvl")),
        "is_blacklisted": data.get("is_blacklisted"),
        "created_at": data.get("created_at"),
    }


def fetch_meteora_positions(pool_address, wallet):
    data = http_get_json(
        f"{METEORA_BASE}/positions/{pool_address}/pnl",
        params={"user": wallet, "status": "open", "page_size": 100},
        timeout=25,
    )
    if not isinstance(data, dict):
        return None
    return [parse_dlmm_position(p) for p in (data.get("positions") or [])]


def _dexscreener_chain(chain):
    return DEXSCREENER_CHAIN.get(str(chain or "").strip().lower(), str(chain).strip().lower())


def _gecko_network(chain):
    return GECKO_NETWORK.get(str(chain or "").strip().lower(), str(chain).strip().lower())


def fetch_dexscreener_pair(chain, pool_address):
    data = http_get_json(f"{DEXSCREENER_BASE}/latest/dex/pairs/{_dexscreener_chain(chain)}/{pool_address}")
    return parse_dexscreener_pair(data)


def fetch_pool_floor(chain, pool_address):
    data = http_get_json(
        f"{GECKO_BASE}/networks/{_gecko_network(chain)}/pools/{pool_address}/ohlcv/day",
        params={"limit": 100}, timeout=25,
    )
    if not isinstance(data, dict):
        return None
    attrs = (data.get("data") or {}).get("attributes") or {}
    return floor_from_ohlcv(attrs.get("ohlcv_list") or [])
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest test_lp_position_alert.py -v`
Expected: PASS，30 passed

- [ ] **Step 5: 提交**

```bash
git add lp_position_alert.py test_lp_position_alert.py
git commit -m "feat: 新增 Meteora/Dexscreener/GeckoTerminal 取数层"
```

---

### Task 7: `run_once` + 飞书告警 + 去重/重武装 + CLI

**Files:**
- Modify: `lp_position_alert.py`
- Test: `test_lp_position_alert.py`

**Interfaces:**
- Consumes: `load_rules`、`update_runtime`、`mark_alerted`、`clear_alert_flag`、`set_status`、`evaluate`、`needs_rearm`、`fetch_meteora_positions`、`fetch_dexscreener_pair`、`send_feishu_msg`
- Produces:
  - `check_rule(rule: dict) -> dict`，返回 `{"status": str, "fired": list[str], "cur": dict|None, "message": str|None}`
  - `run_once() -> None`
  - `build_message(rule: dict, cur: dict, fired: list[str], pool_info: dict | None) -> str`
  - `run_loop(interval: int) -> None`
  - `main() -> None`

- [ ] **Step 1: 写失败测试**

```python
def _db_rule(**over):
    base = {
        "id": 1, "kind": "dlmm", "chain": "sol",
        "pool_address": "POOL1", "wallet": "WALLET1", "position_address": "POS1",
        "pool_name": "PUMP-SOL", "token_x_symbol": "PUMP", "token_y_symbol": "SOL",
        "min_price": 1.5e-05, "max_price": 2.5e-05, "floor_price": 1.5e-05,
        "target_mode": "pnl_pct", "target_pct": 10.0, "entry_price": None,
        "enable_target_alert": 1, "enable_floor_alert": 1, "rearm": 1,
        "target_alerted": 0, "floor_alerted": 0, "status": "open",
    }
    base.update(over)
    return base


@patch("lp_position_alert.fetch_meteora_pool")
@patch("lp_position_alert.send_feishu_msg")
@patch("lp_position_alert.update_runtime")
@patch("lp_position_alert.mark_alerted")
@patch("lp_position_alert.clear_alert_flag")
@patch("lp_position_alert.set_status")
@patch("lp_position_alert.fetch_meteora_positions")
def test_run_once_dlmm_fires_target_and_floor_independently(
        mock_pos, mock_status, mock_clear, mock_mark, mock_update, mock_send, mock_pool):
    mock_pos.return_value = [{
        "position_address": "POS1", "min_price": 1.5e-05, "max_price": 2.5e-05,
        "lower_bin_id": 1, "upper_bin_id": 2, "pnl_pct": 25.0,
        "active_price": 1.0e-05, "is_out_of_range": True, "is_closed": False,
    }]
    mock_pool.return_value = {"name": "PUMP-SOL", "current_price": 1.0e-05}
    with patch("lp_position_alert.load_rules", return_value=[_db_rule()]):
        lpa.run_once()

    assert mock_send.call_count == 1, "两个条件都触发时应合成一条消息，而不是两条"
    msg = mock_send.call_args[0][1]
    assert "盈利" in msg and "跌穿" in msg
    marked = {c[0][1] for c in mock_mark.call_args_list}
    assert marked == {"target_alerted", "floor_alerted"}, "两个标记位必须各自落库"


@patch("lp_position_alert.fetch_meteora_pool")
@patch("lp_position_alert.send_feishu_msg")
@patch("lp_position_alert.update_runtime")
@patch("lp_position_alert.mark_alerted")
@patch("lp_position_alert.clear_alert_flag")
@patch("lp_position_alert.set_status")
@patch("lp_position_alert.fetch_meteora_positions")
def test_run_once_dlmm_marks_closed_position(
        mock_pos, mock_status, mock_clear, mock_mark, mock_update, mock_send, mock_pool):
    mock_pos.return_value = [{
        "position_address": "POS1", "min_price": None, "max_price": None,
        "lower_bin_id": None, "upper_bin_id": None, "pnl_pct": None,
        "active_price": None, "is_out_of_range": None, "is_closed": True,
    }]
    mock_pool.return_value = {"name": "PUMP-SOL"}
    with patch("lp_position_alert.load_rules", return_value=[_db_rule()]):
        lpa.run_once()

    assert mock_status.call_args[0][1] == lpa.STATUS_CLOSED
    assert mock_send.call_count == 1
    assert "已关闭" in mock_send.call_args[0][1]


@patch("lp_position_alert.fetch_meteora_pool")
@patch("lp_position_alert.send_feishu_msg")
@patch("lp_position_alert.update_runtime")
@patch("lp_position_alert.mark_alerted")
@patch("lp_position_alert.clear_alert_flag")
@patch("lp_position_alert.set_status")
@patch("lp_position_alert.fetch_meteora_positions")
def test_run_once_never_alerts_when_fetch_fails(
        mock_pos, mock_status, mock_clear, mock_mark, mock_update, mock_send, mock_pool):
    mock_pos.return_value = None
    with patch("lp_position_alert.load_rules", return_value=[_db_rule()]):
        lpa.run_once()
    assert mock_send.call_count == 0, "取数失败绝不能告警"
    assert mock_status.call_args[0][1] == lpa.STATUS_ERROR


@patch("lp_position_alert.fetch_dexscreener_pair")
@patch("lp_position_alert.send_feishu_msg")
@patch("lp_position_alert.update_runtime")
@patch("lp_position_alert.mark_alerted")
@patch("lp_position_alert.clear_alert_flag")
@patch("lp_position_alert.set_status")
def test_run_once_pool_price_fires_price_target(
        mock_status, mock_clear, mock_mark, mock_update, mock_send, mock_pair):
    mock_pair.return_value = {"price": 110.0, "base_symbol": "AAPL", "quote_symbol": "USDG",
                              "liquidity_usd": 1.0, "pair_created_at": 1}
    rule = _db_rule(kind="pool_price", chain="robinhood", wallet=None,
                    position_address=None, target_mode="price_pct",
                    target_pct=10.0, entry_price=100.0, floor_price=50.0)
    with patch("lp_position_alert.load_rules", return_value=[rule]):
        lpa.run_once()
    assert mock_send.call_count == 1
    assert mock_mark.call_args[0][1] == "target_alerted"


@patch("lp_position_alert.fetch_meteora_pool")
@patch("lp_position_alert.send_feishu_msg")
@patch("lp_position_alert.update_runtime")
@patch("lp_position_alert.mark_alerted")
@patch("lp_position_alert.clear_alert_flag")
@patch("lp_position_alert.set_status")
@patch("lp_position_alert.fetch_meteora_positions")
def test_run_once_rearms_floor_after_recovery(
        mock_pos, mock_status, mock_clear, mock_mark, mock_update, mock_send, mock_pool):
    mock_pos.return_value = [{
        "position_address": "POS1", "min_price": 1.5e-05, "max_price": 2.5e-05,
        "lower_bin_id": 1, "upper_bin_id": 2, "pnl_pct": 0.0,
        "active_price": 3.0e-05, "is_out_of_range": False, "is_closed": False,
    }]
    mock_pool.return_value = {"name": "PUMP-SOL"}
    with patch("lp_position_alert.load_rules",
               return_value=[_db_rule(floor_alerted=1, target_pct=None)]):
        lpa.run_once()
    assert mock_clear.call_args[0][1] == "floor_alerted"
    assert mock_send.call_count == 0, "价格回升不是告警，只是重置标记"


@patch("lp_position_alert.fetch_meteora_pool")
@patch("lp_position_alert.send_feishu_msg")
@patch("lp_position_alert.update_runtime")
@patch("lp_position_alert.mark_alerted")
@patch("lp_position_alert.clear_alert_flag")
@patch("lp_position_alert.set_status")
@patch("lp_position_alert.fetch_meteora_positions")
def test_run_once_dlmm_suppresses_floor_on_unit_mismatch(
        mock_pos, mock_status, mock_clear, mock_mark, mock_update, mock_send, mock_pool):
    mock_pos.return_value = [{
        "position_address": "POS1", "min_price": 1.5e-05, "max_price": 2.5e-05,
        "lower_bin_id": 1, "upper_bin_id": 2, "pnl_pct": 0.0,
        "active_price": 28012.0, "is_out_of_range": True, "is_closed": False,
    }]
    mock_pool.return_value = {"name": "PUMP-SOL", "current_price": 3.56e-05}
    with patch("lp_position_alert.load_rules",
               return_value=[_db_rule(target_pct=None, floor_price=1.5e-05)]):
        lpa.run_once()
    assert mock_send.call_count == 0, "单位不一致必须拒绝判定，不能误报跌穿"
    assert mock_mark.call_count == 0


def test_build_message_contains_actionable_fields():
    rule = _db_rule()
    cur = {"pnl_pct": 25.0, "active_price": 1.0e-05, "min_price": 1.5e-05,
           "max_price": 2.5e-05, "price": None}
    msg = lpa.build_message(rule, cur, ["target", "floor"], {"name": "PUMP-SOL"})
    assert "PUMP-SOL" in msg
    assert "POS1" in msg
    assert "25.00" in msg
    assert "meteora" in msg.lower()
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest test_lp_position_alert.py -k "run_once or rearm_after or build_message" -v`
Expected: FAIL — `AttributeError: module 'lp_position_alert' has no attribute 'run_once'`

- [ ] **Step 3: 最小实现**

在 `fetch_pool_floor` 之后追加：

```python
def _cur_dlmm(rule):
    positions = fetch_meteora_positions(rule["pool_address"], rule["wallet"])
    if positions is None:
        return None, STATUS_ERROR
    for pos in positions:
        if pos.get("position_address") == rule.get("position_address"):
            if pos.get("is_closed"):
                return None, STATUS_CLOSED
            return pos, STATUS_OPEN
    return None, STATUS_CLOSED


def _cur_pool_price(rule):
    pair = fetch_dexscreener_pair(rule["chain"], rule["pool_address"])
    if pair is None or pair.get("price") is None:
        return None, STATUS_ERROR
    return pair, STATUS_OPEN


def build_message(rule, cur, fired, pool_info):
    name = (pool_info or {}).get("name") or rule.get("pool_name") or rule["pool_address"]
    lines = []
    if "target" in fired:
        if rule.get("target_mode") == "pnl_pct":
            lines.append(f"🎯 【盈利达标】真实持仓盈亏 {cur.get('pnl_pct'):.2f}% "
                         f"（目标 {float(rule['target_pct']):.2f}%）")
        else:
            base = float(rule["entry_price"])
            pct = (cur["price"] / base - 1) * 100 if base else 0.0
            lines.append(f"🎯 【盈利达标】现价 {cur['price']:.10g}，较登记价 {base:.10g} "
                         f"涨 {pct:.2f}%（目标 {float(rule['target_pct']):.2f}%）")
    if "floor" in fired:
        price = _current_price(rule, cur)
        lines.append(f"🚨 【价格跌穿】现价 {price:.10g} ≤ 阈值 {float(rule['floor_price']):.10g}")
    lines.append("")
    lines.append(f"池子: {name} ({rule['chain']})")
    lines.append(f"池子地址: {rule['pool_address']}")
    if rule.get("position_address"):
        lines.append(f"仓位地址: {rule['position_address']}")
    if rule.get("position_address"):
        lines.append(f"仓位区间: bin {rule.get('lower_bin_id')} ~ {rule.get('upper_bin_id')}")
    if rule["kind"] == "dlmm":
        lines.append(f"链接: https://app.meteora.ag/dlmm/{rule['pool_address']}")
    else:
        lines.append(f"链接: https://dexscreener.com/{_dexscreener_chain(rule['chain'])}/{rule['pool_address']}")
    return "\n".join(lines)


def check_rule(rule):
    pool_info = None
    if rule["kind"] == "dlmm":
        pool_info = fetch_meteora_pool(rule["pool_address"])
        cur, status = _cur_dlmm(rule)
        if status == STATUS_CLOSED:
            name = rule.get("pool_name") or rule["pool_address"]
            return {
                "status": STATUS_CLOSED,
                "fired": ["closed"],
                "cur": None,
                "message": (f"ℹ️ 【仓位已关闭】{name}\n"
                            f"仓位地址: {rule.get('position_address')}\n"
                            f"该仓位已不在开放列表中，规则自动停用。"),
            }
    else:
        cur, status = _cur_pool_price(rule)
        if cur is not None:
            pool_info = {"name": " / ".join(
                filter(None, [cur.get("base_symbol"), cur.get("quote_symbol")]))}

    if cur is None:
        return {"status": status, "fired": [], "cur": None, "message": None}

    cur = dict(cur)
    if rule["kind"] == "dlmm":
        cur["price"] = None
        if not units_plausible(cur.get("active_price"),
                               (pool_info or {}).get("current_price")):
            print(f"⚠️ 规则 {rule['id']} 单位校验未通过: poolActivePrice="
                  f"{cur.get('active_price')} vs pool current_price="
                  f"{(pool_info or {}).get('current_price')}；"
                  f"本轮跳过跌穿判定（不告警），请人工核对 minPrice 单位。")
            rule = dict(rule)
            rule["enable_floor_alert"] = 0
    else:
        cur["pnl_pct"] = None
        cur["active_price"] = None

    fired = [k for k, v in evaluate(rule, cur).items() if v]
    if rule.get("target_alerted") and "target" in fired:
        fired.remove("target")
    if rule.get("floor_alerted") and "floor" in fired:
        fired.remove("floor")

    message = None
    if fired:
        message = build_message(rule, cur, fired, pool_info)

    if needs_rearm(rule, cur):
        clear_alert_flag(rule["id"], "floor_alerted")

    return {"status": STATUS_OPEN, "fired": fired, "cur": cur, "message": message}


RUN_TIME_FIELDS = ("min_price", "max_price", "last_pnl_pct", "last_active_price")


def run_once():
    rules = load_rules(enabled_only=True)
    if not rules:
        print("ℹ️ 没有启用的 LP 告警规则")
        return

    print(f"🔍 本轮检查 {len(rules)} 条 LP 告警规则 ...")
    ok = fail = 0
    for rule in rules:
        try:
            result = check_rule(rule)
        except Exception:
            print(f"❌ 规则 {rule['id']} 检查异常")
            traceback.print_exc()
            fail += 1
            continue

        cur = result["cur"]
        if result["status"] == STATUS_ERROR:
            print(f"⚠️ 规则 {rule['id']} 取数失败，跳过（不告警）")
            set_status(rule["id"], STATUS_ERROR)
            fail += 1
            continue

        ok += 1
        values = {"status": result["status"]}
        if cur:
            if cur.get("min_price") is not None:
                values["min_price"] = cur["min_price"]
            if cur.get("max_price") is not None:
                values["max_price"] = cur["max_price"]
            values["last_pnl_pct"] = cur.get("pnl_pct")
            values["last_active_price"] = _current_price(rule, cur)
            if cur.get("is_out_of_range") is not None:
                values["is_out_of_range"] = 1 if cur["is_out_of_range"] else 0
        update_runtime(rule["id"], values)

        if result["fired"]:
            print(f"🚨 规则 {rule['id']} 触发: {result['fired']}")
            text = result["message"]
            if text:
                send_feishu_msg(FEISHU_WEBHOOK, text)
            for name in ("target", "floor"):
                if name in result["fired"]:
                    mark_alerted(rule["id"], f"{name}_alerted")
            if "closed" in result["fired"]:
                set_status(rule["id"], STATUS_CLOSED)
                set_enabled(rule["id"], False)

    print(f"📊 本轮完成: 成功 {ok}，失败 {fail}")


def run_loop(interval):
    print(f"🔁 常驻监控模式: 每 {interval} 秒检查一次 (Ctrl+C 退出)")
    while True:
        try:
            run_once()
        except KeyboardInterrupt:
            print("\n👋 已退出常驻监控")
            break
        except Exception:
            traceback.print_exc()
        time.sleep(interval)


def main():
    ensure_table()
    args = sys.argv[1:]
    if "--loop" in args:
        interval = DEFAULT_INTERVAL
        if "--interval" in args:
            idx = args.index("--interval")
            if len(args) > idx + 1:
                interval = int(args[idx + 1])
        run_loop(interval)
    else:
        run_once()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest test_lp_position_alert.py -v`
Expected: PASS，37 passed

- [ ] **Step 5: 提交**

```bash
git add lp_position_alert.py test_lp_position_alert.py
git commit -m "feat: 新增 run_once 告警循环、飞书推送与去重重武装"
```

---

### Task 8: 界面 Tab 1 — Solana 连接 + 列仓位 + 设阈值

**Files:**
- Create: `pages/lp_position_alert.py`
- Modify: `lp_position_alert.py`（追加供页面用的 `preview_dlmm(pool_address, wallet)` 帮助函数）
- Test: `test_lp_position_alert.py`

**Interfaces:**
- Consumes: `ensure_table`、`add_rule`、`fetch_meteora_pool`、`fetch_meteora_positions`、`load_rules`、`set_enabled`、`reset_alerts`、`delete_rule`
- Produces:
  - `preview_dlmm(pool_address: str, wallet: str) -> dict`，返回 `{"ok": bool, "error": str | None, "pool": dict | None, "positions": list[dict]}`
  - `CHAIN_OPTIONS = ["sol"]`、`POOL_PRICE_CHAINS = ["robinhood", "bsc", "base", "eth"]`

  `preview_dlmm` 单独抽出来是因为 Tab 1 的「连接」按钮需要「先验池子、再列仓位、两种失败要能区分」的原子操作，Streamlit 回调里不好内联断言。

- [ ] **Step 1: 写失败测试**

```python
@patch("lp_position_alert.fetch_meteora_positions")
@patch("lp_position_alert.fetch_meteora_pool")
def test_preview_dlmm_reports_bad_pool(mock_pool, mock_pos):
    mock_pool.return_value = None
    out = lpa.preview_dlmm("BADPOOL", "WALLET1")
    assert out["ok"] is False
    assert "连接失败" in out["error"]
    assert out["pool"] is None
    mock_pos.assert_not_called()


@patch("lp_position_alert.fetch_meteora_positions")
@patch("lp_position_alert.fetch_meteora_pool")
def test_preview_dlmm_reports_no_positions(mock_pool, mock_pos):
    mock_pool.return_value = {"name": "PUMP-SOL", "current_price": 1.0}
    mock_pos.return_value = []
    out = lpa.preview_dlmm("POOL1", "WALLET1")
    assert out["ok"] is False
    assert "没有开放仓位" in out["error"]
    assert out["pool"]["name"] == "PUMP-SOL"


@patch("lp_position_alert.fetch_meteora_positions")
@patch("lp_position_alert.fetch_meteora_pool")
def test_preview_dlmm_reports_positions_on_success(mock_pool, mock_pos):
    mock_pool.return_value = {"name": "PUMP-SOL", "current_price": 1.0,
                              "token_x_symbol": "PUMP", "token_y_symbol": "SOL",
                              "tvl": 2.0, "is_blacklisted": False}
    mock_pos.return_value = [{"position_address": "POS1", "min_price": 1.5e-05,
                              "max_price": 2.5e-05, "lower_bin_id": 1,
                              "upper_bin_id": 2, "pnl_pct": 5.0,
                              "active_price": 2.0e-05, "is_out_of_range": False,
                              "is_closed": False}]
    out = lpa.preview_dlmm("POOL1", "WALLET1")
    assert out["ok"] is True
    assert out["error"] is None
    assert len(out["positions"]) == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest test_lp_position_alert.py -k preview_dlmm -v`
Expected: FAIL — `AttributeError: module 'lp_position_alert' has no attribute 'preview_dlmm'`

- [ ] **Step 3: 实现 `preview_dlmm`**

在 `fetch_pool_floor` 之后追加：

```python
CHAIN_OPTIONS = ["sol"]
POOL_PRICE_CHAINS = ["robinhood", "bsc", "base", "eth"]


def preview_dlmm(pool_address, wallet):
    pool = fetch_meteora_pool(pool_address)
    if not pool:
        return {"ok": False, "error": "❌ 连接失败：Meteora 未找到该池子地址，请核对池子地址是否正确。",
                "pool": None, "positions": []}
    positions = fetch_meteora_positions(pool_address, wallet)
    if positions is None:
        return {"ok": False, "error": "❌ 连接成功，但读取仓位失败（接口异常），请稍后重试。",
                "pool": pool, "positions": []}
    if not positions:
        return {"ok": False, "error": "⚠️ 该钱包在此池没有开放仓位。请确认钱包地址，或该仓位是否已关闭。",
                "pool": pool, "positions": []}
    return {"ok": True, "error": None, "pool": pool, "positions": positions}
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest test_lp_position_alert.py -k preview_dlmm -v`
Expected: PASS，3 passed

- [ ] **Step 5: 写页面 Tab 1**

创建 `pages/lp_position_alert.py`：

```python
# -*- coding: utf-8 -*-
"""LP 仓位 / 池子价格告警配置页"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import streamlit as st

from config import FEISHU_WEBHOOK
import lp_position_alert as lpa

st.set_page_config(page_title="LP 仓位告警", page_icon="📌", layout="wide")
st.title("📌 LP 仓位 / 池子价格告警")

if FEISHU_WEBHOOK:
    st.success("✅ 飞书告警通道已配置")
else:
    st.warning("⚠️ 未配置 FEISHU_WEBHOOK，告警仅打印到控制台")

if not lpa.ensure_table():
    st.error("❌ 初始化 lp_position_alert 表失败，请检查 Turso 凭据。")
    st.stop()

st.caption("Solana / Meteora DLMM 读链上仓位真实区间；Robinhood 等链只做池子价格。")

tab_lp, tab_price = st.tabs(["Solana LP 仓位", "池子价格（Robinhood 等）"])

with tab_lp:
    c1, c2, c3 = st.columns(3)
    lp_pool = c1.text_input("池子地址 *", key="lp_pool",
                            placeholder="Meteora DLMM 池子地址（LbPair）")
    lp_wallet = c2.text_input("钱包地址 *", key="lp_wallet", placeholder="持有该仓位的钱包")
    c3.selectbox("链", lpa.CHAIN_OPTIONS, key="lp_chain")

    if st.button("🔌 连接", type="primary", key="lp_connect"):
        if not lp_pool.strip() or not lp_wallet.strip():
            st.warning("⚠️ 请填写池子地址与钱包地址")
        else:
            with st.spinner("正在连接 Meteora 并读取仓位 ..."):
                st.session_state["lp_preview"] = lpa.preview_dlmm(
                    lp_pool.strip(), lp_wallet.strip())

    preview = st.session_state.get("lp_preview")
    if preview:
        if preview["error"]:
            st.error(preview["error"])
        if preview["pool"]:
            p = preview["pool"]
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("池子", p.get("name") or "-")
            m2.metric("现价", f"{p.get('current_price'):.10g}" if p.get("current_price") else "-")
            m3.metric("TVL", f"{p.get('tvl'):,.0f}" if p.get("tvl") else "-")
            m4.metric("黑名单", "是" if p.get("is_blacklisted") else "否")
        if preview["ok"]:
            st.success("✅ 连接成功")
            opts = {pos["position_address"]: pos for pos in preview["positions"]}
            rows = [{
                "仓位地址": pos["position_address"],
                "区间下界": pos["min_price"],
                "区间上界": pos["max_price"],
                "bin": f"{pos['lower_bin_id']} ~ {pos['upper_bin_id']}",
                "当前 PnL%": pos["pnl_pct"],
                "已超区间": pos["is_out_of_range"],
            } for pos in preview["positions"]]
            st.dataframe(pd.DataFrame(rows), use_container_width=True)

            with st.form("lp_add_form"):
                picked = st.selectbox("选择仓位 *", options=list(opts.keys()))
                f1, f2 = st.columns(2)
                tgt_pct = f1.number_input("盈利目标 %（真实持仓盈亏）", value=10.0,
                                          step=1.0, key="lp_tgt")
                default_floor = opts[picked]["min_price"] or 0.0
                floor = f2.number_input("跌穿阈值（默认 = 该仓位区间下界）",
                                        value=float(default_floor),
                                        format="%.10f", step=0.0, key="lp_floor")

                if st.form_submit_button("✅ 添加规则", type="primary"):
                    pos = opts[picked]
                    if floor <= 0:
                        st.warning("⚠️ 跌穿阈值必须大于 0")
                    else:
                        created = lpa.add_rule({
                            "kind": "dlmm", "chain": "sol",
                            "pool_address": lp_pool.strip(),
                            "wallet": lp_wallet.strip(),
                            "position_address": picked,
                            "pool_name": (preview["pool"] or {}).get("name"),
                            "token_x_symbol": (preview["pool"] or {}).get("token_x_symbol"),
                            "token_y_symbol": (preview["pool"] or {}).get("token_y_symbol"),
                            "lower_bin_id": pos["lower_bin_id"],
                            "upper_bin_id": pos["upper_bin_id"],
                            "min_price": pos["min_price"], "max_price": pos["max_price"],
                            "floor_price": float(floor),
                            "target_mode": "pnl_pct", "target_pct": float(tgt_pct),
                            "entry_price": None,
                        })
                        if created:
                            st.success("✅ 规则已添加")
                            st.rerun()
                        else:
                            st.error("❌ 规则写入失败")
```

- [ ] **Step 6: 冒烟运行页面**

Run: `python -c "import ast;ast.parse(open('pages/lp_position_alert.py').read());print('syntax OK')"`
Expected: `syntax OK`

Run: `streamlit run index.py --server.headless true --server.port 8599`（另开一个终端 `curl -s localhost:8599 | head -c 200`）
Expected: 返回 HTML，无 `ImportError` / `SyntaxError`

- [ ] **Step 7: 提交**

```bash
git add lp_position_alert.py pages/lp_position_alert.py test_lp_position_alert.py
git commit -m "feat: LP 告警页 Tab1（Solana 连接、列仓位、设阈值）"
```

---

### Task 9: 界面 Tab 2 — 池子价格 + 共用规则列表

**Files:**
- Modify: `pages/lp_position_alert.py`
- Modify: `lp_position_alert.py`（追加 `preview_pool_price`）
- Test: `test_lp_position_alert.py`

**Interfaces:**
- Consumes: `fetch_dexscreener_pair`、`fetch_pool_floor`、`add_rule`、`load_rules`
- Produces: `preview_pool_price(chain: str, pool_address: str) -> dict`，返回 `{"ok": bool, "error": str|None, "pair": dict|None, "floor": float|None}`

- [ ] **Step 1: 写失败测试**

```python
@patch("lp_position_alert.fetch_dexscreener_pair")
def test_preview_pool_price_success(mock_pair):
    mock_pair.return_value = {"price": 5.14e-06, "base_symbol": "USDG",
                              "quote_symbol": "USDG", "liquidity_usd": 1.0,
                              "pair_created_at": 1}
    out = lpa.preview_pool_price("robinhood", "0xPAIR")
    assert out["ok"] is True
    assert out["pair"]["price"] == pytest.approx(5.14e-06)


@patch("lp_position_alert.fetch_dexscreener_pair")
def test_preview_pool_price_reports_bad_address(mock_pair):
    mock_pair.return_value = None
    out = lpa.preview_pool_price("robinhood", "0xBAD")
    assert out["ok"] is False
    assert "连接失败" in out["error"]
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest test_lp_position_alert.py -k preview_pool_price -v`
Expected: FAIL — `attribute 'preview_pool_price' not found`

- [ ] **Step 3: 实现 `preview_pool_price`**

在 `preview_dlmm` 之后追加：

```python
def preview_pool_price(chain, pool_address):
    pair = fetch_dexscreener_pair(chain, pool_address)
    if not pair or pair.get("price") is None:
        return {"ok": False,
                "error": "❌ 连接失败：Dexscreener 未找到该池子地址，请核对地址与所属链是否正确。",
                "pair": None, "floor": None}
    return {"ok": True, "error": None, "pair": pair, "floor": None}
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest test_lp_position_alert.py -k preview_pool_price -v`
Expected: PASS，2 passed

- [ ] **Step 5: 写 Tab 2 与规则列表**

在 `pages/lp_position_alert.py` 的 `with tab_price:` 分支写入：

```python
with tab_price:
    p1, p2 = st.columns(2)
    px_pool = p1.text_input("池子地址 *", key="px_pool", placeholder="Uniswap 池子 / pair 地址")
    px_chain = p2.selectbox("链", lpa.POOL_PRICE_CHAINS, key="px_chain")

    if st.button("🔌 连接", type="primary", key="px_connect"):
        if not px_pool.strip():
            st.warning("⚠️ 请填写池子地址")
        else:
            with st.spinner("正在连接 ..."):
                st.session_state["px_preview"] = lpa.preview_pool_price(
                    px_chain, px_pool.strip())

    px = st.session_state.get("px_preview")
    if px:
        if px["error"]:
            st.error(px["error"])
        if px["ok"]:
            st.success("✅ 连接成功")
            pair = px["pair"]
            q1, q2, q3, q4 = st.columns(4)
            q1.metric("交易对", f"{pair.get('base_symbol') or '-'} / {pair.get('quote_symbol') or '-'}")
            q2.metric("现价", f"{pair['price']:.10g}")
            q3.metric("流动性", f"{pair.get('liquidity_usd'):,.0f}"
                      if pair.get("liquidity_usd") else "-")
            q4.metric("建池时间",
                      pd.to_datetime(pair["pair_created_at"], unit="ms").strftime("%Y-%m-%d")
                      if pair.get("pair_created_at") else "-")

            if st.button("⬇️ 计算建池以来最低价", key="px_floor_btn"):
                with st.spinner("读取历史 K 线 ..."):
                    st.session_state["px_floor"] = lpa.fetch_pool_floor(
                        px_chain, px_pool.strip())
                if st.session_state["px_floor"] is None:
                    st.warning("⚠️ 取不到历史 K 线，请手动填写跌穿阈值。")

            entry_price = float(pair["price"])
            with st.form("px_add_form"):
                g1, g2 = st.columns(2)
                px_tgt = g1.number_input("盈利目标 %（相对登记时现价）", value=10.0,
                                         step=1.0, key="px_tgt")
                suggested = st.session_state.get("px_floor")
                px_floor = g2.number_input(
                    "跌穿阈值",
                    value=float(suggested) if suggested else 0.0,
                    format="%.10f", step=0.0, key="px_floor_val")
                st.caption(f"登记时现价 {entry_price:.10g}，盈利目标价 "
                           f"{entry_price * (100.0 + px_tgt) / 100.0:.10g}")

                if st.form_submit_button("✅ 添加规则", type="primary"):
                    if px_floor <= 0:
                        st.warning("⚠️ 跌穿阈值必须大于 0（可点上方按钮自动填入）")
                    else:
                        created = lpa.add_rule({
                            "kind": "pool_price", "chain": px_chain,
                            "pool_address": px_pool.strip(),
                            "wallet": None, "position_address": None,
                            "pool_name": f"{pair.get('base_symbol')} / {pair.get('quote_symbol')}",
                            "token_x_symbol": pair.get("base_symbol"),
                            "token_y_symbol": pair.get("quote_symbol"),
                            "floor_price": float(px_floor),
                            "target_mode": "price_pct", "target_pct": float(px_tgt),
                            "entry_price": entry_price,
                        })
                        if created:
                            st.success("✅ 规则已添加")
                            st.rerun()
                        else:
                            st.error("❌ 规则写入失败")

st.divider()
st.subheader("现有规则")

rules = lpa.load_rules(enabled_only=False)
if not rules:
    st.info("ℹ️ 暂无规则，请在上方 Tab 中新增。")
    st.stop()

view = pd.DataFrame([{
    "id": r["id"],
    "类型": r["kind"],
    "池子": r["pool_name"] or r["pool_address"][:10] + "...",
    "链": r["chain"],
    "仓位": (r["position_address"][:10] + "...") if r["position_address"] else "-",
    "触发": "".join([
        f"{'盈利' if r['enable_target_alert'] else ''}"
        f"{'/' if r['enable_target_alert'] and r['enable_floor_alert'] else ''}"
        f"{'跌穿' if r['enable_floor_alert'] else ''}"
    ]),
    "盈利目标%": r["target_pct"],
    "跌穿阈值": r["floor_price"],
    "当前值": (f"PnL {r['last_pnl_pct']:.2f}%"
               if r["kind"] == "dlmm" and r["last_pnl_pct"] is not None
               else (f"{r['last_active_price']:.10g}"
                     if r["last_active_price"] is not None else "-")),
    "状态": {"open": "🟢 监控中", "closed": "⚫ 已关闭", "error": "🔴 取数失败"}.get(r["status"], r["status"]),
    "启用": r["enabled"],
    "已告警": "".join([
        "盈利" if r["target_alerted"] else "",
        "跌穿" if r["floor_alerted"] else "",
    ]) or "-",
} for r in rules])
st.dataframe(view, use_container_width=True)

st.caption("操作")
for r in rules:
    o1, o2, o3, o4 = st.columns([1, 1, 1, 3])
    o1.write(f"#{r['id']}")
    if o2.button("暂停" if r["enabled"] else "启用", key=f"tg_{r['id']}"):
        lpa.set_enabled(r["id"], not r["enabled"])
        st.rerun()
    if o3.button("重置告警", key=f"rs_{r['id']}"):
        lpa.reset_alerts(r["id"])
        st.rerun()
    if o4.button("删除", key=f"dl_{r['id']}"):
        lpa.delete_rule(r["id"])
        st.rerun()
```

- [ ] **Step 6: 冒烟运行**

Run: `python -c "import ast;ast.parse(open('pages/lp_position_alert.py').read());print('syntax OK')"`
Expected: `syntax OK`

Run: `streamlit run index.py --server.headless true --server.port 8599`
Expected: 页面出现两个 Tab，无异常

- [ ] **Step 7: 提交**

```bash
git add lp_position_alert.py pages/lp_position_alert.py test_lp_position_alert.py
git commit -m "feat: LP 告警页 Tab2（池子价格、历史最低价、规则列表）"
```

---

### Task 10: 调度、导航注册与端到端验证

**Files:**
- Create: `.github/workflows/lp_position_monitor.yml`
- Modify: `index.py`（仅追加注册）

**Interfaces:**
- Consumes: `lp_position_alert.main()`（Task 7）
- Produces: 5 分钟一轮的云端调度

- [ ] **Step 1: 新建 workflow**

创建 `.github/workflows/lp_position_monitor.yml`：

```yaml
name: LP Position Alert Monitor

on:
  schedule:
    - cron: '*/5 * * * *'
  workflow_dispatch:

jobs:
  run-lp-position-monitor:
    runs-on: ubuntu-latest
    steps:
      - name: 1. 拉取仓库代码
        uses: actions/checkout@v4

      - name: 2. 安装 Python 环境
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: 3. 安装依赖
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: 4. 执行 LP 告警监控
        env:
          FEISHU_WEBHOOK: ${{ secrets.FEISHU_WEBHOOK }}
          LIBSQL_URL: ${{ secrets.LIBSQL_URL }}
          LIBSQL_TOKEN: ${{ secrets.LIBSQL_TOKEN }}
        run: python lp_position_alert.py
```

- [ ] **Step 2: 注册导航（仅追加一行）**

在 `index.py` 中这一行（第 52 行，原样存在，勿改）之后追加：

```python
token_distribution = st.Page("pages/distribution.py", title="代币分布图", icon=":material/bar_chart:")
```

追加内容：

```python
lp_position_alert_page = st.Page("pages/lp_position_alert.py", title="LP 仓位告警", icon=":material/notifications_active:")
```

并把 `lp_position_alert_page` 追加进 `"策略与数据"` 列表末尾：

```python
            "策略与数据": [dashboard, discover_lp, lp_bands, price_monitor, pool_simulator, token_distribution, lp_position_alert_page],
```

- [ ] **Step 3: 确认既有文件只被追加改动**

Run: `git diff index.py`
Expected: 恰好 **2 行 `+`、1 行 `-`**：新增 `lp_position_alert_page = st.Page(...)`（+1）、替换 `"策略与数据"` 那一行（+1 / -1）。除这一行外不应有任何 `-` 行，也不应有其他文件被改动（`git diff --stat` 只列 `index.py`）。

- [ ] **Step 4: 全部测试**

Run: `python -m pytest test_lp_position_alert.py test_lp_bands.py -q`
Expected: **53 passed**（本功能 42 + 既有 `test_lp_bands.py` 11）

**不要**跑裸的 `python -m pytest -v`。`test_imports.py` 不是 pytest 用例，它在模块顶层调用 `sys.exit(1)`，pytest 收集阶段会直接 `INTERNALERROR`。且它当前**本来就失败**（exit 1）：它断言 `monitor_meteora_pump.py` 里 `def fetch_tokens_by_strategy` 恰好出现一次，而该文件里这个字符串**出现 0 次**（函数早已被移除或改名，测试没跟着更新）。这是**先于本次改动就存在的陈旧测试**，`git diff monitor_meteora_pump.py` 为空可证。

→ 本次不要顺手去修它（超出范围，会把一个功能分支变成 repo 清理）。如需单独核对，跑 `python3 test_imports.py`，预期输出 `共 1 项失败: ['fetch_tokens_by_strategy 定义唯一']`、exit 1 —— 与改动前一致即为「未回归」。

- [ ] **Step 5: 端到端 — Tab 1 真实连接**

Run: `python -c "
import lp_position_alert as lpa
out = lpa.preview_dlmm('<你的pool地址>', '<你的钱包地址>')
print('ok   =', out['ok'])
print('err  =', out['error'])
print('pool =', out['pool'])
for p in out['positions']:
    print(' pos', p['position_address'], 'min', p['min_price'], 'max', p['max_price'], 'pnl%', p['pnl_pct'])
"`
Expected: `ok = True`，打印出仓位与区间上下界，与 Task 1 手工验证的数值一致

- [ ] **Step 6: 端到端 — 触发跌穿告警（用假阈值，不依赖市场）**

给上一步的规则把 `floor_price` 设到现价之上，跑一轮，确认飞书收到消息：

Run: `python -c "
import lp_position_alert as lpa
lpa.ensure_table()
rules = lpa.load_rules(enabled_only=False, kind='dlmm')
r = rules[-1]
print('rule', r['id'], 'active_price', r['last_active_price'])
lpa._execute('UPDATE lp_position_alert SET floor_price = ?, floor_alerted = 0 WHERE id = ?',
             [float(r['last_active_price']) * 2, r['id']])
print('set floor above price -> should fire')
lpa.run_once()
"`
Expected: 打印 `🚨 规则 N 触发: ['floor']`，飞书收到【价格跌穿】，且 `floor_alerted=1`

- [ ] **Step 7: 端到端 — 不重复推送**

Run: `python lp_position_alert.py`
Expected: 日志显示该规则已 `floor_alerted`，**不再推送**（`send_feishu_msg` 未被调用）

- [ ] **Step 8: 端到端 — 自动重武装**

Run: `python -c "
import lp_position_alert as lpa
r = lpa.load_rules(enabled_only=False, kind='dlmm')[-1]
lpa._execute('UPDATE lp_position_alert SET floor_price = ?, floor_alerted = 1 WHERE id = ?',
             [float(r['last_active_price']) * 0.5, r['id']])
print('floor below price -> rearm, no alert')
lpa.run_once()
r2 = lpa.load_rules(enabled_only=False, kind='dlmm')[-1]
print('floor_alerted =', r2['floor_alerted'])
"`
Expected: 打印 `floor_alerted = False`，且**未推送任何飞书消息**

- [ ] **Step 9: 端到端 — 取数失败不告警**

Run: `python -c "
import lp_position_alert as lpa
lpa.ensure_table()
lpa._execute(\"UPDATE lp_position_alert SET pool_address='INVALID', floor_price=1, floor_alerted=0, target_alerted=0, status='open' WHERE id=(SELECT MAX(id) FROM lp_position_alert)\")
lpa.run_once()
"`
Expected: 打印取数失败告警日志，`status` 变 `error`，**飞书未收到任何消息**

- [ ] **Step 10: 清理测试数据**

Run: `python -c "
import lp_position_alert as lpa
lpa._execute(\"DELETE FROM lp_position_alert WHERE pool_address='INVALID'\")
print('cleaned')
"`

- [ ] **Step 11: 回归确认 —— 现有价格监控未受影响**

Run: `python monitor_price.py`
Expected: 输出与改动前一致（`price_alert` 规则照常检查），无 ImportError

- [ ] **Step 12: 提交**

```bash
git add .github/workflows/lp_position_monitor.yml index.py
git commit -m "feat: 接入 LP 告警调度与导航注册"
```

---

## Self-Review

**1. Spec coverage**

| Spec 章节 | 覆盖任务 |
|---|---|
| 目标（连接成功 / 盈利目标 / 跌穿 / 飞书） | T7（告警）、T8（Tab1 连接）、T9（Tab2 连接） |
| 范围：两种 kind | T5（表 `kind`）、T6（两套取数）、T7（分派） |
| 数据源：Meteora `/pools` + `/positions/{pool}/pnl` | T6 |
| 数据源：Dexscreener pair（chainId `robinhood` 不是 4663） | T6（测试断言 `/pairs/robinhood/` 且不含 `/pairs/4663/`） |
| 数据源：GeckoTerminal OHLCV 求最低价 | T3（`floor_from_ohlcv`）、T6（`fetch_pool_floor`） |
| string→float 类型陷阱 | T2（`to_float` + 专项测试） |
| 表 `lp_position_alert` 全列 | T5（`COLUMNS` 与 DDL 一致） |
| 跌穿用 `poolActivePrice` 而非 `/pools.current_price` | T4（`test_evaluate_dlmm_floor_uses_active_price_not_pool_price`） |
| 空值即跳过 | T4（`test_evaluate_skips_disabled_and_null_triggers`、`test_evaluate_never_raises_on_missing_current_values`） |
| 保存时校验至少一个触发条件 | T8 / T9（表单里 `floor <= 0` 拒存；`enable_*` 默认 1） |
| 双标记位独立去重 | T7（`test_run_once_dlmm_fires_target_and_floor_independently`） |
| 跌穿自动重武装 | T4（`needs_rearm`）、T7（`test_run_once_rearms_floor_after_recovery`） |
| 仓位关闭 → status=closed + 通知一次 | T7（`test_run_once_dlmm_marks_closed_position`） |
| 取数失败绝不告警 | T7（`test_run_once_never_alerts_when_fetch_fails`）、T10 Step 9 |
| 界面两步式 Tab1 / Tab2 / 规则列表 | T8、T9 |
| 调度 workflow | T10 |
| 只碰 `index.py` 一行 | T10 Step 2/3 |
| 风险 1：minPrice 单位验证 | T1（阻塞门，含失败分支） |
| 风险 1 纵深防御：运行期单位校验，不一致则拒绝判定而非静默误判 | T4（`units_plausible` + 4 测试）、T7（`check_rule` 内守卫 + `test_run_once_dlmm_suppresses_floor_on_unit_mismatch`） |
| GeckoTerminal limit 100 翻页 | **未覆盖** —— 见下 |

**未覆盖项（明确记录）**：spec 提到建池超 100 天的池子需用 `before_timestamp` 翻页，「建池以来最低价」才完整。本计划先按单页 100 根实现（T6），并在页面提示取不到就让用户手填。翻页作为后续增强，不阻塞首版。此为有意缩小范围，不是遗漏。

**2. Placeholder scan**：无 `TBD` / `TODO` / 「适当处理错误」之类；每个改动步骤都带完整代码。T1/T10 的真实地址用 `<你的pool地址>` 占位是**运行时输入**，不是代码占位。

**3. Type consistency**：`to_float` / `parse_dlmm_position` / `parse_dexscreener_pair` / `floor_from_ohlcv` / `evaluate` / `needs_rearm` / `_current_price` / `ensure_table` / `add_rule` / `load_rules` / `delete_rule` / `set_enabled` / `reset_alerts` / `clear_alert_flag` / `set_status` / `update_runtime` / `mark_alerted` / `preview_dlmm` / `preview_pool_price` / `http_get_json` / `fetch_meteora_pool` / `fetch_meteora_positions` / `fetch_dexscreener_pair` / `fetch_pool_floor` / `check_rule` / `build_message` / `run_once` / `run_loop` / `main` 全文一致。

`cur` 字典在 T4 定义形状（`pnl_pct` / `active_price` / `price`），T7 的 `check_rule` 按此构造，`evaluate` 与 `_current_price` 按此消费，三处一致。

`COLUMNS` 与 `CREATE_TABLE_SQL` 的列**逐一对应**（28 列），`_row_to_rule` 依赖 `SELECT {COLUMNS}, id` 的列序，因此 `load_rules` 必须用 `", ".join(COLUMNS)` 且 `id` 放最后 —— 已在 T5 实现中固定。

## Execution Handoff

计划完成并保存到 `docs/superpowers/plans/2026-09-15-lp-position-alert.md`。两种执行方式：

**1. Subagent-Driven（推荐）** —— 每个任务派一个全新 subagent，任务之间我来审查，迭代快

**2. Inline Execution** —— 在当前会话按 executing-plans 批量执行，带检查点

选哪种？

**注意**：Task 1 是阻塞门，需要你提供**真实的 Solana 钱包地址**（以及一个你持有仓位的 Meteora DLMM 池子地址）。这一关不过，Task 7 的跌穿告警不能上线。

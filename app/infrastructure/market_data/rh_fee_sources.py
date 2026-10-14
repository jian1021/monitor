"""Robinhood Chain fee-screen 数据源（全部为实测可用的真实接口）。

数据源：
1. Robinhood Chain 官方 RPC（链验证）
2. rhpools 索引服务（链 4663 池子/成交量/手续费/流动性，链上事件索引）
3. DexScreener（chainId=robinhood，涨跌幅/创建时间/流动性补充）

Meteora 未在 Robinhood Chain 部署（Solana 专属），启动时执行部署检查并拦截。
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.core.settings import ROBINHOOD_RPC
from app.infrastructure.market_data.http import _safe_get
from app.infrastructure.rhpools import client as rhpools_client

ROBINHOOD_CHAIN_ID = 4663
DEFAULT_RPC = "https://rpc.mainnet.chain.robinhood.com/"
DEXSCREENER_BASE = "https://api.dexscreener.com"
METEORA_API_BASE = "https://dlmm.datapi.meteora.ag"

# rhpools 索引冷启动回填期（实测约 38h）内窗口数据为部分历史
INDEX_BACKFILL_LAG_S = 600.0   # lag 超过该值在界面提示"索引追赶中"


# ---------------------------------------------------------------- 链验证
def _rpc_call(rpc_url: str, method: str, params: list | None = None,
              timeout: int = 15) -> Any:
    import requests
    resp = requests.post(
        rpc_url, json={"jsonrpc": "2.0", "method": method,
                       "params": params or [], "id": 1},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json().get("result")


def verify_chain(rpc_url: str | None = None) -> dict:
    """启动时链验证：Chain ID 必须等于 4663（实测 eth_chainId）。"""
    url = (rpc_url or ROBINHOOD_RPC or DEFAULT_RPC).strip()
    result = {
        "ok": False,
        "rpc_url": url,
        "expected_chain_id": ROBINHOOD_CHAIN_ID,
        "observed_chain_id": None,
        "reason": "",
        "checked_at": time.time(),
    }
    try:
        raw = _rpc_call(url, "eth_chainId")
        result["observed_chain_id"] = int(str(raw), 16) if raw else None
    except Exception as e:
        result["reason"] = f"RPC 不可用（{url}）：{e}"
        return result
    if result["observed_chain_id"] != ROBINHOOD_CHAIN_ID:
        result["reason"] = (
            f"Chain ID 不匹配：期望 {ROBINHOOD_CHAIN_ID}，"
            f"实测 {result['observed_chain_id']}——这不是 Robinhood Chain 主网")
        return result
    result["ok"] = True
    result["reason"] = "已验证：Robinhood Chain 主网（Chain ID 4663）"
    return result


# ---------------------------------------------------------------- Meteora 部署检查
def check_meteora_deployment(timeout: int = 10) -> dict:
    """Meteora 是否部署在 Robinhood Chain。

    结论（固定事实，附带实时探针证据）：Meteora 是 Solana 原生协议，
    其官方 API 无链参数、只返回 Solana mint 地址的池子；rhpools 已审核
    部署注册表仅含 V2/V3/Slipstream/V4。因此 Robinhood Chain 上
    不存在 Meteora 流动池，筛选宇宙为 Uniswap V2/V3/V4/Slipstream。
    """
    evidence = [
        "Meteora 官方 API（dlmm.datapi.meteora.ag）无 chain 参数，只服务 Solana",
        "Meteora 文档（docs.meteora.ag）全部为 Solana DLMM/DAMM",
        "rhpools 已审核部署注册表（rhpools.lp_chain）仅含 V2/V3/Slipstream/V4，无 Meteora 程序 ID",
        "搜索到的「Robinhood/SOL」Meteora 池是 Solana 链上 ROBINHOOD 代币池"
        "（dexpaprika.com/solana/pool/...），不是 Robinhood Chain 的池子",
    ]
    probe = {"performed": False, "detail": ""}
    try:
        import requests
        resp = requests.get(
            f"{METEORA_API_BASE}/pools",
            params={"page": 1, "page_size": 3},
            timeout=timeout,
        )
        if resp.status_code == 200:
            data = resp.json().get("data") or []
            addrs = []
            for pool in data[:3]:
                for side in (pool.get("token_x") or {}, pool.get("token_y") or {}):
                    addr = str(side.get("address") or "")
                    if addr:
                        addrs.append(addr)
            evm_style = [a for a in addrs if a.startswith("0x")]
            probe = {
                "performed": True,
                "detail": (
                    f"实时探针：Meteora API 返回 {len(addrs)} 个代币地址，"
                    f"其中 EVM 格式（0x 前缀）{len(evm_style)} 个"
                    + ("——全部为 Solana mint 地址，确认无 EVM/Robinhood Chain 池"
                       if not evm_style else "——出现 EVM 地址，需人工复核")
                ),
            }
            evidence.append(probe["detail"])
    except Exception as e:
        probe = {"performed": True, "detail": f"实时探针失败：{e}"}
    return {
        "deployed_on_robinhood_chain": False,
        "evidence": evidence,
        "probe": probe,
    }


# ---------------------------------------------------------------- rhpools
def _rhpools_get(path: str, params: dict | None = None,
                 timeout: float = 20.0) -> dict | None:
    return rhpools_client.get(path, params=params, timeout=timeout)


def fetch_pools(window: str = "24h", sort: str = "volume",
                limit: int = 150, offset: int = 0) -> dict:
    """拉取 rhpools 池子列表（含 24h/7d 成交量、手续费、TVL、Active TVL）。"""
    out = {"ok": False, "rows": [], "reason": "", "fetched_at": time.time()}
    if not rhpools_client.available():
        out["reason"] = "robinhoodpools 子模块未拉取（git submodule update --init）"
        return out
    status = rhpools_client.ensure_running()
    if status is None:
        out["reason"] = (
            f"rhpools 索引服务不可用：{rhpools_client.last_error() or '未知原因'}"
        )
        return out
    payload = _rhpools_get("/api/lp/pools", {
        "window": window, "sort": sort, "order": "desc",
        "limit": min(150, max(1, int(limit))), "offset": max(0, int(offset)),
    })
    if payload is None:
        out["reason"] = f"rhpools /api/lp/pools 请求失败（window={window}）"
        return out
    rows = payload.get("rows") or []
    out["ok"] = True
    out["rows"] = rows
    out["total"] = payload.get("total")
    out["status"] = status
    return out


def fetch_status() -> dict | None:
    return rhpools_client.health(timeout=5)


def fetch_pool_events(pool_id: str, limit: int = 400) -> list[dict]:
    """从 /api/lp/tape 拉取最近链上事件并按 pool_id 过滤（用于重建 K 线）。

    tape 是全局事件流，样本有限；返回事件按时间升序。
    """
    payload = _rhpools_get("/api/lp/tape", {"limit": limit}, timeout=15)
    if payload is None:
        return []
    events = [e for e in (payload.get("rows") or [])
              if e.get("pool_id") == pool_id]
    events.sort(key=lambda e: to_ts(e.get("timestamp")))
    return events


def to_ts(value) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------- DexScreener
def fetch_pair_market(pair_address: str) -> dict | None:
    """DexScreener robinhood 链交易对详情（涨跌幅 m5/h1/h6/h24、创建时间等）。"""
    if not pair_address:
        return None
    url = f"{DEXSCREENER_BASE}/latest/dex/pairs/robinhood/{pair_address}"
    data = _safe_get(url, timeout=15)
    if not data:
        return None
    pairs = data.get("pairs") or []
    for pair in pairs:
        if str(pair.get("pairAddress") or "").lower() == pair_address.lower():
            return pair
    return pairs[0] if pairs else None


def search_pairs(query: str, limit: int = 10) -> list[dict]:
    """DexScreener 搜索 robinhood 链池子。"""
    from urllib.parse import quote
    data = _safe_get(f"{DEXSCREENER_BASE}/latest/dex/search?q={quote(query)}")
    if not data:
        return []
    pairs = [p for p in (data.get("pairs") or [])
             if p.get("chainId") == "robinhood"]
    return pairs[:limit]


# ---------------------------------------------------------------- K 线重建
def build_candles_5m(events: list[dict]) -> list[dict]:
    """用链上逐事件价格重建 5 分钟 K 线（样本有限，如实标注）。

    事件带 price0_usd/price1_usd 与 timestamp；按 300 秒分桶，
    O=首事件价、H/L=桶内最高最低、C=末事件价。无事件返回 []。
    """
    candles: dict[int, dict] = {}
    for event in events:
        ts = to_ts(event.get("timestamp"))
        price = to_float_or_none(event.get("price0_usd"))
        if ts <= 0 or price is None or price <= 0:
            continue
        bucket = int(ts // 300) * 300
        candle = candles.setdefault(bucket, {
            "ts": bucket, "open": price, "high": price, "low": price,
            "close": price, "events": 0,
        })
        candle["high"] = max(candle["high"], price)
        candle["low"] = min(candle["low"], price)
        candle["close"] = price
        candle["events"] += 1
    return [candles[k] for k in sorted(candles)]


def to_float_or_none(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result


__all__ = [
    "ROBINHOOD_CHAIN_ID", "DEFAULT_RPC", "INDEX_BACKFILL_LAG_S",
    "verify_chain", "check_meteora_deployment",
    "fetch_pools", "fetch_status", "fetch_pool_events",
    "fetch_pair_market", "search_pairs", "build_candles_5m",
]

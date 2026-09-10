"""dex_client.py — Dexscreener + GeckoTerminal 数据拉取（替代 gmgn_cli）.

数据源:
  - Token 信息 (价格/流动性/交易量): Dexscreener API (无需 API Key, ~300 req/min)
  - OHLCV K线数据:                  GeckoTerminal API (无需 API Key, ~10 req/min)

用法:
    from dex_client import fetch_token_info, fetch_ohlcv

    # Token 信息 (兼容旧 gmgn_cli 接口)
    data, source = fetch_token_info("sol", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
    # source: "dexscreener" 成功; data 结构与旧 GMGN 返回兼容
    # 失败: (None, 错误提示字符串)

    # OHLCV K线 (给 lp_bands_tool 用)
    t, o, h, l, c, v = fetch_ohlcv("sol", token_address, "1h", days=7)
"""
import datetime
import json
import sys
import time
from typing import Optional

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

# ============================================================
# 链名映射
# ============================================================
# GMGN 链名 → Dexscreener chainId
_CHAIN_TO_DEXSCREENER = {
    "sol": "solana",
    "bsc": "bsc",
    "base": "base",
    "eth": "ethereum",
    "robinhood": "robinhood",
    "arc": "arc",
    "stable": "stable",
}

# GMGN 链名 → GeckoTerminal network id
_CHAIN_TO_GECKO = {
    "sol": "solana",
    "bsc": "bsc",
    "base": "base",
    "eth": "eth",
    "robinhood": "robinhood",
    "arc": "arc",
    "stable": "stable",
}

# GMGN K线周期 → GeckoTerminal (timeframe, aggregate)
_RESOLUTION_TO_GECKO = {
    "30s": ("minute", 1),   # GeckoTerminal 无 30s，降级到 1m
    "1m":  ("minute", 1),
    "5m":  ("minute", 5),
    "15m": ("minute", 15),
    "1h":  ("hour", 1),
    "4h":  ("hour", 4),
    "1d":  ("day", 1),
}

DEXSCREENER_BASE = "https://api.dexscreener.com"
GECKO_BASE = "https://api.geckoterminal.com/api/v2"


# ============================================================
# 内部工具
# ============================================================

def _to_dexscreener_chain(chain: str) -> str:
    return _CHAIN_TO_DEXSCREENER.get(chain.lower().strip(), chain.lower().strip())


def _to_gecko_network(chain: str) -> str:
    return _CHAIN_TO_GECKO.get(chain.lower().strip(), chain.lower().strip())


def _safe_get(url: str, timeout: int = 15, **kwargs) -> Optional[dict]:
    """带错误处理的 GET 请求."""
    if requests is None:
        return None
    try:
        resp = requests.get(url, timeout=timeout, **kwargs)
        if resp.status_code == 200:
            return resp.json()
        print(f"⚠️ HTTP {resp.status_code}: {url}", file=sys.stderr)
    except Exception as e:
        print(f"⚠️ 请求异常: {e}", file=sys.stderr)
    return None


# ============================================================
# Dexscreener — Token 信息
# ============================================================

def _select_best_pair(pairs: list, chain_id: str, token_address: str = "") -> Optional[dict]:
    """从多个交易对中选出流动性最高的那个.

    优先: 目标地址作为 baseToken 的交易对 (token 信息语义: 我要查 X 的价格,
    X 通常是交易对里的 base)。若目标地址是 quoteToken (如 USDC 被当作报价币),
    则退而求其次选流动性最高的。
    """
    if not pairs:
        return None
    chain_pairs = [p for p in pairs if p.get("chainId") == chain_id]
    if not chain_pairs:
        chain_pairs = pairs
    token = (token_address or "").lower()

    if token:
        base_match = [p for p in chain_pairs
                      if (p.get("baseToken") or {}).get("address", "").lower() == token]
        if base_match:
            chain_pairs = base_match

    def liq(p):
        try:
            return float((p.get("liquidity") or {}).get("usd") or 0)
        except (TypeError, ValueError):
            return 0.0
    chain_pairs.sort(key=liq, reverse=True)
    return chain_pairs[0]


def fetch_token_info(chain: str, address: str):
    """通过 Dexscreener 拉取 token 信息 (兼容旧 gmgn_cli.fetch_token_info 接口).

    返回:
      (data_dict, "dexscreener")  成功
      (None, 错误提示字符串)      失败
    """
    if requests is None:
        return None, "缺少 requests 库 (pip install requests)"

    address = address.strip()
    chain_id = _to_dexscreener_chain(chain)

    # 尝试 token-pairs/v1 (链指定，更精确)
    url = f"{DEXSCREENER_BASE}/token-pairs/v1/{chain_id}/{address}"
    data = _safe_get(url)

    pairs = []
    if data is not None:
        if isinstance(data, list):
            pairs = data
        elif isinstance(data, dict):
            pairs = data.get("pairs") or []

    # fallback: /latest/dex/tokens/{address} (跨链)
    if not pairs:
        url2 = f"{DEXSCREENER_BASE}/latest/dex/tokens/{address}"
        data2 = _safe_get(url2)
        if data2 and isinstance(data2, dict):
            pairs = data2.get("pairs") or []

    if not pairs:
        return None, f"Dexscreener 未找到 {address[:8]}... 的交易对"

    pair = _select_best_pair(pairs, chain_id, address)
    if pair is None:
        return None, f"Dexscreener 未找到 {address[:8]}... 在 {chain_id} 上的交易对"

    # 构造兼容旧 GMGN 的 data 字典
    return _pair_to_gmgn_format(pair, address), "dexscreener"


def _pair_to_gmgn_format(pair: dict, token_address: str) -> dict:
    """把 Dexscreener pair 对象转成兼容旧 GMGN data 字典格式.

    旧代码的字段访问方式:
      data["price"]["price"]         → 价格
      data["price"]["volume_24h"]    → 24h 交易量
      data["symbol"]                 → 代币符号
      data["name"]                   → 代币名称
      data["liquidity"]              → 流动性 (USD)
      data["holder_count"]           → 持有人数 (Dexscreener 无此字段)
      data["pool"]["exchange"]       → DEX 名称
      data["pool"]["quote_symbol"]   → 报价币种
      data["pool"]["fee_ratio"]      → 费率
      data["ath_price"]              → 历史最高价
      data["total_supply"]           → 总供应量
      data["trade_fee"]              → 交易手续费
    """
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    target = (token_address or "").lower()
    is_quote_target = bool(target) and (quote.get("address") or "").lower() == target
    token = quote if is_quote_target else base

    # 价格: priceUsd 是 baseToken 的美元价.
    # 目标币是 quote 侧时 (如 USDC 在 PUMP/USDC 里), 用 priceUsd/priceNative 反推.
    price_usd = 0.0
    try:
        base_usd = float(pair.get("priceUsd") or 0)
    except (TypeError, ValueError):
        base_usd = 0.0
    if is_quote_target:
        try:
            native = float(pair.get("priceNative") or 0)
        except (TypeError, ValueError):
            native = 0.0
        price_usd = base_usd / native if native > 0 else 0.0
    else:
        price_usd = base_usd

    # 24h 交易量
    vol24 = 0.0
    try:
        vol24 = float((pair.get("volume") or {}).get("h24") or 0)
    except (TypeError, ValueError):
        pass

    # 流动性
    liq = 0.0
    try:
        liq = float((pair.get("liquidity") or {}).get("usd") or 0)
    except (TypeError, ValueError):
        pass

    # FDV / MarketCap
    fdv = pair.get("fdv")
    mc = pair.get("marketCap")

    # 创建时间
    created_at = pair.get("pairCreatedAt")

    return {
        # 兼容旧接口的嵌套结构
        "price": {
            "price": str(price_usd),
            "volume_24h": str(vol24),
        },
        "symbol": token.get("symbol") or "",
        "name": token.get("name") or "",
        "liquidity": str(liq),
        "holder_count": 0,  # Dexscreener 无此字段
        "pool": {
            "exchange": pair.get("dexId") or "",
            "quote_symbol": quote.get("symbol") or "",
            "fee_ratio": "0",  # Dexscreener 无费率字段
            "pair_address": pair.get("pairAddress") or "",
        },
        "ath_price": None,  # Dexscreener 无 ATH
        "total_supply": None,  # Dexscreener 无供应量
        "trade_fee": "0",  # Dexscreener 无交易手续费
        "fdv": fdv,
        "market_cap": mc,
        "pair_created_at": created_at,
        "dexscreener_url": pair.get("url") or "",
        "price_change_24h": (pair.get("priceChange") or {}).get("h24"),
        "_raw": pair,  # 保留原始数据以备后用
    }


# ============================================================
# GeckoTerminal — OHLCV K线数据
# ============================================================

def _resolve_pool_address(chain: str, token_address: str) -> Optional[str]:
    """用 Dexscreener 解析 token → pool_address (取流动性最高的池子)."""
    chain_id = _to_dexscreener_chain(chain)
    url = f"{DEXSCREENER_BASE}/token-pairs/v1/{chain_id}/{token_address}"
    data = _safe_get(url)

    pairs = []
    if data is not None:
        if isinstance(data, list):
            pairs = data
        elif isinstance(data, dict):
            pairs = data.get("pairs") or []

    if not pairs:
        return None
    best = _select_best_pair(pairs, chain_id, token_address)
    return best.get("pairAddress") if best else None


def fetch_ohlcv(chain: str, token_address: str, resolution: str = "1h",
                days: Optional[float] = None) -> tuple:
    """从 GeckoTerminal 拉取 OHLCV K线数据.

    参数:
      chain:       链名 (sol/bsc/base/eth/...)
      token_address: Token 合约地址
      resolution:  K线周期 (30s/1m/5m/15m/1h/4h/1d)
      days:        拉取天数 (None=按周期自动计算)

    返回:
      (t, o, h, l, c, v) — 时间标签、开高低收量 (numpy array)

    异常:
      ValueError / SystemExit (与旧 load_gmgn 行为一致)
    """
    import numpy as np

    if requests is None:
        raise ValueError("缺少 requests 库 (pip install requests)")

    # 解析 resolution → GeckoTerminal timeframe + aggregate
    tf_info = _RESOLUTION_TO_GECKO.get(resolution)
    if tf_info is None:
        raise ValueError(f"不支持的 K线周期: {resolution} (可选: {', '.join(_RESOLUTION_TO_GECKO)})")
    timeframe, aggregate = tf_info

    # 计算时间范围
    bar_seconds = {"30s": 30, "1m": 60, "5m": 300, "15m": 900,
                   "1h": 3600, "4h": 14400, "1d": 86400}.get(resolution, 3600)
    resolution_days_map = {
        "30s": 0.034, "1m": 0.069, "5m": 0.34, "15m": 1.04,
        "1h": 4.16, "4h": 16.6, "1d": 100,
    }
    window_days = float(days) if days is not None else float(resolution_days_map.get(resolution, 4.16))
    to_ts = int(time.time())
    from_ts = to_ts - int(window_days * 86400)

    # 第一步: resolve token → pool_address
    pool_address = _resolve_pool_address(chain, token_address)
    if not pool_address:
        raise SystemExit(f"未找到 {token_address[:8]}... 在 {chain} 上的交易对 (无法查询 K线)")

    # 第二步: 拉取 OHLCV
    network = _to_gecko_network(chain)
    url = (f"{GECKO_BASE}/networks/{network}/pools/{pool_address}"
           f"/ohlcv/{timeframe}?aggregate={aggregate}"
           f"&before_timestamp={to_ts}&limit=1000")
    data = _safe_get(url)

    if not data or "data" not in data:
        raise SystemExit(f"GeckoTerminal OHLCV 请求失败 (pool={pool_address[:8]}...)")

    ohlcv_list = (data.get("data") or {}).get("attributes", {}).get("ohlcv_list") or []
    if not ohlcv_list:
        raise SystemExit(f"GeckoTerminal OHLCV 返回为空 (pool={pool_address[:8]}...)")

    # 解析: 每条 [timestamp, open, high, low, close, volume]
    # 1) GeckoTerminal 返回倒序 → 反转成升序
    # 2) limit=1000 会返回超出请求窗口的旧K线 → 按 from_ts/to_ts 过滤
    # 3) Gecko 偶发返回重复时间戳 → 去重
    t, o, h, l, c, v = [], [], [], [], [], []
    prev_ts = None
    for candle in reversed(ohlcv_list):
        if len(candle) < 6:
            continue
        ts = int(candle[0])
        if ts < from_ts or ts > to_ts:
            continue
        if ts == prev_ts:
            continue
        prev_ts = ts
        t.append(datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M"))
        o.append(float(candle[1]))
        h.append(float(candle[2]))
        l.append(float(candle[3]))
        c.append(float(candle[4]))
        v.append(float(candle[5]))

    if not c:
        raise SystemExit(f"GeckoTerminal OHLCV 解析后为空 (pool={pool_address[:8]}...)")

    expected = int(window_days * 86400 / bar_seconds)
    if len(c) < expected:
        hrs = len(c) * bar_seconds / 3600
        print(f"  ⚠️ GeckoTerminal OHLCV 仅覆盖最近 {hrs:.1f} 小时"
              f" (请求窗口为 {window_days:.0f} 天)", file=sys.stderr)

    print(f"✅ 已从 GeckoTerminal 拉取 {len(c)} 根 {resolution} K线 ({chain})", file=sys.stderr)
    return t, np.array(o), np.array(h), np.array(l), np.array(c), np.array(v)

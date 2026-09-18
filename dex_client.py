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
from urllib.parse import quote

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
OKX_BASE = "https://www.okx.com"

# OKX DEX K线：按代币合约地址直查（无需先解析池子），限流比 GeckoTerminal 宽松得多。
# 不支持 Robinhood Chain（实测 chainIndex 报 51001），那条链仍走 GeckoTerminal。
OKX_DEX_CHAIN_INDEX = {"eth": "1", "bsc": "56", "base": "8453", "sol": "501"}
OKX_DEX_BAR = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D"}
_OKX_BARS_PER_DAY = {"1m": 1440, "5m": 288, "15m": 96, "1h": 24, "4h": 6, "1d": 1}

# 未显式指定 days 时，按周期估算取数窗口（1d 默认 100 天，与历史行为一致）
_RESOLUTION_DAYS = {
    "30s": 0.034, "1m": 0.069, "5m": 0.34, "15m": 1.04,
    "1h": 4.16, "4h": 16.6, "1d": 100,
}

# GeckoTerminal 免费额度很紧（约 30 次/分钟），批量监控时容易撞 429
RATE_LIMIT_RETRIES = 4
RATE_LIMIT_BACKOFF = 3.0
RATE_LIMIT_MAX_WAIT = 60.0


# ============================================================
# 内部工具
# ============================================================

def _to_dexscreener_chain(chain: str) -> str:
    return _CHAIN_TO_DEXSCREENER.get(chain.lower().strip(), chain.lower().strip())


def _to_gecko_network(chain: str) -> str:
    return _CHAIN_TO_GECKO.get(chain.lower().strip(), chain.lower().strip())


def _safe_get(url: str, timeout: int = 15, **kwargs) -> Optional[dict]:
    """带错误处理的 GET 请求；遇到 429 限流会退避重试而不是直接放弃."""
    if requests is None:
        return None
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            resp = requests.get(url, timeout=timeout, **kwargs)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429 and attempt < RATE_LIMIT_RETRIES:
                # 服务器常回 Retry-After: 0，直接采信会导致「等 0 秒」瞬间重试完、
                # 重试形同虚设；因此以指数退避为下限，仅当服务器要求更久时才加长。
                wait = RATE_LIMIT_BACKOFF * (2 ** attempt)
                try:
                    wait = max(wait, float(resp.headers.get("Retry-After")))
                except (TypeError, ValueError):
                    pass
                wait = min(wait, RATE_LIMIT_MAX_WAIT)
                print(f"⏳ HTTP 429 限流，{wait:.0f}s 后重试 ({attempt + 1}/{RATE_LIMIT_RETRIES})",
                      file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"⚠️ HTTP {resp.status_code}: {url}", file=sys.stderr)
            return None
        except Exception as e:
            # SSL 中断 / 连接超时这类瞬时故障也应退避重试，否则一次抖动就丢一个标的
            if attempt < RATE_LIMIT_RETRIES:
                wait = RATE_LIMIT_BACKOFF * (2 ** attempt)
                print(f"⏳ 请求异常，{wait:.0f}s 后重试 ({attempt + 1}/{RATE_LIMIT_RETRIES}): {e}",
                      file=sys.stderr)
                time.sleep(wait)
                continue
            print(f"⚠️ 请求异常: {e}", file=sys.stderr)
            return None
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


def _to_num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def lookup_token(chain: str, address: str) -> Optional[dict]:
    """按合约地址查单个代币的展示信息（地址直填时用）.

    返回 {address, symbol, name, price, market_cap, liquidity, found}；
    查不到时 found=False，但仍返回该地址，便于界面提示而不是直接拦住用户。
    注意：fetch_token_info 有跨链兜底，这里强制校验命中的链与所选链一致，
    否则选错链会静默拿到另一条链的同名/同址代币。
    """
    data, _source = fetch_token_info(chain, address)
    raw = (data or {}).get("_raw") or {}
    expected = _to_dexscreener_chain(chain)
    if raw.get("chainId") and raw.get("chainId") != expected:
        data = None
    if not data:
        return {"address": address, "symbol": None, "name": None,
                "price": None, "market_cap": None, "liquidity": None,
                "found": False}
    return {
        "address": address,
        "symbol": data.get("symbol"),
        "name": data.get("name"),
        "price": _to_num((data.get("price") or {}).get("price")),
        "market_cap": _to_num(data.get("market_cap")) or _to_num(data.get("fdv")),
        "liquidity": _to_num(data.get("liquidity")),
        "found": True,
    }


def search_tokens(chain: str, query: str, limit: int = 10) -> list:
    """按名称/符号搜索代币，返回候选列表供人工挑选.

    同名代币极多（仿盘、跨链版本、同名的不同项目），流动性最高的未必是你要的，
    因此这里只返回候选、按流动性降序，由调用方（界面）让人选择。
    """
    if requests is None:
        return []
    chain_id = _to_dexscreener_chain(chain)
    data = _safe_get(f"{DEXSCREENER_BASE}/latest/dex/search?q={quote(str(query))}")
    pairs = (data or {}).get("pairs") or []
    chain_pairs = [p for p in pairs if p.get("chainId") == chain_id] or pairs

    def liquidity(pair):
        try:
            return float((pair.get("liquidity") or {}).get("usd") or 0)
        except (TypeError, ValueError):
            return 0.0

    seen, out = set(), []
    for pair in sorted(chain_pairs, key=liquidity, reverse=True):
        base = pair.get("baseToken") or {}
        address = base.get("address")
        if not address or address in seen:
            continue
        seen.add(address)
        out.append({
            "address": address,
            "symbol": base.get("symbol"),
            "name": base.get("name"),
            "price": _to_num(pair.get("priceUsd")),
            "market_cap": _to_num(pair.get("marketCap")) or _to_num(pair.get("fdv")),
            "liquidity": liquidity(pair),
            "found": True,
        })
        if len(out) >= limit:
            break
    return out


def search_okx_symbols(query: str, limit: int = 10) -> list:
    """按符号搜索 OKX 现货交易对，返回候选列表供人工挑选.

    匹配 instId 或 base 包含 query 的交易对。
    """
    if requests is None or not query.strip():
        return []
    data = _safe_get(f"{OKX_BASE}/api/v5/public/instruments?instType=SPOT")
    instruments = (data or {}).get("data") or []
    q = query.strip().upper()
    matches = [i for i in instruments if q in i.get("instId", "").upper()
               or q in i.get("base", "").upper()]
    out = []
    seen = set()
    for inst in matches[:limit * 2]:
        symbol = inst.get("instId")
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        out.append({"symbol": symbol, "name": f"{inst.get('base', '')}/{inst.get('quote', '')}"})
        if len(out) >= limit:
            break
    return out


def _fetch_ohlcv_okx_dex(chain: str, token_address: str, resolution: str, days):
    """用 OKX DEX 取 K 线；链不支持或取不到时返回 None（由调用方回退 GeckoTerminal）."""
    index = OKX_DEX_CHAIN_INDEX.get((chain or "").lower())
    bar = OKX_DEX_BAR.get(resolution)
    if not index or not bar:
        return None
    import numpy as np

    window_days = float(days) if days is not None else float(
        _RESOLUTION_DAYS.get(resolution, 4.16))
    per_day = _OKX_BARS_PER_DAY.get(resolution, 24)
    limit = max(10, min(int(window_days * per_day) + 5, 1000))
    data = _safe_get(
        f"{OKX_BASE}/api/v5/dex/market/candles?chainIndex={index}"
        f"&tokenContractAddress={token_address}&bar={bar}&limit={limit}")
    rows = (data or {}).get("data") or []
    if not rows:
        return None

    # OKX 返回新 -> 旧，需反转为旧 -> 新（与 GeckoTerminal 路径一致）
    rows = list(reversed(rows))
    t, o, h, l, c, v = [], [], [], [], [], []
    for row in rows:
        if len(row) < 6:
            continue
        t.append(datetime.datetime.fromtimestamp(int(row[0]) / 1000).strftime("%m-%d %H:%M"))
        o.append(float(row[1]))
        h.append(float(row[2]))
        l.append(float(row[3]))
        c.append(float(row[4]))
        v.append(float(row[5]))
    if not c:
        return None
    print(f"✅ 已从 OKX DEX 拉取 {len(c)} 根 {resolution} K线 ({chain})", file=sys.stderr)
    return t, np.array(o), np.array(h), np.array(l), np.array(c), np.array(v)


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

    # 优先 OKX DEX：一次请求即可（无需解析池子）、限流宽松；
    # 链不支持或取不到时回退下面的 GeckoTerminal 逻辑。
    okx_result = _fetch_ohlcv_okx_dex(chain, token_address, resolution, days)
    if okx_result is not None:
        return okx_result

    # 解析 resolution → GeckoTerminal timeframe + aggregate
    tf_info = _RESOLUTION_TO_GECKO.get(resolution)
    if tf_info is None:
        raise ValueError(f"不支持的 K线周期: {resolution} (可选: {', '.join(_RESOLUTION_TO_GECKO)})")
    timeframe, aggregate = tf_info

    # 计算时间范围
    bar_seconds = {"30s": 30, "1m": 60, "5m": 300, "15m": 900,
                   "1h": 3600, "4h": 14400, "1d": 86400}.get(resolution, 3600)
    window_days = float(days) if days is not None else float(
        _RESOLUTION_DAYS.get(resolution, 4.16))
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

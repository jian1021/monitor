"""Dexscreener 适配器：token 信息、按名搜索、按址查询、池子解析。"""

from typing import Optional
from urllib.parse import quote

from app.infrastructure.market_data.chains import _to_dexscreener_chain
from app.infrastructure.market_data.http import _safe_get, requests

DEXSCREENER_BASE = "https://api.dexscreener.com"


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

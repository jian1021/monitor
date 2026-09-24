"""Meteora / Dexscreener / GeckoTerminal 取数（LP 告警）。"""

from app.domain.lp_alert.models import (
    floor_from_ohlcv,
    parse_dexscreener_pair,
    parse_dlmm_position,
    to_float,
)
from app.infrastructure.market_data.http import HEADERS, requests


METEORA_BASE = "https://dlmm.datapi.meteora.ag"


DEXSCREENER_BASE = "https://api.dexscreener.com"


GECKO_BASE = "https://api.geckoterminal.com/api/v2"


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


def fetch_open_portfolio(wallet, page_size=50):
    data = http_get_json(
        f"{METEORA_BASE}/portfolio/open",
        params={"user": wallet, "page_size": page_size},
        timeout=25,
    )
    if not isinstance(data, dict):
        return None
    pools = []
    for p in data.get("pools") or []:
        pools.append({
            "pool_address": p.get("poolAddress"),
            "pool_name": " / ".join(filter(None, [p.get("tokenX"), p.get("tokenY")])),
            "token_x_symbol": p.get("tokenX"),
            "token_y_symbol": p.get("tokenY"),
            "pool_price": to_float(p.get("poolPrice")),
            "pnl_pct": to_float(p.get("pnlPctChange")),
            "open_positions": p.get("openPositionCount"),
            "position_addresses": p.get("listPositions") or [],
        })
    return pools

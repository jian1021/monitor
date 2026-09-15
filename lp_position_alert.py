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

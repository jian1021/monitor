"""OKX 适配器：现货符号搜索、DEX K线。"""

import datetime
import sys

from app.infrastructure.market_data.chains import _RESOLUTION_DAYS
from app.infrastructure.market_data.http import _safe_get, requests

OKX_BASE = "https://www.okx.com"

# OKX DEX K线：按代币合约地址直查（无需先解析池子），限流比 GeckoTerminal 宽松得多。
# 不支持 Robinhood Chain（实测 chainIndex 报 51001），那条链仍走 GeckoTerminal。
OKX_DEX_CHAIN_INDEX = {"eth": "1", "bsc": "56", "base": "8453", "sol": "501"}
OKX_DEX_BAR = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D"}
_OKX_BARS_PER_DAY = {"1m": 1440, "5m": 288, "15m": 96, "1h": 24, "4h": 6, "1d": 1}


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

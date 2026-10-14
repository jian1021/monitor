"""
DEX Paprika 数据源（页面⑨ 池级 K 线）。

接口（官方 v3，已实测）：
  - 池详情:  GET /networks/{network}/pools/{pool_address}
  - 池 K线:  GET /networks/{network}/pools/{pool_address}/ohlcv?interval=&limit=&start=

鉴权：
  - 请求头 Authorization: <api_key>（原始 Key，无 Bearer 前缀）。Key 可选：
      * 无 Key   -> 仅 interval 1h/6h/12h/24h，回看最近 24 小时；
      * 免费 Key -> 增加 10m/15m/30m，回看最近 7 天（本项目页面⑨ 使用）。
  - 注意：默认 PowerShell/无 UA 请求会被网关拦截为 402；统一 HttpClient 自带
    User-Agent=MeteoraPoolScanner/3.1，实测可正常访问。

OHLCV 响应为数组：[{time_open,time_close,open,high,low,close,volume}, ...]。
本模块统一为 {timestamp(Unix秒), open, high, low, close, volume} 与项目其它 K 线一致。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ..api_client import HttpClient
from ..config import DEXPAPRIKA_API_KEY

NETWORK = "solana"


def _api_headers() -> dict[str, str]:
    if not DEXPAPRIKA_API_KEY:
        return {}
    return {"Authorization": DEXPAPRIKA_API_KEY}


def new_client(*, base_url: str, requests_per_second: float) -> HttpClient:
    """创建带 DEX Paprika Key 请求头的客户端（复用统一 HttpClient）。"""
    return HttpClient(
        base_url=base_url,
        requests_per_second=requests_per_second,
        headers=_api_headers(),
    )


def default_start() -> str:
    """依据是否配置 Key 返回可用回看窗口：有 Key 为 7 天，否则 24 小时。"""
    return "-7d" if DEXPAPRIKA_API_KEY else "-24h"


def fetch_pool(client: HttpClient, pool_address: str) -> Optional[dict]:
    """获取池详情；池不存在（404）返回 None。

    返回 {pool_address, name, dex, token_symbol, price_usd}。
    """
    resp = client.get_json(f"/networks/{NETWORK}/pools/{pool_address}")
    if not resp:
        return None
    tokens = resp.get("tokens") or []
    symbols = [t.get("symbol") or "" for t in tokens if isinstance(t, dict)]
    symbols = [s for s in symbols if s]
    base_symbol = symbols[0] if symbols else ""
    quote_symbol = symbols[1] if len(symbols) > 1 else ""
    name = base_symbol if not quote_symbol else f"{base_symbol}/{quote_symbol}"
    return {
        "pool_address": resp.get("id") or pool_address,
        "name": name or pool_address[:8] + "…",
        "dex": resp.get("dex_id") or "",
        "token_symbol": base_symbol or (quote_symbol or ""),
        "price_usd": _to_float(resp.get("last_price_usd")),
    }


def fetch_ohlcv(
    client: HttpClient,
    pool_address: str,
    interval: str,
    *,
    start: Optional[str] = None,
    limit: int = 1000,
) -> list[dict]:
    """获取池 K 线。interval 见官方（1m/5m/10m/15m/30m/1h/6h/12h/24h）。

    start 为相对时间偏移（如 -24h / -7d）；省略时按 default_start() 取当前档位窗口。
    超出档位窗口时接口返回 403（由调用方处理）。
    返回 [{timestamp, open, high, low, close, volume}]，按时间正序。
    """
    resp = client.get_json(
        f"/networks/{NETWORK}/pools/{pool_address}/ohlcv",
        params={"interval": interval, "limit": int(limit), "start": start or default_start()},
    )
    rows = resp if isinstance(resp, list) else (resp.get("data") if isinstance(resp, dict) else None) or []
    candles: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = _parse_ts(row.get("time_open"))
        if ts is None:
            continue
        o, h, l, c = (row.get("open"), row.get("high"), row.get("low"), row.get("close"))
        if None in (o, h, l, c):
            continue
        candles.append({
            "timestamp": ts,
            "open": float(o), "high": float(h), "low": float(l), "close": float(c),
            "volume": _to_float(row.get("volume")) or 0.0,
        })
    candles.sort(key=lambda c: c["timestamp"])
    return candles


def _parse_ts(raw) -> Optional[int]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return None


def _to_float(raw) -> Optional[float]:
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
"""
CoinGecko 数据源（页面⑨ 价格区间计算）。

接口（官方 v3，已实测）：
  - 搜索:      GET /search?query={关键字}          -> {coins:[{id,symbol,name,market_cap_rank,...}]}
  - 当前价格:  GET /simple/price?ids={id}&vs_currencies=usd -> {id:{usd:价格}}
  - OHLC K线:  GET /coins/{id}/ohlc?vs_currency=usd&days={d} -> [[ts_ms,open,high,low,close],...]

OHLC 粒度（实测 / 官方文档）：
  - days=1   -> 30 分钟粒度（48 根）
  - days=7/30-> 4 小时粒度（42 / 180 根）
  - 15 分钟及更细粒度官方不提供。

无 API Key 时使用公共域名（限频较严）；配置 COINGECKO_API_KEY 后自动切换到
pro-api.coingecko.com/api/v3。所有请求经过统一 HttpClient（限频/重试/超时）。
"""

from __future__ import annotations

from typing import Optional

from ..api_client import HttpClient
from ..config import COINGECKO_API_KEY


def _api_headers() -> dict[str, str]:
    if not COINGECKO_API_KEY:
        return {}
    # Pro 与 Demo Key 的请求头不同
    if COINGECKO_API_KEY.startswith("CG_"):
        return {"x-cg-pro-api-key": COINGECKO_API_KEY}
    return {"x-cg-demo-api-key": COINGECKO_API_KEY}


def new_client(*, base_url: str, requests_per_second: float) -> HttpClient:
    """创建带 CoinGecko Key 请求头的客户端（复用统一 HttpClient）。"""
    return HttpClient(
        base_url=base_url,
        requests_per_second=requests_per_second,
        headers=_api_headers(),
    )


def search_coin(client: HttpClient, query: str) -> Optional[dict]:
    """搜索代币，返回最佳匹配 {id, symbol, name}；无匹配返回 None。

    匹配策略：优先符号或 ID 完全一致（不区分大小写），其次名称完全一致，
    否则取市值排名最靠前的结果。
    """
    resp = client.get_json("/search", params={"query": query})
    coins = resp.get("coins") or []
    if not coins:
        return None
    q = query.strip().lower()

    def rank(c: dict) -> tuple:
        sym = (c.get("symbol") or "").lower()
        cid = (c.get("id") or "").lower()
        name = (c.get("name") or "").lower()
        exact = 0 if (sym == q or cid == q) else (1 if name == q else 2)
        return (exact, c.get("market_cap_rank") or 999_999)

    best = min(coins, key=rank)
    return {
        "id": best.get("id") or "",
        "symbol": best.get("symbol") or "",
        "name": best.get("name") or "",
    }


def fetch_price(client: HttpClient, coin_id: str) -> Optional[float]:
    """获取代币当前 USD 价格。失败/缺失返回 None。"""
    try:
        resp = client.get_json("/simple/price", params={"ids": coin_id, "vs_currencies": "usd"})
        price = (resp or {}).get(coin_id, {}).get("usd")
        return float(price) if price is not None else None
    except (TypeError, ValueError):
        return None


def fetch_ohlc(client: HttpClient, coin_id: str, days: int) -> list[dict]:
    """获取代币 OHLC K 线（days=1 为 30 分钟粒度，7/30 为 4 小时粒度）。

    返回 [{timestamp, open, high, low, close}]，时间戳为 Unix 秒，按时间正序。
    """
    resp = client.get_json(
        "/coins/{id}/ohlc".format(id=coin_id),
        params={"vs_currency": "usd", "days": int(days)},
    )
    candles: list[dict] = []
    for row in resp or []:
        if not isinstance(row, list) or len(row) < 5:
            continue
        try:
            candles.append({
                "timestamp": int(row[0]) // 1000,
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
            })
        except (TypeError, ValueError):
            continue
    return candles
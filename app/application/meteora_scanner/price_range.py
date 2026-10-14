"""
价格区间计算（页面 ⑨）。

两种数据源：
  A) DEX Paprika（**池级 K 线**，输入池地址）
     池地址 → DEX Paprika /networks/solana/pools/{address}/ohlcv → ta 库算 ATR
     周期：15m / 30m / 1h（免费注册 Key 支持；无 Key 仅 1h 且最近 24 小时）。
  B) CoinGecko（代币级 K 线，输入代币符号/名称/ID）
     代币 → CoinGecko /coins/{id}/ohlc → ta 库算 ATR
     周期：1d / 1h / 30m（无需 Key）。

两者共用：上价 = 当前价格 + 倍数 × ATR；下价 = 当前价格 − 倍数 × ATR。
ATR 采用现成技术指标库 `ta`（ta.volatility.AverageTrueRange，Wilder 平滑）。
所有数据均来自真实 API，绝不生成模拟数据。
"""

from __future__ import annotations

import math
from typing import Any, Optional

import pandas as pd
from ta.volatility import AverageTrueRange

from .api_client import ApiError
from .providers import coingecko, dexpaprika

# ---- DEX Paprika 池模式 ----
POOL_TIMEFRAMES = ["15m", "30m", "1h"]
DEFAULT_POOL_TIMEFRAME = "1h"

# ---- CoinGecko 代币模式 ----
TIMEFRAMES = ["1d", "1h", "30m"]
DEFAULT_TIMEFRAME = "1h"

DEFAULT_ATR_PERIOD = 14
DEFAULT_MULTIPLIER = 1.5

# 界面周期 -> (CoinGecko days 参数, 聚合为多少秒的K线, 原始粒度说明)
_TIMEFRAME_CFG: dict[str, tuple[int, Optional[int], str]] = {
    "1d": (30, 86400, "4小时"),
    "1h": (1, 3600, "30分钟"),
    "30m": (1, None, "30分钟"),
}


def resolve_coin(client, query: str) -> dict:
    """将用户输入解析为 CoinGecko 代币。

    返回 {coin_id, symbol, name, error}；解析失败时 error 非空。
    """
    try:
        coin = coingecko.search_coin(client, query)
    except Exception as exc:  # noqa: BLE001
        return {"coin_id": "", "symbol": "", "name": "", "error": f"CoinGecko 搜索失败：{exc}"}
    if not coin:
        return {"coin_id": "", "symbol": "", "name": "", "error": f"CoinGecko 未找到匹配 “{query}” 的代币。"}
    return {"coin_id": coin.get("id", ""), "symbol": coin.get("symbol", ""),
            "name": coin.get("name", ""), "error": None}


def fetch_market(client, coin_id: str, timeframe: str) -> dict:
    """获取当前价格与对应周期的 K 线（CoinGecko 代币模式）。

    返回 {price, candles, raw_granularity, candle_count}。
    """
    days, agg_seconds, raw_granularity = _TIMEFRAME_CFG.get(timeframe, _TIMEFRAME_CFG[DEFAULT_TIMEFRAME])
    candles = coingecko.fetch_ohlc(client, coin_id, days)
    if agg_seconds:
        candles = _aggregate(candles, agg_seconds)
    return {
        "price": coingecko.fetch_price(client, coin_id),
        "candles": candles,
        "raw_granularity": raw_granularity,
        "candle_count": len(candles),
    }


def resolve_pool(client, pool_address: str) -> dict:
    """将用户输入的池地址解析为 DEX Paprika 池详情。

    返回 {pool_address, name, dex, token_symbol, price_usd, error}；失败时 error 非空。
    """
    addr = (pool_address or "").strip()
    empty = {"pool_address": "", "name": "", "dex": "", "token_symbol": "", "price_usd": None}
    try:
        info = dexpaprika.fetch_pool(client, addr)
    except ApiError as exc:
        if exc.status == 404:
            return {**empty, "error": f"DEX Paprika 未找到该池地址（仅支持 Solana 网络）：{addr}"}
        return {**empty, "error": f"DEX Paprika 查询失败：{exc}"}
    except Exception as exc:  # noqa: BLE001
        return {**empty, "error": f"DEX Paprika 查询失败：{exc}"}
    if not info:
        return {**empty, "error": f"DEX Paprika 未找到该池地址：{addr}"}
    return {**info, "error": None}


def fetch_pool_market(client, pool_address: str, timeframe: str) -> dict:
    """获取池当前价格与对应周期的**池级** K 线（DEX Paprika 池模式）。

    返回 {price, candles, raw_granularity, candle_count}。价格优先取池详情 last_price_usd，
    缺失时回退为最新一根 K 线收盘价。
    """
    candles = dexpaprika.fetch_ohlcv(client, pool_address, timeframe, limit=1000)
    price = None
    try:
        pool = dexpaprika.fetch_pool(client, pool_address)
        price = pool.get("price_usd") if pool else None
    except Exception:  # noqa: BLE001
        price = None
    if price is None and candles:
        price = candles[-1].get("close")
    return {
        "price": price,
        "candles": candles,
        "raw_granularity": timeframe,
        "candle_count": len(candles),
    }



def compute_range(
    price: Optional[float],
    candles: list[dict],
    multiplier: float = DEFAULT_MULTIPLIER,
    period: int = DEFAULT_ATR_PERIOD,
) -> dict:
    """基于 ta 库的 ATR 计算上下价格区间。

    返回字段：atr / upper / lower / period_used（实际采用的 ATR 样本数）/ candle_count。
    当前价格或倍数非法、或 K 线不足 2 根、或无有效 ATR 时，对应数值为 None。
    """
    result = {
        "atr": None,
        "upper": None,
        "lower": None,
        "period_used": None,
        "candle_count": len(candles),
    }
    if price is None or float(price) <= 0 or multiplier is None or float(multiplier) <= 0:
        return result
    rows = [c for c in candles if c.get("high") and c.get("low") and c.get("close")]
    if len(rows) < 2:
        return result

    df = pd.DataFrame(rows)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    period_used = max(1, min(int(period), len(df) - 1))
    atr_series = AverageTrueRange(high=high, low=low, close=close,
                                  window=period_used, fillna=False).average_true_range()
    atr_value = float(atr_series.iloc[-1])
    if not math.isfinite(atr_value) or atr_value < 0:
        return result

    result.update(
        atr=atr_value,
        period_used=period_used,
        upper=float(price) + float(multiplier) * atr_value,
        lower=float(price) - float(multiplier) * atr_value,
    )
    return result


def _aggregate(candles: list[dict], bucket_seconds: int) -> list[dict]:
    """将细粒度 K 线按整点时间窗聚合为大周期 K 线（例：30m → 1h、4h → 1d）。

    按每个原始 K 线的 open 时间所属窗口聚合 open/high/low/close/volume。
    返回按时间正序的聚合 K 线列表；字段缺失的原始 K 线被忽略。
    """
    buckets: dict[int, dict[str, Any]] = {}
    for c in sorted(candles, key=lambda x: x.get("timestamp") or 0):
        ts = c.get("timestamp")
        if ts is None:
            continue
        open_, close, high, low = c.get("open"), c.get("close"), c.get("high"), c.get("low")
        if open_ is None or close is None:
            continue
        bucket_id = int(ts) // bucket_seconds
        cur = buckets.get(bucket_id)
        if cur is None:
            buckets[bucket_id] = {
                "timestamp": bucket_id * bucket_seconds,
                "open": open_,
                "high": high if high is not None else open_,
                "low": low if low is not None else open_,
                "close": close,
                "volume": c.get("volume") or 0.0,
            }
        else:
            if high is not None:
                cur["high"] = max(cur["high"], high)
            if low is not None:
                cur["low"] = min(cur["low"], low)
            cur["close"] = close
            cur["volume"] = (cur.get("volume") or 0.0) + (c.get("volume") or 0.0)
    return [buckets[k] for k in sorted(buckets)]
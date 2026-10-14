"""价格与 K 线数据：从 Meteora datapi 拉取 OHLCV 与 meme 代币 USD 价格。"""

from __future__ import annotations

import time

import requests

from src.meteora_api import QUOTE_MINTS, fetch_pool_detail, to_float

TIMEFRAME_SECONDS = {
    "5m": 300,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "12h": 43200,
    "24h": 86400,
}

_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/plain, */*"}

# 实测原生 OHLCV 单次窗口上限约 96 根，超过会返回空；取数时从大到小逐档尝试。
_MAX_BARS_PER_REQUEST = 96


def _normalize_candle(raw):
    try:
        return {
            "timestamp": int(to_float(raw.get("timestamp"))),
            "open": to_float(raw.get("open")),
            "high": to_float(raw.get("high")),
            "low": to_float(raw.get("low")),
            "close": to_float(raw.get("close")),
            "volume": to_float(raw.get("volume")),
        }
    except (AttributeError, TypeError, ValueError):
        return None


def fetch_ohlcv(cfg, pool_address, timeframe="5m", bars=30, logger=None) -> list[dict]:
    """拉取最近 ``bars`` 根 K 线（按时间升序）；失败返回已拿到的部分或空列表。"""
    if not pool_address:
        return []
    seconds = TIMEFRAME_SECONDS.get(str(timeframe))
    if not seconds:
        raise ValueError(f"不支持的 timeframe: {timeframe}")

    end_ts = int(time.time())
    counts = sorted(
        {min(_MAX_BARS_PER_REQUEST, max(int(bars) + 2, 24)), 48, _MAX_BARS_PER_REQUEST},
        reverse=True,
    )
    candles: list[dict] = []
    for count in counts:
        start_ts = end_ts - count * seconds
        try:
            resp = requests.get(
                f"{cfg.data_api_base}/pools/{pool_address}/ohlcv",
                params={"timeframe": timeframe, "start_time": start_ts, "end_time": end_ts},
                headers=_HEADERS,
                timeout=cfg.request_timeout,
            )
            if resp.status_code != 200:
                _warn(logger, f"K线 {str(pool_address)[:8]} {timeframe} 响应异常: {resp.status_code}")
                continue
            raw = resp.json().get("data") or []
        except Exception as exc:  # noqa: BLE001
            _warn(logger, f"K线 {str(pool_address)[:8]} {timeframe} 拉取失败: {exc}")
            continue
        candles = [c for c in (_normalize_candle(c) for c in raw) if c]
        if len(candles) >= int(bars):
            break

    # 去重 + 升序
    deduped = {c["timestamp"]: c for c in candles if c["timestamp"] > 0}
    ordered = [deduped[ts] for ts in sorted(deduped)]
    if len(ordered) < int(bars):
        _warn(logger, f"K线 {str(pool_address)[:8]} {timeframe} 数量不足: {len(ordered)}/{bars}")
    return ordered


def fetch_closes(cfg, pool_address, timeframe="1h", bars=30, logger=None) -> list[float]:
    return [c["close"] for c in fetch_ohlcv(cfg, pool_address, timeframe, bars, logger)]


def meme_usd_price(cfg, pool_address, pool_meta=None, logger=None) -> float | None:
    """meme 代币 USD 价格（用于记录触发价与信号后表现）。"""
    meta = pool_meta if isinstance(pool_meta, dict) and pool_meta else None
    if meta is None:
        meta = fetch_pool_detail(cfg, pool_address, logger=logger)
    if not isinstance(meta, dict):
        return None
    sides = [
        s for s in (meta.get("token_x"), meta.get("token_y"))
        if isinstance(s, dict) and s
    ]
    if not sides:
        return None
    candidates = [s for s in sides if str(s.get("address", "")) not in QUOTE_MINTS]
    if not candidates:
        return None
    price = min(to_float(s.get("price"), float("inf")) for s in candidates)
    return price if price not in (float("inf"), 0.0) and price > 0 else None


def latest_close(cfg, pool_address, timeframe="5m", logger=None) -> float | None:
    candles = fetch_ohlcv(cfg, pool_address, timeframe, bars=2, logger=logger)
    if not candles:
        return None
    close = to_float(candles[-1].get("close"))
    return close if close > 0 else None


def _warn(logger, message):
    if logger:
        logger.warning(message)
    else:
        print(message)

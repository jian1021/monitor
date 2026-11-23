"""退出策略的行情检查与飞书告警。"""

import math

import pandas as pd
import requests
import ta

from app.core.settings import FEISHU_WEBHOOK
from app.domain.lp_alert.evaluator import units_plausible
from app.infrastructure.db.exit_strategy import (
    load_exit_alert_state, load_exit_strategy, save_exit_alert_state,
)
from app.infrastructure.market_data import rh_fee_sources
from app.infrastructure.notifications.sender import send_feishu_msg
from app.infrastructure.db.module_webhooks import resolve_webhook


def _sol_candles(address: str, timeframe: str) -> list[dict]:
    """Load real Meteora DLMM OHLCV candles."""
    response = requests.get(
        f"https://dlmm.datapi.meteora.ag/pools/{address}/ohlcv",
        params={"timeframe": timeframe}, timeout=15,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("data") or []


def _robinhood_candles(address: str, pool_id: str | None = None) -> tuple[list[dict], float | None]:
    events = rh_fee_sources.fetch_pool_events(pool_id or address, limit=800)
    candles = rh_fee_sources.build_candles_5m(events)
    pair = rh_fee_sources.fetch_pair_market(address) or {}
    # Keep price and candles on the same token/quote basis; DexScreener's
    # priceUsd may refer to the pair's base token, while tape uses token0.
    price = (rh_fee_sources.to_float_or_none(candles[-1].get("close"))
             if candles else rh_fee_sources.to_float_or_none(pair.get("priceUsd")))
    return candles, price


def _aggregate_robinhood(candles: list[dict], timeframe: str) -> list[dict]:
    if timeframe != "1h":
        return candles
    buckets = {}
    for candle in candles:
        bucket = int(candle["ts"]) // 3600 * 3600
        target = buckets.get(bucket)
        if target is None:
            buckets[bucket] = {"ts": bucket, "open": candle["open"],
                               "high": candle["high"], "low": candle["low"],
                               "close": candle["close"]}
        else:
            target["high"] = max(target["high"], candle["high"])
            target["low"] = min(target["low"], candle["low"])
            target["close"] = candle["close"]
    return [buckets[k] for k in sorted(buckets)]


DEFAULT_RSI_PERIOD = 6        # 原 RSI(3) 在 5m 上噪声过大
DEFAULT_RSI_CONFIRM_BARS = 2  # 连续 N 根已收盘 K 线 RSI ≥ 阈值才告警


def _evaluate(candles: list[dict], live_price: float | None,
              floor_price: float, rsi_high: float,
              rsi_period: int = DEFAULT_RSI_PERIOD,
              confirm_bars: int = DEFAULT_RSI_CONFIRM_BARS) -> tuple[dict, dict]:
    closes = []
    for row in candles:
        value = row.get("close")
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            closes.append(value)
    try:
        price = float(live_price) if live_price is not None else (closes[-1] if closes else None)
    except (TypeError, ValueError):
        price = closes[-1] if closes else None
    if price is not None and (not math.isfinite(price) or price <= 0):
        price = closes[-1] if closes else None

    # 单位校验：实时价与 K 线收盘价、下限与现价必须同一数量级，
    # 否则静默比较会让下限告警永久失效（或永远误报）。
    units_ok = True
    if closes and price is not None and not units_plausible(price, closes[-1]):
        units_ok = False
    if floor_price > 0 and price is not None and not units_plausible(floor_price, price):
        units_ok = False

    rsi = middle = upper = None
    period = max(2, int(rsi_period))
    confirm = max(1, int(confirm_bars))
    rsi_confirmed = False
    if len(closes) >= period + confirm:
        series_rsi = ta.momentum.rsi(pd.Series(closes, dtype="float64"), window=period)
        candidate = float(series_rsi.iloc[-1])
        if math.isfinite(candidate):
            rsi = candidate
        tail = [float(v) for v in series_rsi.iloc[-confirm:]]
        rsi_confirmed = all(math.isfinite(v) and v >= rsi_high for v in tail)
    if len(closes) >= 20:
        series = pd.Series(closes, dtype="float64")
        middle = float(series.rolling(20).mean().iloc[-1])
        std = float(series.rolling(20).std(ddof=0).iloc[-1])
        upper = middle + 2 * std

    values = {"price": price, "rsi": rsi, "middle": middle, "upper": upper,
              "rsi_period": period, "units_ok": units_ok}
    previous = closes[-2] if len(closes) >= 2 else None
    hits = {
        # The manually configured floor is price-only and must work even when
        # candle history is too short to calculate RSI or Bollinger bands.
        "pool_floor": (units_ok and floor_price > 0 and price is not None
                       and price <= floor_price),
        "rsi_high": rsi_confirmed,
        # 单位异常本身也是一个需要人工处理的告警（同样走闩锁，只报一次）
        "units_mismatch": not units_ok,
        "bollinger_middle": (previous is not None and middle is not None
                             and previous < middle <= price),
        "bollinger_upper": (previous is not None and upper is not None
                            and previous < upper <= price),
    }
    return values, hits


def run_monitor() -> list[dict]:
    config = load_exit_strategy()
    if not config.get("enabled"):
        return []
    alerts = []
    timeframe = config.get("timeframe") or "5m"
    for pool in config.get("pools") or []:
        if not pool.get("enabled", True):
            continue
        address = str(pool.get("address") or "").strip()
        if not address:
            continue
        try:
            if pool.get("chain") == "robinhood":
                candles, live_price = _robinhood_candles(address, pool.get("pool_id"))
                candles = _aggregate_robinhood(candles, timeframe)
            else:
                candles, live_price = _sol_candles(address, timeframe), None
            values, conditions = _evaluate(
                candles, live_price, float(pool.get("floor_price") or 0),
                float(config.get("rsi_high", 90)),
                int(config.get("rsi_period", DEFAULT_RSI_PERIOD)),
                int(config.get("rsi_confirm_bars", DEFAULT_RSI_CONFIRM_BARS)),
            )
            if not any(conditions.values()):
                # 全部未命中：写回全 False 以解除旧闩锁，保证下次命中能再次告警
                save_exit_alert_state(address, conditions)
                continue
            previous = load_exit_alert_state(address)
            fresh = [name for name, hit in conditions.items()
                     if hit and not previous.get(name)]
            if fresh:
                labels = {
                    "pool_floor": "到达池子价格下限",
                    "rsi_high": (f"RSI({values['rsi_period']}) 连续 "
                                 f"{int(config.get('rsi_confirm_bars', DEFAULT_RSI_CONFIRM_BARS))} 根 ≥ "
                                 f"{float(config.get('rsi_high', 90)):g}"),
                    "units_mismatch": "⚠️ 价格单位疑似不一致（下限/实时价/K线不在同一数量级），下限判定已暂停，请核对配置",
                    "bollinger_middle": "价格上穿布林中轨",
                    "bollinger_upper": "价格上穿布林上轨",
                }
                alerts.append({"pool": pool, "values": values, "state": conditions,
                               "reasons": [labels[k] for k in fresh]})
                retry_state = {name: bool(hit and name not in fresh)
                               for name, hit in conditions.items()}
                save_exit_alert_state(address, retry_state)
            else:
                save_exit_alert_state(address, conditions)
        except Exception as exc:
            print(f"退出策略行情检查失败 [{address[:14]}…]: {exc}")

    if alerts:
        lines = ["🚨【退出策略告警】任一条件命中，请检查池子："]
        for item in alerts:
            values = item["values"]
            price = "未知" if values["price"] is None else f"{values['price']:.8g}"
            rsi = "数据不足" if values["rsi"] is None else f"{values['rsi']:.2f}"
            rsi_name = f"RSI({values.get('rsi_period', DEFAULT_RSI_PERIOD)})"
            pool = item["pool"]
            lines.append(
                f"\n{pool.get('label') or pool.get('address')} ({pool.get('chain')})"
                f"\n现价: {price} | {rsi_name}: {rsi}"
                f"\n触发: {'、'.join(item['reasons'])}"
                f"\n地址: {pool.get('address')}"
            )
        sent = send_feishu_msg(resolve_webhook("exit_strategy"), "\n".join(lines))
        if sent:
            for item in alerts:
                save_exit_alert_state(item["pool"]["address"], item["state"])
    return alerts

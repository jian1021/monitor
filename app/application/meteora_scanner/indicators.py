"""
技术指标与信号（文档 5. 价格趋势筛选、8. 重点池监控）。

仅使用真实 DLMM K 线（OHLCV）。
注意：RSI(3) 等反转信号仅用于观察，不得用于推算 LP 手续费收益。
"""

from __future__ import annotations

import math
from typing import Optional

TREND_UP = "上涨"
TREND_DOWN = "下跌"
TREND_SIDEWAYS = "横盘"
TREND_UNKNOWN = "趋势不明"
TREND_INSUFFICIENT = "趋势数据不足"


def rsi(closes: list[float], period: int = 3) -> Optional[float]:
    """Wilder RSI。数据不足返回 None。"""
    if len(closes) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain = diff if diff > 0 else 0.0
        loss = -diff if diff < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def compute_trend(candles: list[dict], threshold_pct: float = 3.0) -> str:
    """基于收盘价判断趋势：上涨/下跌/横盘/趋势不明/趋势数据不足。"""
    if not candles:
        return TREND_INSUFFICIENT
    closes = [c.get("close") for c in candles if c.get("close") is not None]
    if len(closes) < 2:
        return TREND_INSUFFICIENT
    first, last = closes[0], closes[-1]
    if first <= 0:
        return TREND_UNKNOWN
    change = (last - first) / first * 100.0
    if change > threshold_pct:
        return TREND_UP
    if change < -threshold_pct:
        return TREND_DOWN
    return TREND_SIDEWAYS


def price_change_pct(candles: list[dict]) -> Optional[float]:
    """窗口内首尾收盘价变化百分比。"""
    closes = [c.get("close") for c in candles if c.get("close") is not None]
    if len(closes) < 2 or closes[0] <= 0:
        return None
    return (closes[-1] - closes[0]) / closes[0] * 100.0


def realized_volatility(candles: list[dict]) -> Optional[float]:
    """基于收盘价对数收益的标准差（未年化）。数据不足返回 None。"""
    closes = [c.get("close") for c in candles if c.get("close") is not None and c.get("close") > 0]
    if len(closes) < 3:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1) if n > 1 else 0.0
    return math.sqrt(var)


def compute_signals(candles: list[dict]) -> dict[str, bool]:
    """
    计算反转/观察信号（文档 8.）：
      - rsi3_oversold: RSI(3) < 20
      - stop_new_low: 最近低点不再创新低
      - break_local_high: 收盘突破近段局部高点
      - volume_recovery: 近期成交量较此前明显回升
    数据不足时对应信号为 False。
    """
    signals = {
        "rsi3_oversold": False,
        "stop_new_low": False,
        "break_local_high": False,
        "volume_recovery": False,
    }
    closes = [c.get("close") for c in candles if c.get("close") is not None]
    lows = [c.get("low") for c in candles if c.get("low") is not None]
    highs = [c.get("high") for c in candles if c.get("high") is not None]
    vols = [c.get("volume") for c in candles if c.get("volume") is not None]

    r = rsi(closes, 3)
    if r is not None and r < 20:
        signals["rsi3_oversold"] = True

    if len(lows) >= 4:
        prior_low = min(lows[:-1])
        if lows[-1] > prior_low:
            signals["stop_new_low"] = True

    if len(highs) >= 4 and len(closes) >= 1:
        prior_high = max(highs[:-1])
        if closes[-1] > prior_high:
            signals["break_local_high"] = True

    if len(vols) >= 6:
        recent = sum(vols[-3:]) / 3.0
        prior = sum(vols[-6:-3]) / 3.0
        if prior > 0 and recent > prior * 1.3:
            signals["volume_recovery"] = True

    return signals

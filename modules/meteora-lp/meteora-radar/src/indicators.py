"""指标与 5 分钟止跌 / 反弹信号（纯函数，便于回测与单测）。

* RSI：Wilder 平滑，与主流交易所/ta 库口径一致。
* 反弹信号：完全按方案第 5 节的初始规则，先「止跌」再「突破局部高点」，成交量
  只作为可选确认条件。这些规则是待验证的交易假设，可能出现假突破。
"""

from __future__ import annotations


# ===================== RSI =====================
def rsi_series(closes, period: int = 14) -> list:
    """返回与 closes 等长的 RSI 列表，前 period 个为 None。"""
    period = max(1, int(period))
    values = [float(c) for c in (closes or [])]
    result: list = [None] * len(values)
    if len(values) < period + 1:
        return result

    gains, losses = [], []
    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
        gains.append(diff if diff > 0 else 0.0)
        losses.append(-diff if diff < 0 else 0.0)

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def _rsi(ag, al):
        if al == 0:
            return 100.0 if ag > 0 else 50.0
        rs = ag / al
        return 100.0 - 100.0 / (1.0 + rs)

    result[period] = _rsi(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        result[i] = _rsi(avg_gain, avg_loss)
    return result


def rsi(closes, period: int = 14, offset: int = 1):
    """取 RSI 的某个位置：offset=1 最新，offset=2 倒数第二根（已收线口径）。"""
    series = rsi_series(closes, period)
    if not series:
        return None
    offset = max(1, int(offset))
    if len(series) < offset:
        return None
    value = series[-offset]
    if value is None:
        # 序列长度刚好卡在 period 附近时向前找最近一个有效值
        for candidate in reversed(series[:-offset + 1] or series):
            if candidate is not None:
                return candidate
        return None
    return value


# ===================== K 线工具 =====================
def closed_bars(candles, use_closed_bar: bool = True) -> list:
    """去掉当前未收线的那根（数据源最后一根通常是进行中的 K 线）。"""
    bars = [c for c in (candles or []) if isinstance(c, dict)]
    if use_closed_bar and len(bars) > 1:
        return bars[:-1]
    return bars


def detect_stabilization(candles, lookback: int = 5, stabilize_bars: int = 2,
                         use_closed_bar: bool = True):
    """判断窗口内是否「不再持续创出新低」。

    规则：窗口最低点必须出现在最后 stabilize_bars 根之前，即最近几根没有刷新新低。
    返回 {"stabilized", "low_index", "window_low", "window_high"}；数据不足返回 None。
    """
    bars = closed_bars(candles, use_closed_bar)
    lookback = max(1, int(lookback))
    if len(bars) < lookback:
        return None
    window = bars[-lookback:]
    lows = [c["low"] for c in window]
    highs = [c["high"] for c in window]
    low_index = min(range(len(lows)), key=lows.__getitem__)
    stabilized = low_index <= (len(window) - 1 - max(0, int(stabilize_bars)))
    return {
        "stabilized": bool(stabilized),
        "low_index": low_index,
        "window_low": lows[low_index],
        "window_high": max(highs),
    }


def detect_rebound(candles, lookback: int = 5, stabilize_bars: int = 2,
                   volume_factor: float = 1.0, require_volume: bool = False,
                   use_closed_bar: bool = True):
    """止跌后某根 K 线收盘突破此前窗口局部高点 → 反弹确认信号。

    数据不足 / 未止跌 / 未突破 / 放量不达标(且要求放量) 时返回 None。
    """
    bars = closed_bars(candles, use_closed_bar)
    lookback = max(1, int(lookback))
    if len(bars) < lookback + 1:
        return None

    window = bars[-(lookback + 1):-1]
    breakout = bars[-1]

    stability = detect_stabilization(window, lookback, stabilize_bars, use_closed_bar=False)
    if not stability or not stability["stabilized"]:
        return None

    prior_high = max(c["high"] for c in window)
    prior_low = min(c["low"] for c in window)
    if breakout["close"] <= prior_high:
        return None

    avg_volume = sum(c["volume"] for c in window) / len(window)
    volume_ratio = None
    volume_confirmed = False
    if avg_volume > 0 and breakout["volume"] > 0:
        volume_ratio = breakout["volume"] / avg_volume
        volume_confirmed = volume_ratio >= float(volume_factor)
    if require_volume and not volume_confirmed:
        return None

    return {
        "breakout_ts": breakout.get("timestamp"),
        "breakout_close": breakout["close"],
        "breakout_open": breakout["open"],
        "prior_high": prior_high,
        "prior_low": prior_low,
        "window_low": stability["window_low"],
        "volume_ratio": volume_ratio,
        "volume_confirmed": bool(volume_confirmed),
        "lookback": lookback,
    }


def analyze_5m(candles, cfg) -> dict:
    """一次算出 5m 的止跌与反弹状态，供候选记录与告警使用。"""
    lookback = int(cfg.rebound_lookback_bars)
    stabilize = int(cfg.rebound_stabilize_bars)
    use_closed = bool(getattr(cfg, "use_closed_bar", True))
    stability = detect_stabilization(candles, lookback, stabilize, use_closed)
    rebound = detect_rebound(
        candles, lookback, stabilize,
        volume_factor=float(cfg.rebound_volume_factor),
        require_volume=bool(getattr(cfg, "require_volume_confirmation", False)),
        use_closed_bar=use_closed,
    )
    return {
        "bars": len(closed_bars(candles, use_closed)),
        "stabilization": stability,
        "stabilized": bool(stability and stability["stabilized"]),
        "rebound": rebound,
    }

from src.indicators import (
    analyze_5m,
    closed_bars,
    detect_rebound,
    detect_stabilization,
    rsi,
    rsi_series,
)


# ---------- RSI ----------
def test_rsi_all_up_is_100():
    closes = [float(i) for i in range(1, 30)]
    assert rsi(closes, period=14) == 100.0


def test_rsi_all_down_is_zero():
    closes = [float(i) for i in range(30, 1, -1)]
    assert rsi(closes, period=14) == 0.0


def test_rsi_needs_enough_bars():
    assert rsi([1.0, 2.0, 3.0], period=14) is None
    assert rsi_series([1.0, 2.0], period=14) == [None, None]


def test_rsi_offset_uses_previous_bar():
    closes = [float(i) for i in range(1, 30)]
    closes[-1] = 0.0  # 最新一根大跌
    latest = rsi(closes, period=14, offset=1)
    previous = rsi(closes, period=14, offset=2)
    assert latest is not None and previous is not None
    assert latest < previous  # 最新一根下跌，RSI 应更低


# ---------- 止跌 / 反弹 ----------
def _bar(low, high, close, volume=100.0, ts=0):
    return {"timestamp": ts, "open": close, "high": high, "low": low,
            "close": close, "volume": volume}


def _rebound_candles():
    # 前 5 根：从高点回落，最低点在窗口第 3 根(索引2)出现 → 最近不再创新低
    window = [
        _bar(10.0, 10.5, 10.2),
        _bar(9.0, 10.0, 9.2),
        _bar(8.0, 9.0, 8.5),
        _bar(8.0, 8.6, 8.4),
        _bar(8.5, 9.0, 8.9),
    ]
    breakout = _bar(8.8, 10.8, 10.6, volume=300.0)  # 收盘 10.6 > 窗口高点 10.5，放量
    return window + [breakout]


def test_closed_bars_drops_in_progress_bar():
    candles = [_bar(1, 2, 1.5), _bar(1, 2, 1.5)]
    assert len(closed_bars(candles, True)) == 1
    assert len(closed_bars(candles, False)) == 2


def test_detect_stabilization_true_and_false():
    candles = _rebound_candles()
    stability = detect_stabilization(candles[:-1], lookback=5, stabilize_bars=2,
                                     use_closed_bar=False)
    assert stability["stabilized"] is True

    # 最低点落在最后一根 → 仍在创新低，未止跌
    descending = [_bar(10, 11, 10), _bar(9, 10, 9), _bar(8, 9, 8),
                  _bar(7, 8, 7), _bar(6, 7, 6)]
    assert detect_stabilization(descending, lookback=5, stabilize_bars=2,
                                use_closed_bar=False)["stabilized"] is False


def test_detect_rebound_confirms_breakout():
    signal = detect_rebound(_rebound_candles(), lookback=5, stabilize_bars=2,
                            volume_factor=1.0, require_volume=True, use_closed_bar=False)
    assert signal is not None
    assert signal["prior_high"] == 10.5
    assert signal["breakout_close"] == 10.6
    assert signal["volume_confirmed"] is True


def test_detect_rebound_rejects_without_breakout():
    candles = _rebound_candles()
    candles[-1]["close"] = 10.4  # 未突破窗口高点
    assert detect_rebound(candles, lookback=5, stabilize_bars=2,
                          use_closed_bar=False) is None


def test_detect_rebound_requires_volume_when_configured():
    candles = _rebound_candles()
    candles[-1]["volume"] = 50.0  # 低于均量
    assert detect_rebound(candles, lookback=5, stabilize_bars=2,
                          volume_factor=1.0, require_volume=True, use_closed_bar=False) is None
    # 不强制放量时仍能出信号，只是标记为未放量
    soft = detect_rebound(candles, lookback=5, stabilize_bars=2,
                          volume_factor=1.0, require_volume=False, use_closed_bar=False)
    assert soft is not None and soft["volume_confirmed"] is False


def test_analyze_5m_bundle():
    class Cfg:
        rebound_lookback_bars = 5
        rebound_stabilize_bars = 2
        rebound_volume_factor = 1.0
        require_volume_confirmation = False
        use_closed_bar = False

    result = analyze_5m(_rebound_candles(), Cfg())
    assert result["stabilized"] is True
    assert result["rebound"] is not None
    assert result["bars"] == 6

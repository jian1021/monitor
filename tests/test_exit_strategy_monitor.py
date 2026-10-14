from app.application.exit_strategy.monitor import _evaluate


def test_pool_floor_is_checked_with_insufficient_candle_history():
    values, hits = _evaluate([{"close": "0.9"}], None, 1.0, 90.0)

    assert values["price"] == 0.9
    assert hits["pool_floor"] is True
    assert hits["rsi_high"] is False
    assert hits["bollinger_middle"] is False
    assert hits["bollinger_upper"] is False


def test_rsi_three_is_available_without_bollinger_history():
    candles = [{"close": price} for price in (1.0, 1.1, 1.2, 1.3)]

    values, hits = _evaluate(candles, None, 0.0, 90.0)

    assert values["rsi"] is not None
    assert hits["rsi_high"] is True
    assert values["middle"] is None


def test_bollinger_crosses_are_checked_after_twenty_closes():
    candles = [{"close": 100.0} for _ in range(18)] + [{"close": 99.0}, {"close": 99.0}]

    values, hits = _evaluate(candles, 101.0, 0.0, 90.0)

    assert values["middle"] is not None
    assert hits["bollinger_middle"] is True
    assert hits["bollinger_upper"] is True


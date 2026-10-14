from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.application.monitors import meme_rsi_batch
from app.infrastructure.db import meme_watchlist


def test_batch_monitor_persists_selected_timeframe():
    db = Mock()
    db.execute.return_value = SimpleNamespace(rows=[("timeframe", "1h"), ("rsi_high", "85")])
    with patch.object(meme_watchlist, "ensure_meme_watchlist_schema"), \
         patch.object(meme_watchlist, "get_db_client", return_value=db):
        params = meme_watchlist.get_meme_rsi_params()
    assert params["timeframe"] == "1h"
    assert params["rsi_high"] == 85.0


def test_batch_monitor_rejects_unsupported_timeframe():
    db = Mock()
    with patch.object(meme_watchlist, "ensure_meme_watchlist_schema"), \
         patch.object(meme_watchlist, "get_db_client", return_value=db):
        assert not meme_watchlist.update_meme_rsi_params({"timeframe": "5m"})
    db.execute.assert_not_called()


def test_scan_uses_selected_timeframe_with_default_fetcher():
    pool = {"address": "pool-address", "symbol": "MEME", "enabled": True}
    with patch.object(meme_rsi_batch, "_default_rsi_fetcher", return_value=(95, 0.25)) as fetch, \
         patch.object(meme_rsi_batch, "update_meme_pool_rsi"), \
         patch.object(meme_rsi_batch.time, "sleep"):
        hits = meme_rsi_batch.scan_watchlist([pool], timeframe="1h")
    fetch.assert_called_once_with("pool-address", meme_rsi_batch.RSI_PERIOD, "1h")
    assert hits[0]["side"] == "超买"


def test_alert_message_includes_selected_timeframe():
    message = meme_rsi_batch.build_alert_message(
        [{"symbol": "MEME", "address": "pool", "rsi": 95, "price": 1, "side": "超买"}],
        timeframe="15m",
    )
    assert "RSI(3,15m)" in message


def test_fifteen_minute_rsi_aggregates_three_native_five_minute_bars():
    from app.application.monitors.rsi import _aggregate_meteora_candles

    candles = [
        {"timestamp": stamp, "open": opened, "high": high, "low": low,
         "close": close, "volume": 1}
        for stamp, opened, high, low, close in (
            (600, 1, 2, 0.5, 1.5),
            (0, 0.8, 1.5, 0.7, 1.0),
            (300, 1.0, 1.8, 0.9, 1.2),
        )
    ]
    aggregated = _aggregate_meteora_candles(candles, 900)

    assert len(aggregated) == 1
    assert aggregated[0] == {
        "timestamp": 0, "open": 0.8, "high": 2.0, "low": 0.5,
        "close": 1.5, "volume": 3.0,
    }

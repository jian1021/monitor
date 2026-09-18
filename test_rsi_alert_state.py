from unittest.mock import patch

import db
from main import process_rsi_alert


SETTINGS = {"rsi_low": 30, "rsi_high": 70}


def test_rsi_alert_marks_bond_active_and_records_time_once():
    item = {"id": 12, "name": "转债", "code": "123456", "alert_latched": False}
    with patch("main.update_asset_alert_state") as update:
        message = process_rsi_alert(item, 20, 100.0, SETTINGS, "可转债")

    assert "可转债 RSI 超卖" in message
    update.assert_called_once_with(12, active=True, latched=True, rsi=20, price=100.0, alert=True)


def test_rsi_alert_does_not_repeat_while_latched_after_reset():
    item = {"id": 12, "name": "转债", "code": "123456",
            "alert_active": False, "alert_latched": True}
    with patch("main.update_asset_alert_state") as update:
        message = process_rsi_alert(item, 20, 100.0, SETTINGS, "可转债")

    assert message is None
    update.assert_called_once_with(12, active=False, latched=True, rsi=20, price=100.0, alert=False)


def test_rsi_alert_stays_active_on_later_checks_until_reset():
    item = {"id": 12, "name": "转债", "code": "123456",
            "alarm_active": True, "alert_latched": True}
    with patch("main.update_asset_alert_state") as update:
        message = process_rsi_alert(item, 20, 100.0, SETTINGS, "可转债")

    assert message is None
    update.assert_called_once_with(12, active=True, latched=True, rsi=20, price=100.0, alert=False)


def test_rsi_alert_clears_latch_after_rsi_recovers():
    item = {"id": 12, "name": "ETF", "code": "510000", "alert_latched": True}
    with patch("main.update_asset_alert_state") as update:
        message = process_rsi_alert(item, 50, 2.0, SETTINGS, "ETF")

    assert message is None
    update.assert_called_once_with(12, active=False, latched=False, rsi=50, price=2.0, alert=False)


def test_reset_asset_alert_only_clears_active_flag():
    client = type("Client", (), {
        "execute": lambda self, sql, params: None,
        "close": lambda self: None,
    })()
    with patch("db.get_db_client", return_value=client):
        assert db.reset_asset_alert(12) is True

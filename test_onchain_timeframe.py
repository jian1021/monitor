"""链上代币监控时间级别（asset_config.timeframe → resolution/days）测试."""
from unittest.mock import patch

import main
import monitor_rsi


def fake_config(*tokens):
    return {"crypto_okx": [], "meteora": [], "convertible_bonds": [],
            "etfs": [], "tokens": list(tokens)}


def test_token_uses_asset_timeframe_over_default():
    calls = []
    with patch.object(monitor_rsi, "get_token_rsi",
                      side_effect=lambda *a: calls.append(a) or (50.0, 1.0)), \
         patch("main.send_feishu_msg") as send:
        main.run_onchain_token_monitor(fake_config(
            {"code": "AAA", "name": "AAA", "chain": "sol", "timeframe": "1h"},
        ))
    assert calls[0][1] == "AAA"
    assert calls[0][2] == "1h"   # resolution = 标的的 timeframe
    assert calls[0][4] == 5      # days = TOKEN_RESOLUTION_DAYS["1h"]
    send.assert_not_called()     # RSI 50 处于正常区间，不推送


def test_token_falls_back_to_default_resolution():
    calls = []
    with patch.object(monitor_rsi, "get_token_rsi",
                      side_effect=lambda *a: calls.append(a) or (50.0, 1.0)), \
         patch("main.send_feishu_msg"):
        main.run_onchain_token_monitor(fake_config(
            {"code": "BBB", "name": "BBB", "chain": "robinhood"},
        ))
    assert calls[0][2] == "1d"   # 无 timeframe 时回退默认
    assert calls[0][4] == 15


def test_alert_message_uses_asset_resolution_label():
    with patch.object(monitor_rsi, "get_token_rsi", return_value=(5.0, 0.5)), \
         patch("main.send_feishu_msg") as send:
        main.run_onchain_token_monitor(fake_config(
            {"code": "CCC", "name": "CCC", "chain": "base", "timeframe": "1h"},
        ))
    msg = send.call_args[0][1]
    assert "1H RSI" in msg


def test_alert_message_contains_full_token_contract_address():
    address = "Dz9mQ9NzkB1234567890abcdefghijkLMNOPQRST"
    with patch.object(monitor_rsi, "get_token_rsi", return_value=(5.0, 0.5)), \
         patch("main.send_feishu_msg") as send:
        main.run_onchain_token_monitor(fake_config(
            {"code": address, "name": "USELESS", "chain": "sol", "timeframe": "1h"},
        ))

    assert address in send.call_args[0][1]


def test_timeframe_label_knows_4h():
    assert monitor_rsi.timeframe_label("4h") == "4H"
    assert monitor_rsi.TOKEN_RESOLUTION_DAYS["4h"] == 7

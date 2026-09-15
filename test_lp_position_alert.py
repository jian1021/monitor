# -*- coding: utf-8 -*-
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lp_position_alert as lpa


def test_to_float_accepts_strings_and_rejects_junk():
    assert lpa.to_float("1.25") == 1.25
    assert lpa.to_float(3) == 3.0
    assert lpa.to_float("1e-6") == pytest.approx(1e-6)
    assert lpa.to_float(None) is None
    assert lpa.to_float("") is None
    assert lpa.to_float("abc") is None


def test_parse_dlmm_position_converts_string_price_fields():
    raw = {
        "positionAddress": "POS1",
        "minPrice": "3.4e-05",
        "maxPrice": "5.2e-05",
        "lowerBinId": 123,
        "upperBinId": 456,
        "pnlPctChange": "12.5",
        "poolActivePrice": "4.1e-05",
        "isOutOfRange": False,
        "isClosed": False,
    }
    out = lpa.parse_dlmm_position(raw)
    assert out["position_address"] == "POS1"
    assert out["min_price"] == pytest.approx(3.4e-05)
    assert out["max_price"] == pytest.approx(5.2e-05)
    assert out["lower_bin_id"] == 123
    assert out["upper_bin_id"] == 456
    assert out["pnl_pct"] == 12.5
    assert out["active_price"] == pytest.approx(4.1e-05)
    assert out["is_out_of_range"] is False
    assert out["is_closed"] is False


def test_parse_dlmm_position_tolerates_missing_optional_fields():
    out = lpa.parse_dlmm_position({"positionAddress": "POS2", "isClosed": True})
    assert out["position_address"] == "POS2"
    assert out["min_price"] is None
    assert out["active_price"] is None
    assert out["is_out_of_range"] is None
    assert out["is_closed"] is True


def test_parse_dexscreener_pair_reads_first_pair():
    payload = {"pairs": [{
        "priceUsd": "0.000005140",
        "baseToken": {"symbol": "USDG"},
        "quoteToken": {"symbol": "USDG"},
        "liquidity": {"usd": 123456.78},
        "pairCreatedAt": 1785550187000,
    }]}
    out = lpa.parse_dexscreener_pair(payload)
    assert out["price"] == pytest.approx(5.140e-06)
    assert out["base_symbol"] == "USDG"
    assert out["quote_symbol"] == "USDG"
    assert out["liquidity_usd"] == pytest.approx(123456.78)
    assert out["pair_created_at"] == 1785550187000


def test_parse_dexscreener_pair_returns_none_when_no_pairs():
    assert lpa.parse_dexscreener_pair({"pairs": []}) is None
    assert lpa.parse_dexscreener_pair({}) is None
    assert lpa.parse_dexscreener_pair(None) is None


def test_parse_dexscreener_pair_survives_missing_nested_fields():
    out = lpa.parse_dexscreener_pair({"pairs": [{}]})
    assert out["price"] is None
    assert out["base_symbol"] is None
    assert out["liquidity_usd"] is None


def test_floor_from_ohlcv_takes_min_low_from_newest_first_list():
    ohlcv = [
        [1789462800, 1, 1, 5.14e-06, 5.14e-06, 10],
        [1788562800, 1, 1, 9.40e-06, 9.40e-06, 20],
        [1787662800, 1, 1, 3.43e-06, 3.43e-06, 30],
    ]
    assert lpa.floor_from_ohlcv(ohlcv) == pytest.approx(3.43e-06)


def test_floor_from_ohlcv_returns_none_for_empty_or_malformed():
    assert lpa.floor_from_ohlcv([]) is None
    assert lpa.floor_from_ohlcv(None) is None
    assert lpa.floor_from_ohlcv([["only-two", "cols"]]) is None
    assert lpa.floor_from_ohlcv([[1, 2, 3, "junk", 5, 6]]) is None


def _rule(**over):
    base = {
        "kind": "dlmm", "target_mode": "pnl_pct", "target_pct": 10.0,
        "floor_price": 3.0e-05, "entry_price": None,
        "enable_target_alert": 1, "enable_floor_alert": 1, "rearm": 1,
        "floor_alerted": 0, "target_alerted": 0,
    }
    base.update(over)
    return base


def test_evaluate_dlmm_target_fires_on_pnl_pct():
    cur = {"pnl_pct": 10.5, "active_price": 4.0e-05, "price": None}
    assert lpa.evaluate(_rule(), cur) == {"target": True, "floor": False}


def test_evaluate_dlmm_target_does_not_fire_below_threshold():
    cur = {"pnl_pct": 9.99, "active_price": 4.0e-05, "price": None}
    assert lpa.evaluate(_rule(), cur) == {"target": False, "floor": False}


def test_evaluate_dlmm_floor_fires_when_active_price_at_or_below_floor():
    for act in (3.0e-05, 2.9e-05):
        cur = {"pnl_pct": 0.0, "active_price": act, "price": None}
        assert lpa.evaluate(_rule(target_pct=None), cur) == {"target": False, "floor": True}


def test_evaluate_dlmm_floor_uses_active_price_not_pool_price():
    cur = {"pnl_pct": 0.0, "active_price": 4.0e-05, "price": 1.0e-09}
    assert lpa.evaluate(_rule(target_pct=None), cur)["floor"] is False


def test_evaluate_pool_price_target_uses_entry_snapshot():
    rule = _rule(kind="pool_price", target_mode="price_pct", target_pct=10.0,
                 entry_price=100.0, floor_price=50.0)
    assert lpa.evaluate(rule, {"pnl_pct": None, "active_price": None, "price": 110.0})["target"] is True
    assert lpa.evaluate(rule, {"pnl_pct": None, "active_price": None, "price": 109.99})["target"] is False


def test_evaluate_pool_price_floor_uses_price():
    rule = _rule(kind="pool_price", target_mode="price_pct", target_pct=10.0,
                 entry_price=100.0, floor_price=50.0)
    assert lpa.evaluate(rule, {"price": 50.0})["floor"] is True
    assert lpa.evaluate(rule, {"price": 50.01})["floor"] is False


def test_evaluate_skips_disabled_and_null_triggers():
    cur = {"pnl_pct": 99.0, "active_price": 1.0, "price": 1.0}
    assert lpa.evaluate(_rule(enable_target_alert=0), cur)["target"] is False
    assert lpa.evaluate(_rule(enable_floor_alert=0), cur)["floor"] is False
    assert lpa.evaluate(_rule(target_pct=None), cur)["target"] is False
    assert lpa.evaluate(_rule(floor_price=None), cur)["floor"] is False


def test_evaluate_never_raises_on_missing_current_values():
    assert lpa.evaluate(_rule(), {}) == {"target": False, "floor": False}


def test_evaluate_pool_price_ignores_zero_or_missing_entry():
    rule = _rule(kind="pool_price", target_mode="price_pct", target_pct=10.0, entry_price=0.0)
    assert lpa.evaluate(rule, {"price": 999.0})["target"] is False
    rule2 = _rule(kind="pool_price", target_mode="price_pct", target_pct=10.0, entry_price=None)
    assert lpa.evaluate(rule2, {"price": 999.0})["target"] is False


def test_needs_rearm_only_when_floor_alerted_and_price_recovered():
    rule = _rule(floor_alerted=1, rearm=1)
    assert lpa.needs_rearm(rule, {"active_price": 4.0e-05}) is True
    assert lpa.needs_rearm(rule, {"active_price": 2.0e-05}) is False
    assert lpa.needs_rearm(_rule(floor_alerted=0, rearm=1), {"active_price": 4.0e-05}) is False
    assert lpa.needs_rearm(_rule(floor_alerted=1, rearm=0), {"active_price": 4.0e-05}) is False
    assert lpa.needs_rearm(_rule(floor_alerted=1, rearm=1), {}) is False


def test_units_plausible_accepts_matching_values():
    assert lpa.units_plausible(3.56e-05, 3.56e-05) is True
    assert lpa.units_plausible(3.60e-05, 3.56e-05) is True
    assert lpa.units_plausible(3.9e-04, 3.56e-05) is True


def test_units_plausible_rejects_inverted_units():
    assert lpa.units_plausible(28012.0, 3.56e-05) is False
    assert lpa.units_plausible(3.56e-05, 28012.0) is False


def test_units_plausible_passes_when_data_missing():
    assert lpa.units_plausible(None, 3.56e-05) is True
    assert lpa.units_plausible(3.56e-05, None) is True
    assert lpa.units_plausible(None, None) is True


def test_units_plausible_rejects_nonpositive():
    assert lpa.units_plausible(0.0, 3.56e-05) is False
    assert lpa.units_plausible(3.56e-05, 0.0) is False


from config import LIBSQL_URL, LIBSQL_TOKEN

requires_db = pytest.mark.skipif(
    not (LIBSQL_URL and LIBSQL_TOKEN), reason="未配置 Turso 凭据"
)


@requires_db
def test_ensure_table_and_crud_roundtrip():
    assert lpa.ensure_table() is True
    rule = {
        "kind": "dlmm", "chain": "sol",
        "pool_address": "TESTPOOL_ROUNDTRIP", "wallet": "TESTWALLET",
        "position_address": "TESTPOS", "pool_name": "TEST/SOL",
        "token_x_symbol": "TEST", "token_y_symbol": "SOL",
        "lower_bin_id": 1, "upper_bin_id": 2,
        "min_price": 1.5e-05, "max_price": 2.5e-05, "floor_price": 1.5e-05,
        "target_mode": "pnl_pct", "target_pct": 10.0, "entry_price": None,
    }
    assert lpa.add_rule(rule) is True
    rows = [r for r in lpa.load_rules(enabled_only=False, kind="dlmm")
            if r["pool_address"] == "TESTPOOL_ROUNDTRIP"]
    assert len(rows) == 1
    rid = rows[0]["id"]
    assert rows[0]["target_pct"] == pytest.approx(10.0)
    assert rows[0]["min_price"] == pytest.approx(1.5e-05)
    assert rows[0]["status"] == lpa.STATUS_OPEN
    assert rows[0]["target_alerted"] is False

    assert lpa.update_runtime(rid, {"last_pnl_pct": 12.5, "last_active_price": 2.0e-05}) is True
    assert lpa.set_status(rid, lpa.STATUS_CLOSED) is True
    assert lpa.clear_alert_flag(rid, "floor_alerted") is True
    assert lpa.set_enabled(rid, False) is True

    after = [r for r in lpa.load_rules(enabled_only=False, kind="dlmm") if r["id"] == rid][0]
    assert after["last_pnl_pct"] == pytest.approx(12.5)
    assert after["status"] == lpa.STATUS_CLOSED
    assert after["enabled"] is False

    # pool_price 规则每轮会把 last_pnl_pct 写成 None，必须确认 None 能正常绑定并回读
    assert lpa.update_runtime(rid, {"last_pnl_pct": None}) is True
    assert [r for r in lpa.load_rules(enabled_only=False, kind="dlmm")
            if r["id"] == rid][0]["last_pnl_pct"] is None

    assert lpa.delete_rule(rid) is True
    assert [r for r in lpa.load_rules(enabled_only=False, kind="dlmm") if r["id"] == rid] == []


def _mock_response(json_data, status=200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = json_data
    return m


@patch("lp_position_alert.requests")
def test_fetch_meteora_pool(mock_requests):
    mock_requests.get.return_value = _mock_response({
        "name": "PUMP-SOL", "current_price": 3.56e-05,
        "token_x": {"symbol": "PUMP", "decimals": 6},
        "token_y": {"symbol": "SOL", "decimals": 9},
        "tvl": 1317645.9, "is_blacklisted": False, "created_at": 1752335747,
    })
    out = lpa.fetch_meteora_pool("POOL1")
    assert out["name"] == "PUMP-SOL"
    assert out["current_price"] == pytest.approx(3.56e-05)
    assert out["token_x_symbol"] == "PUMP"
    assert out["token_y_symbol"] == "SOL"
    assert out["is_blacklisted"] is False
    called = mock_requests.get.call_args[0][0]
    assert called.endswith("/pools/POOL1")


@patch("lp_position_alert.requests")
def test_fetch_meteora_pool_returns_none_on_http_error(mock_requests):
    mock_requests.get.return_value = _mock_response({}, 500)
    assert lpa.fetch_meteora_pool("POOL1") is None


@patch("lp_position_alert.requests")
def test_fetch_meteora_positions_parses_list(mock_requests):
    mock_requests.get.return_value = _mock_response({"positions": [{
        "positionAddress": "POS1", "minPrice": "1.5e-05", "maxPrice": "2.5e-05",
        "lowerBinId": 10, "upperBinId": 20, "pnlPctChange": "8.0",
        "poolActivePrice": "2.0e-05", "isOutOfRange": False, "isClosed": False,
    }], "totalCount": 1})
    out = lpa.fetch_meteora_positions("POOL1", "WALLET1")
    assert len(out) == 1
    assert out[0]["position_address"] == "POS1"
    assert out[0]["pnl_pct"] == 8.0
    _, kwargs = mock_requests.get.call_args
    assert kwargs["params"]["user"] == "WALLET1"


@patch("lp_position_alert.requests")
def test_fetch_meteora_positions_returns_none_on_http_error(mock_requests):
    mock_requests.get.return_value = _mock_response({}, 400)
    assert lpa.fetch_meteora_positions("POOL1", "W1") is None


@patch("lp_position_alert.requests")
def test_fetch_dexscreener_pair_maps_chain_and_parses(mock_requests):
    mock_requests.get.return_value = _mock_response({"pairs": [{
        "priceUsd": "5.14e-06",
        "baseToken": {"symbol": "USDG"}, "quoteToken": {"symbol": "USDG"},
        "liquidity": {"usd": 1000}, "pairCreatedAt": 1,
    }]})
    out = lpa.fetch_dexscreener_pair("robinhood", "0xPAIR")
    assert out["price"] == pytest.approx(5.14e-06)
    called = mock_requests.get.call_args[0][0]
    assert "/pairs/robinhood/0xPAIR" in called
    assert "/pairs/4663/" not in called


@patch("lp_position_alert.requests")
def test_fetch_pool_floor_from_ohlcv(mock_requests):
    mock_requests.get.return_value = _mock_response({"data": {"attributes": {
        "ohlcv_list": [[300, 1, 1, 5.0e-06, 5.0e-06, 1],
                       [200, 1, 1, 3.0e-06, 3.0e-06, 1],
                       [100, 1, 1, 4.0e-06, 4.0e-06, 1]]}}})
    assert lpa.fetch_pool_floor("robinhood", "0xPAIR") == pytest.approx(3.0e-06)
    called = mock_requests.get.call_args[0][0]
    assert "/networks/robinhood/pools/0xPAIR/ohlcv/day" in called


@patch("lp_position_alert.requests")
def test_http_get_json_returns_none_on_network_exception(mock_requests):
    mock_requests.get.side_effect = RuntimeError("boom")
    assert lpa.http_get_json("https://example.invalid") is None


def _db_rule(**over):
    base = {
        "id": 1, "kind": "dlmm", "chain": "sol",
        "pool_address": "POOL1", "wallet": "WALLET1", "position_address": "POS1",
        "pool_name": "PUMP-SOL", "token_x_symbol": "PUMP", "token_y_symbol": "SOL",
        "min_price": 1.5e-05, "max_price": 2.5e-05, "floor_price": 1.5e-05,
        "target_mode": "pnl_pct", "target_pct": 10.0, "entry_price": None,
        "enable_target_alert": 1, "enable_floor_alert": 1, "rearm": 1,
        "target_alerted": 0, "floor_alerted": 0, "status": "open",
    }
    base.update(over)
    return base


@patch("lp_position_alert.fetch_meteora_pool")
@patch("lp_position_alert.send_feishu_msg")
@patch("lp_position_alert.update_runtime")
@patch("lp_position_alert.mark_alerted")
@patch("lp_position_alert.clear_alert_flag")
@patch("lp_position_alert.set_status")
@patch("lp_position_alert.fetch_meteora_positions")
def test_run_once_dlmm_fires_target_and_floor_independently(
        mock_pos, mock_status, mock_clear, mock_mark, mock_update, mock_send, mock_pool):
    mock_pos.return_value = [{
        "position_address": "POS1", "min_price": 1.5e-05, "max_price": 2.5e-05,
        "lower_bin_id": 1, "upper_bin_id": 2, "pnl_pct": 25.0,
        "active_price": 1.0e-05, "is_out_of_range": True, "is_closed": False,
    }]
    mock_pool.return_value = {"name": "PUMP-SOL", "current_price": 1.0e-05}
    with patch("lp_position_alert.load_rules", return_value=[_db_rule()]):
        lpa.run_once()

    assert mock_send.call_count == 1, "两个条件都触发时应合成一条消息，而不是两条"
    msg = mock_send.call_args[0][1]
    assert "盈利" in msg and "跌穿" in msg
    marked = {c[0][1] for c in mock_mark.call_args_list}
    assert marked == {"target_alerted", "floor_alerted"}, "两个标记位必须各自落库"


@patch("lp_position_alert.fetch_meteora_pool")
@patch("lp_position_alert.send_feishu_msg")
@patch("lp_position_alert.update_runtime")
@patch("lp_position_alert.mark_alerted")
@patch("lp_position_alert.clear_alert_flag")
@patch("lp_position_alert.set_status")
@patch("lp_position_alert.fetch_meteora_positions")
def test_run_once_dlmm_marks_closed_position(
        mock_pos, mock_status, mock_clear, mock_mark, mock_update, mock_send, mock_pool):
    mock_pos.return_value = [{
        "position_address": "POS1", "min_price": None, "max_price": None,
        "lower_bin_id": None, "upper_bin_id": None, "pnl_pct": None,
        "active_price": None, "is_out_of_range": None, "is_closed": True,
    }]
    mock_pool.return_value = {"name": "PUMP-SOL"}
    with patch("lp_position_alert.load_rules", return_value=[_db_rule()]):
        lpa.run_once()

    assert mock_status.call_args[0][1] == lpa.STATUS_CLOSED
    assert mock_send.call_count == 1
    assert "已关闭" in mock_send.call_args[0][1]


@patch("lp_position_alert.fetch_meteora_pool")
@patch("lp_position_alert.send_feishu_msg")
@patch("lp_position_alert.update_runtime")
@patch("lp_position_alert.mark_alerted")
@patch("lp_position_alert.clear_alert_flag")
@patch("lp_position_alert.set_status")
@patch("lp_position_alert.fetch_meteora_positions")
def test_run_once_never_alerts_when_fetch_fails(
        mock_pos, mock_status, mock_clear, mock_mark, mock_update, mock_send, mock_pool):
    mock_pos.return_value = None
    with patch("lp_position_alert.load_rules", return_value=[_db_rule()]):
        lpa.run_once()
    assert mock_send.call_count == 0, "取数失败绝不能告警"
    assert mock_status.call_args[0][1] == lpa.STATUS_ERROR


@patch("lp_position_alert.fetch_dexscreener_pair")
@patch("lp_position_alert.send_feishu_msg")
@patch("lp_position_alert.update_runtime")
@patch("lp_position_alert.mark_alerted")
@patch("lp_position_alert.clear_alert_flag")
@patch("lp_position_alert.set_status")
def test_run_once_pool_price_fires_price_target(
        mock_status, mock_clear, mock_mark, mock_update, mock_send, mock_pair):
    mock_pair.return_value = {"price": 110.0, "base_symbol": "AAPL", "quote_symbol": "USDG",
                              "liquidity_usd": 1.0, "pair_created_at": 1}
    rule = _db_rule(kind="pool_price", chain="robinhood", wallet=None,
                    position_address=None, target_mode="price_pct",
                    target_pct=10.0, entry_price=100.0, floor_price=50.0)
    with patch("lp_position_alert.load_rules", return_value=[rule]):
        lpa.run_once()
    assert mock_send.call_count == 1
    assert mock_mark.call_args[0][1] == "target_alerted"


@patch("lp_position_alert.fetch_meteora_pool")
@patch("lp_position_alert.send_feishu_msg")
@patch("lp_position_alert.update_runtime")
@patch("lp_position_alert.mark_alerted")
@patch("lp_position_alert.clear_alert_flag")
@patch("lp_position_alert.set_status")
@patch("lp_position_alert.fetch_meteora_positions")
def test_run_once_rearms_floor_after_recovery(
        mock_pos, mock_status, mock_clear, mock_mark, mock_update, mock_send, mock_pool):
    mock_pos.return_value = [{
        "position_address": "POS1", "min_price": 1.5e-05, "max_price": 2.5e-05,
        "lower_bin_id": 1, "upper_bin_id": 2, "pnl_pct": 0.0,
        "active_price": 3.0e-05, "is_out_of_range": False, "is_closed": False,
    }]
    mock_pool.return_value = {"name": "PUMP-SOL"}
    with patch("lp_position_alert.load_rules",
               return_value=[_db_rule(floor_alerted=1, target_pct=None)]):
        lpa.run_once()
    assert mock_clear.call_args[0][1] == "floor_alerted"
    assert mock_send.call_count == 0, "价格回升不是告警，只是重置标记"


@patch("lp_position_alert.fetch_meteora_pool")
@patch("lp_position_alert.send_feishu_msg")
@patch("lp_position_alert.update_runtime")
@patch("lp_position_alert.mark_alerted")
@patch("lp_position_alert.clear_alert_flag")
@patch("lp_position_alert.set_status")
@patch("lp_position_alert.fetch_meteora_positions")
def test_run_once_dlmm_suppresses_floor_on_unit_mismatch(
        mock_pos, mock_status, mock_clear, mock_mark, mock_update, mock_send, mock_pool):
    mock_pos.return_value = [{
        "position_address": "POS1", "min_price": 1.5e-05, "max_price": 2.5e-05,
        "lower_bin_id": 1, "upper_bin_id": 2, "pnl_pct": 0.0,
        "active_price": 28012.0, "is_out_of_range": True, "is_closed": False,
    }]
    mock_pool.return_value = {"name": "PUMP-SOL", "current_price": 3.56e-05}
    with patch("lp_position_alert.load_rules",
               return_value=[_db_rule(target_pct=None, floor_price=1.5e-05)]):
        lpa.run_once()
    assert mock_send.call_count == 0, "单位不一致必须拒绝判定，不能误报跌穿"
    assert mock_mark.call_count == 0


def test_build_message_contains_actionable_fields():
    rule = _db_rule()
    cur = {"pnl_pct": 25.0, "active_price": 1.0e-05, "min_price": 1.5e-05,
           "max_price": 2.5e-05, "price": None}
    msg = lpa.build_message(rule, cur, ["target", "floor"], {"name": "PUMP-SOL"})
    assert "PUMP-SOL" in msg
    assert "POS1" in msg
    assert "25.00" in msg
    assert "meteora" in msg.lower()


@patch("lp_position_alert.fetch_meteora_positions")
@patch("lp_position_alert.fetch_meteora_pool")
def test_preview_dlmm_reports_bad_pool(mock_pool, mock_pos):
    mock_pool.return_value = None
    out = lpa.preview_dlmm("BADPOOL", "WALLET1")
    assert out["ok"] is False
    assert "连接失败" in out["error"]
    assert out["pool"] is None
    mock_pos.assert_not_called()


@patch("lp_position_alert.fetch_meteora_positions")
@patch("lp_position_alert.fetch_meteora_pool")
def test_preview_dlmm_reports_no_positions(mock_pool, mock_pos):
    mock_pool.return_value = {"name": "PUMP-SOL", "current_price": 1.0}
    mock_pos.return_value = []
    out = lpa.preview_dlmm("POOL1", "WALLET1")
    assert out["ok"] is False
    assert "没有开放仓位" in out["error"]
    assert out["pool"]["name"] == "PUMP-SOL"


@patch("lp_position_alert.fetch_meteora_positions")
@patch("lp_position_alert.fetch_meteora_pool")
def test_preview_dlmm_reports_positions_on_success(mock_pool, mock_pos):
    mock_pool.return_value = {"name": "PUMP-SOL", "current_price": 1.0,
                              "token_x_symbol": "PUMP", "token_y_symbol": "SOL",
                              "tvl": 2.0, "is_blacklisted": False}
    mock_pos.return_value = [{"position_address": "POS1", "min_price": 1.5e-05,
                              "max_price": 2.5e-05, "lower_bin_id": 1,
                              "upper_bin_id": 2, "pnl_pct": 5.0,
                              "active_price": 2.0e-05, "is_out_of_range": False,
                              "is_closed": False}]
    out = lpa.preview_dlmm("POOL1", "WALLET1")
    assert out["ok"] is True
    assert out["error"] is None
    assert len(out["positions"]) == 1


@patch("lp_position_alert.fetch_dexscreener_pair")
def test_preview_pool_price_success(mock_pair):
    mock_pair.return_value = {"price": 5.14e-06, "base_symbol": "USDG",
                              "quote_symbol": "USDG", "liquidity_usd": 1.0,
                              "pair_created_at": 1}
    out = lpa.preview_pool_price("robinhood", "0xPAIR")
    assert out["ok"] is True
    assert out["pair"]["price"] == pytest.approx(5.14e-06)


@patch("lp_position_alert.fetch_dexscreener_pair")
def test_preview_pool_price_reports_bad_address(mock_pair):
    mock_pair.return_value = None
    out = lpa.preview_pool_price("robinhood", "0xBAD")
    assert out["ok"] is False
    assert "连接失败" in out["error"]


@patch("lp_position_alert.http_get_json")
def test_fetch_open_portfolio_parses_pools(mock_get):
    mock_get.return_value = {"totalPositions": 2, "pools": [{
        "poolAddress": "POOL1", "tokenX": "MET", "tokenY": "SOL",
        "poolPrice": 0.00204, "pnlPctChange": "11.5",
        "openPositionCount": 2, "listPositions": ["P1", "P2"],
    }]}
    out = lpa.fetch_open_portfolio("WALLET1")
    assert len(out) == 1
    assert out[0]["pool_address"] == "POOL1"
    assert out[0]["pool_name"] == "MET / SOL"
    assert out[0]["pool_price"] == pytest.approx(0.00204)
    assert out[0]["pnl_pct"] == 11.5
    assert out[0]["open_positions"] == 2
    assert out[0]["position_addresses"] == ["P1", "P2"]
    assert mock_get.call_args[1]["params"]["user"] == "WALLET1"


@patch("lp_position_alert.http_get_json")
def test_fetch_open_portfolio_returns_none_on_failure(mock_get):
    mock_get.return_value = None
    assert lpa.fetch_open_portfolio("W") is None


@patch("lp_position_alert.fetch_open_portfolio")
def test_preview_wallet_success(mock_pf):
    mock_pf.return_value = [{"pool_address": "POOL1", "pool_name": "MET / SOL"}]
    out = lpa.preview_wallet("W")
    assert out["ok"] is True
    assert out["error"] is None
    assert out["pools"][0]["pool_address"] == "POOL1"


@patch("lp_position_alert.fetch_open_portfolio")
def test_preview_wallet_reports_no_open_positions(mock_pf):
    mock_pf.return_value = []
    out = lpa.preview_wallet("W")
    assert out["ok"] is False
    assert "没有开放的 LP 仓位" in out["error"]


@patch("lp_position_alert.fetch_open_portfolio")
def test_preview_wallet_reports_fetch_failure(mock_pf):
    mock_pf.return_value = None
    out = lpa.preview_wallet("W")
    assert out["ok"] is False
    assert "连接失败" in out["error"]

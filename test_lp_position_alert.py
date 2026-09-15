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

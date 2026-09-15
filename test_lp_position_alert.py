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

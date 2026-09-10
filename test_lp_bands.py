# -*- coding: utf-8 -*-
import json
import os
import sys
import time
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lp_bands_tool as tool
import dex_client
import numpy as np


def test_pair_to_gmgn_format_basic():
    pair = {
        "chainId": "solana",
        "dexId": "raydium",
        "url": "https://dexscreener.com/solana/fake",
        "pairAddress": "PAIR123",
        "baseToken": {"address": "TOKEN1", "name": "TestToken", "symbol": "TEST"},
        "quoteToken": {"address": "USDC", "name": "USD Coin", "symbol": "USDC"},
        "priceUsd": "1.23",
        "volume": {"h24": 50000},
        "liquidity": {"usd": 100000},
        "fdv": 1000000,
        "marketCap": 800000,
        "pairCreatedAt": 1700000000000,
    }
    data = dex_client._pair_to_gmgn_format(pair, "TOKEN1")
    assert data["symbol"] == "TEST"
    assert data["name"] == "TestToken"
    assert data["price"]["price"] == "1.23"
    assert data["price"]["volume_24h"] == "50000.0"
    assert data["liquidity"] == "100000.0"
    assert data["pool"]["exchange"] == "raydium"
    assert data["pool"]["quote_symbol"] == "USDC"
    assert data["holder_count"] == 0


def test_pair_to_gmgn_format_missing_fields():
    pair = {"baseToken": {}, "quoteToken": {}}
    data = dex_client._pair_to_gmgn_format(pair, "X")
    assert data["symbol"] == ""
    assert data["price"]["price"] == "0.0"
    assert data["liquidity"] == "0.0"


def _mock_response(json_data, status=200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = json_data
    return m


@patch("dex_client.requests")
def test_fetch_token_info_success(mock_requests):
    pair = {
        "chainId": "solana",
        "dexId": "raydium",
        "pairAddress": "PAIR1",
        "baseToken": {"address": "TOK1", "name": "Foo", "symbol": "FOO"},
        "quoteToken": {"address": "USDC", "name": "USD Coin", "symbol": "USDC"},
        "priceUsd": "0.50",
        "volume": {"h24": 10000},
        "liquidity": {"usd": 50000},
    }
    mock_requests.get.return_value = _mock_response([pair])
    data, source = dex_client.fetch_token_info("sol", "TOK1")
    assert source == "dexscreener"
    assert data is not None
    assert data["symbol"] == "FOO"
    assert data["price"]["price"] == "0.5"


@patch("dex_client.requests")
def test_fetch_token_info_not_found(mock_requests):
    mock_requests.get.return_value = _mock_response([], 200)
    data, source = dex_client.fetch_token_info("sol", "NONEXISTENT")
    assert data is None
    assert "未找到" in source


def _sample_gecko_ohlcv():
    now = int(time.time())
    # GeckoTerminal 返回倒序 (新→旧); 时间戳须落在请求窗口内
    return {
        "data": {
            "attributes": {
                "ohlcv_list": [
                    [now, 100.5, 102.0, 100.0, 101.0, 2000.0],
                    [now - 3600, 100.0, 101.0, 99.0, 100.5, 1000.0],
                ]
            }
        }
    }


@patch("dex_client._safe_get")
def test_fetch_ohlcv_success(mock_get):
    def side_effect(url, **kwargs):
        if "dexscreener.com" in url:
            return [{"pairAddress": "POOL1", "liquidity": {"usd": 10000}, "chainId": "solana"}]
        if "geckoterminal.com" in url:
            return _sample_gecko_ohlcv()
        return None
    mock_get.side_effect = side_effect

    t, o, h, l, c, v = dex_client.fetch_ohlcv("sol", "TOKEN1", "1h", days=1)
    assert len(c) == 2
    assert list(c) == [100.5, 101.0]
    assert list(o) == [100.0, 100.5]


@patch("dex_client._safe_get")
def test_fetch_ohlcv_no_pool_raises(mock_get):
    mock_get.return_value = None
    try:
        dex_client.fetch_ohlcv("sol", "NOWHERE", "1h", days=1)
        assert False, "应当抛出 SystemExit"
    except SystemExit as e:
        assert "未找到" in str(e)


@patch("dex_client._safe_get")
def test_fetch_ohlcv_empty_ohlcv_raises(mock_get):
    def side_effect(url, **kwargs):
        if "dexscreener.com" in url:
            return [{"pairAddress": "POOL1", "liquidity": {"usd": 10000}, "chainId": "solana"}]
        if "geckoterminal.com" in url:
            return {"data": {"attributes": {"ohlcv_list": []}}}
        return None
    mock_get.side_effect = side_effect
    try:
        dex_client.fetch_ohlcv("sol", "TOKEN1", "1h", days=1)
        assert False, "应当抛出 SystemExit"
    except SystemExit as e:
        assert "空" in str(e)


@patch("dex_client.fetch_ohlcv")
def test_load_gmgn_delegates_to_dex_client(mock_fetch):
    mock_fetch.return_value = (
        ["01-01 00:00", "01-01 01:00"],
        np.array([100.0, 101.0]),
        np.array([101.0, 102.0]),
        np.array([99.0, 100.0]),
        np.array([100.5, 101.0]),
        np.array([1000.0, 2000.0]),
    )
    t, o, h, l, c, v = tool.load_gmgn("sol", "FAKE", "1h", days=1)
    mock_fetch.assert_called_once_with("sol", "FAKE", "1h", 1)
    assert len(c) == 2


def test_gmgn_config_check_noop():
    tool.gmgn_config_check()


def test_build_chart_df_basic():
    pd = pytest.importorskip("pandas")
    from lp_bands_chart import build_chart_df
    n = 600
    t = [str(k) for k in range(n)]
    c = np.linspace(100, 120, n)
    h = c + 1.0
    l = c - 1.0
    bands = [
        (0, 200, 125.0, 95.0, 110.0),
        (200, 400, 130.0, 100.0, 115.0),
        (400, 600, 135.0, 105.0, 120.0),
    ]
    df = build_chart_df(t, c, h, l, bands)
    assert len(df) == n
    assert set(df["seg"].unique()) == {0, 1, 2}
    assert df["upper"].iloc[0] == 125.0
    assert df["lower"].iloc[300] == 100.0


def test_build_chart_df_empty_bands():
    pd = pytest.importorskip("pandas")
    from lp_bands_chart import build_chart_df
    df = build_chart_df([], np.array([]), np.array([]), np.array([]), [])
    assert df.empty

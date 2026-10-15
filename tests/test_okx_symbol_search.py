# -*- coding: utf-8 -*-
"""回归：OKX 现货搜索必须读 v5 的真实字段 baseCcy/quoteCcy.

历史 bug：代码读 `base`/`quote`（OKX 现货接口没有这两个字段），
候选名恒为 "/"，按基础币匹配也永远命中不了。
"""

from unittest.mock import patch

from app.infrastructure.market_data import okx

_INSTRUMENTS = [
    {"instId": "BTC-USDT", "baseCcy": "BTC", "quoteCcy": "USDT"},
    {"instId": "BTC-USDC", "baseCcy": "BTC", "quoteCcy": "USDC"},
    {"instId": "ETH-USDT", "baseCcy": "ETH", "quoteCcy": "USDT"},
]


def _search(query):
    with patch.object(okx, "_safe_get", return_value={"data": _INSTRUMENTS}):
        return okx.search_okx_symbols(query)


def test_candidate_name_is_built_from_base_and_quote_currency():
    out = _search("BTC")

    assert [item["symbol"] for item in out] == ["BTC-USDT", "BTC-USDC"]
    assert out[0]["name"] == "BTC/USDT"


def test_matching_works_when_only_base_currency_is_provided():
    out = _search("eth")

    assert out == [{"symbol": "ETH-USDT", "name": "ETH/USDT"}]


def test_blank_query_returns_no_candidates():
    assert okx.search_okx_symbols("   ") == []

# -*- coding: utf-8 -*-
"""回归：A 股代码解析在 baostock 与腾讯兜底源之间必须一致.

历史 bug：_to_bs_code 支持 'sh.600000' 写法，但 _get_tencent_rsi 直接对原始
字符串取首字符判断市场，配成 'sh.600000' 时会拼出 `szsh.600000`，于是
baostock 一不可用，兜底源必然取不到数据（返回 None）。
"""

from unittest.mock import patch

from app.application.monitors import rsi as rsi_mod

_ROWS = [
    ["2026-01-01", "1", "11.0", "10.0", "11.0", "1"],
    ["2026-01-02", "1", "12.0", "11.0", "12.0", "1"],
    ["2026-01-03", "1", "13.0", "12.0", "13.0", "1"],
    ["2026-01-04", "1", "14.0", "13.0", "14.0", "1"],
    ["2026-01-05", "1", "15.0", "14.0", "15.0", "1"],
    ["2026-01-06", "1", "16.0", "15.0", "16.0", "1"],
]


def test_bare_and_prefixed_codes_normalize_to_the_same_bs_code():
    assert rsi_mod._to_bs_code("600000") == "sh.600000"
    assert rsi_mod._to_bs_code("sh.600000") == "sh.600000"
    assert rsi_mod._to_bs_code("SH.600000") == "sh.600000"
    assert rsi_mod._to_bs_code("000001") == "sz.000001"
    assert rsi_mod._to_bs_code("sz.000001") == "sz.000001"
    assert rsi_mod._to_bs_code("110043") == "sh.110043"   # 可转债 11xxxx 走沪市


def test_tencent_fallback_builds_valid_url_for_prefixed_code():
    urls = []

    class _Response:
        @staticmethod
        def json():
            return {"data": {"sh600000": {"qfqday": _ROWS}}}

    def fake_get(url, **kwargs):
        urls.append(url)
        return _Response()

    with patch.object(rsi_mod.requests, "get", side_effect=fake_get):
        rsi, price = rsi_mod._get_tencent_rsi("sh.600000", length=3, frequency="d")

    assert urls, "腾讯兜底源未被调用"
    assert "param=sh600000,day" in urls[0]
    assert "szsh" not in urls[0]
    assert rsi is not None
    assert price == 15.0

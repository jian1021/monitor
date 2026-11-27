"""GeckoTerminal OHLCV currency 口径映射的回归测试。

背景：`--currency` 默认改成 `quote`（池子比价）后，请求层把它原样发给接口，
而接口只接受 usd / token，导致 HTTP 400 "Invalid currency"。
修复：只在 GeckoClient.ohlcv_page 发请求时把 quote 映射为 currency=token，
上层（CLI 默认值、缓存 series key、测试 FakeClient）继续保持 quote 语义。
"""
from __future__ import annotations

from pathlib import Path

from lprange import cli, data

ROOT = Path(__file__).resolve().parents[1]


def _capture_client():
    c = data.GeckoClient(interval=0)
    calls = []

    def fake_get(path, params=None):
        calls.append((path, params))
        return {"data": {"attributes": {"ohlcv_list": [[1, 1.0, 1.0, 1.0, 1.0, 1.0]]}}}

    c.get = fake_get
    return c, calls


def test_quote_maps_to_token_at_request_layer():
    c, calls = _capture_client()
    c.ohlcv_page("solana", "POOL", None, "quote", "base")
    path, params = calls[0]
    assert path.endswith("/ohlcv/hour")
    assert params["currency"] == "token"
    assert params["token"] == "base"


def test_quote_direction_is_preserved():
    """--token quote 时取倒数比价（quote/base）。"""
    c, calls = _capture_client()
    c.ohlcv_page("solana", "POOL", None, "quote", "quote")
    assert calls[0][1]["currency"] == "token"
    assert calls[0][1]["token"] == "quote"


def test_usd_and_token_pass_through():
    c, calls = _capture_client()
    c.ohlcv_page("solana", "POOL", 123, "usd", "base")
    c.ohlcv_page("solana", "POOL", None, "token", "base")
    assert calls[0][1]["currency"] == "usd"
    assert calls[0][1]["before_timestamp"] == 123
    assert calls[1][1]["currency"] == "token"


def test_never_sends_illegal_currency():
    """接口合法值只有 usd / token，任何内部口径都不能漏出 quote。"""
    c, calls = _capture_client()
    for currency, token in [("quote", "base"), ("quote", "quote"), ("usd", "base"),
                            ("token", "base")]:
        c.ohlcv_page("solana", "POOL", None, currency, token)
    assert {p["currency"] for _, p in calls} <= {"usd", "token"}


def test_upper_layers_keep_quote_semantics():
    """CLI 默认值与缓存 series key 不变，否则 FakeClient 测试和缓存会错位。"""
    args = cli.build_parser().parse_args(["run", "POOL"])
    assert args.currency == "quote"
    assert args.token == "base"
    src = (ROOT / "lprange" / "data.py").read_text(encoding="utf-8")
    assert 'series = f"{currency}:{token}"' in src
    assert '"currency": api_currency' in src

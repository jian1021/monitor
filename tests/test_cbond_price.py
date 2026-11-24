"""可转债价格监控：代码识别、市场前缀、分流与休市跳过。"""
from app.application.monitors import price
from app.infrastructure.market_data import a_share


def test_sh_convertible_bonds_use_sh_prefix():
    for code in ("110059", "111010", "113052", "118000"):
        assert a_share.to_market_code(code) == "sh" + code


def test_sz_convertible_bonds_and_etf_keep_sz():
    for code in ("123107", "127045", "128136", "159967"):
        assert a_share.to_market_code(code) == "sz" + code


def test_existing_prefixes_unchanged():
    assert a_share.to_market_code("515790") == "sh515790"
    assert a_share.to_market_code("600519") == "sh600519"
    assert a_share.to_market_code("000001") == "sz000001"


def test_is_convertible_bond():
    assert a_share.is_convertible_bond("113052") and a_share.is_convertible_bond("127045")
    assert not a_share.is_convertible_bond("159967")
    assert not a_share.is_convertible_bond("600519")


def test_cbond_routes_to_exchange_quote(monkeypatch):
    monkeypatch.setattr(price, "fetch_token_info",
                        lambda *a: (_ for _ in ()).throw(AssertionError("不应走 Dexscreener")))
    monkeypatch.setattr(a_share, "fetch_realtime_price", lambda code: (128.5, "兴业转债"))
    assert price.fetch_price("", "113052", asset_type="cbond") == (128.5, "兴业转债")


def test_cbond_alert_and_closed_day(monkeypatch):
    rule = {"id": 7, "address": "113052", "chain": "", "asset_type": "cbond", "symbol": "兴业转债",
            "target_price": 120.0, "direction": "gte", "alerted": False,
            "last_checked_at": None, "daily_check_at": None}
    sent = []
    monkeypatch.setattr(price, "load_enabled_rules", lambda: [rule])
    monkeypatch.setattr(price, "fetch_price", lambda *a, **k: (128.5, "兴业转债"))
    monkeypatch.setattr(price, "update_rule_price", lambda *a, **k: None)
    monkeypatch.setattr(price, "mark_alerted", lambda rid: None)
    monkeypatch.setattr(price, "resolve_webhook", lambda k: "hook")
    monkeypatch.setattr(price, "send_feishu_msg", lambda h, m: sent.append(m) or True)
    monkeypatch.setattr(price, "ashare_market_open_day", lambda now=None: True)
    price.run_once()
    assert len(sent) == 1 and "可转债" in sent[0] and "gmgn" not in sent[0]

    sent.clear()
    monkeypatch.setattr(price, "ashare_market_open_day", lambda now=None: False)
    price.run_once()
    assert sent == []

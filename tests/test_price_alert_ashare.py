"""价格告警支持 A 股 / 场内 ETF（腾讯实时行情）。

原来 price_alert 只认「合约地址 + 公链」，走 Dexscreener 取价；
A 股没有地址，只能按 6 位代码查行情，因此新增 asset_type 分流。
"""

from app.application.monitors import price
from app.infrastructure.market_data import a_share


# ================= 代码 -> 行情代码 =================
def test_market_code_prefix():
    assert a_share.to_market_code("159967") == "sz159967"   # 深市 ETF
    assert a_share.to_market_code("515790") == "sh515790"   # 沪市 ETF
    assert a_share.to_market_code("600519") == "sh600519"   # 沪市股票
    assert a_share.to_market_code("000001") == "sz000001"   # 深市股票


def test_market_code_passthrough_and_invalid():
    assert a_share.to_market_code("sh515790") == "sh515790"
    with __import__("pytest").raises(ValueError):
        a_share.to_market_code("123")


# ================= 腾讯行情解析 =================
class _Resp:
    def __init__(self, text):
        self.text = text
        self.encoding = None


def _patch_quote(monkeypatch, body):
    monkeypatch.setattr(a_share.requests, "get",
                        lambda *a, **kw: _Resp(body))


def test_parse_quote(monkeypatch):
    body = ('v_sz159967="51~创业板成长ETF华夏~159967~0.451~0.436~0.430~26740704";')
    _patch_quote(monkeypatch, body)

    price_val, name = a_share.fetch_realtime_price("159967")

    assert price_val == 0.451
    assert name == "创业板成长ETF华夏"


def test_suspended_returns_no_price(monkeypatch):
    """停牌时当前价为 0，应当视为无有效报价。"""
    body = 'v_sz159967="51~创业板成长ETF华夏~159967~0.000~0.436~0.430~0";'
    _patch_quote(monkeypatch, body)

    price_val, name = a_share.fetch_realtime_price("159967")

    assert price_val is None
    assert name == "创业板成长ETF华夏"


def test_malformed_response(monkeypatch):
    _patch_quote(monkeypatch, "无权访问")
    assert a_share.fetch_realtime_price("159967") == (None, None)


# ================= 按 asset_type 分流 =================
def test_fetch_price_routes_ashare(monkeypatch):
    """ashare 规则必须走腾讯行情，不得落到 Dexscreener。"""
    seen = {}

    def fake_token_info(chain, address):
        raise AssertionError("ashare 规则不应调用 Dexscreener")

    def fake_realtime(code):
        seen["code"] = code
        return 0.451, "创业板成长ETF华夏"

    monkeypatch.setattr(price, "fetch_token_info", fake_token_info)
    monkeypatch.setattr(a_share, "fetch_realtime_price", fake_realtime)

    value, name = price.fetch_price("", "159967", asset_type="ashare")

    assert value == 0.451
    assert name == "创业板成长ETF华夏"
    assert seen["code"] == "159967"


def test_fetch_price_still_routes_token(monkeypatch):
    """token 规则维持 Dexscreener 原路径。"""
    called = {}

    def fake_token_info(chain, address):
        called["args"] = (chain, address)
        return {"symbol": "PONS", "price": {"price": "0.19"}}, "dexscreener"

    monkeypatch.setattr(price, "fetch_token_info", fake_token_info)

    value, symbol = price.fetch_price("robinhood", "0x" + "a" * 40)

    assert value == 0.19
    assert symbol == "PONS"
    assert called["args"][0] == "robinhood"


# ================= 表结构与列 =================
def test_schema_has_asset_type():
    assert "asset_type" in price.CREATE_TABLE_SQL
    assert "asset_type" in price.RULE_COLUMNS
    assert any("asset_type" in ddl for ddl in price._MIGRATIONS)


def test_rule_columns_match_select_order():
    """页面用 RULE_COLUMNS 直接构造 DataFrame，顺序必须与查询一致。"""
    assert price.RULE_COLUMNS[0] == "id"
    assert price.RULE_COLUMNS.index("asset_type") == 3
    assert price.RULE_COLUMNS[-1] == "created_at"

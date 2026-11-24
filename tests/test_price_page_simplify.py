"""价格监控页简化：自动识别类型、目标价更新。"""
import pathlib

from app.application.monitors import price

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _detect():
    src = (ROOT / "pages/st_monitor_price.py").read_text(encoding="utf-8")
    start, end = src.index("def detect_type"), src.index("def lookup")
    from app.infrastructure.market_data.a_share import is_convertible_bond
    ns = {"is_convertible_bond": is_convertible_bond}
    exec(src[start:end], ns)
    return ns["detect_type"]


def test_detect_type():
    d = _detect()
    assert d("159967") == "ashare"
    assert d("600519") == "ashare"
    assert d("113052") == "cbond"
    assert d("127045") == "cbond"
    assert d("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v") == "token"


def test_update_rule_target_rejects_invalid():
    assert price.update_rule_target(1, 0, "gte") is False
    assert price.update_rule_target(1, 10, "eq") is False


def test_update_rule_target_resets_alerted(monkeypatch):
    sql = {}

    class C:
        def execute(self, q, args):
            sql["q"], sql["a"] = q, args
        def close(self):
            pass
    monkeypatch.setattr(price, "get_db_client", lambda: C())
    assert price.update_rule_target(3, 1.5, "lte") is True
    assert "alerted = 0" in sql["q"] and sql["a"] == [1.5, "lte", 3]


def test_manual_refresh_does_not_touch_checked():
    src = (ROOT / "pages/st_monitor_price.py").read_text(encoding="utf-8")
    assert "touch_checked=False" in src

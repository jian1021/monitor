"""价格规则的两个时间戳：最近取价 / 最近定时检查。

- last_price_at：只要成功取到报价就更新（页面手动刷新、后台定时检查都算）
- last_checked_at：只在完整定时检查（含告警判定）结束后推进，是每日门控的唯一依据

页面「🔄 刷新现价」必须传 touch_checked=False，否则手动查一次价就会把当天
09:30 的定时检查跳过；但用户需要看到「刚刷新过」，所以单独加一个取价时间列。
"""

import pathlib
from datetime import datetime, timedelta, timezone

from app.application.monitors import price

ROOT = pathlib.Path(__file__).resolve().parents[1]
BJ = timezone(timedelta(hours=8))


class _FakeClient:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return type("R", (), {"rows": []})()

    def close(self):
        pass


# ================= 表结构 =================
def test_schema_has_last_price_at_without_breaking_column_order():
    assert "last_price_at" in price.CREATE_TABLE_SQL
    assert any("last_price_at" in ddl for ddl in price._MIGRATIONS)
    assert "last_price_at" in price.RULE_COLUMNS
    # 页面用 RULE_COLUMNS 直接构造 DataFrame，既有索引约束不能破坏
    assert price.RULE_COLUMNS.index("daily_check_at") == 10
    assert price.RULE_COLUMNS[-1] == "created_at"


# ================= 回写语义 =================
def test_manual_refresh_updates_price_time_only(monkeypatch):
    """手动刷新：推进 last_price_at，绝不推进 last_checked_at。"""
    fake = _FakeClient()
    monkeypatch.setattr(price, "get_db_client", lambda: fake)

    price.update_rule_price(7, 0.451, touch_checked=False)
    sql, params = fake.calls[0]
    assert "last_price_at = datetime('now')" in sql
    assert "last_checked_at" not in sql
    assert params == [0.451, 7]


def test_scheduled_check_updates_both_timestamps(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(price, "get_db_client", lambda: fake)

    price.update_rule_price(7, 0.451, touch_checked=True)
    sql, params = fake.calls[0]
    assert "last_price_at = datetime('now')" in sql
    assert "last_checked_at = datetime('now')" in sql
    assert params == [0.451, 7]


def test_daily_gate_depends_only_on_last_checked():
    """取价时间变化不影响每日门控：今天 09:30 已检查过，19:00 仍不再检查。"""
    rule = {"daily_check_at": "09:30", "last_checked_at": "2026-11-27 01:30:00",
            "last_price_at": "2026-11-27 11:42:00"}
    assert price.due_for_daily_check(rule, now=datetime(2026, 11, 27, 19, 0, tzinfo=BJ)) is False


# ================= 读取列不错位 =================
def test_load_all_rules_returns_price_time(monkeypatch):
    fake = _FakeClient()
    row = [5, "159967", "", "ashare", "ETF", 0.39, "lte", 1, 0.451,
           "2026-11-27 01:30:00", "09:30", 0,
           "2026-11-27 11:42:00", "2026-11-23 00:49:17"]

    class _RS:
        rows = [row]

    def _exec(sql, params=None):
        fake.calls.append((sql, params))
        return _RS()

    fake.execute = _exec
    monkeypatch.setattr(price, "get_db_client", lambda: fake)
    rules = price.load_all_rules()
    assert "last_price_at" in fake.calls[0][0]
    assert rules[0]["last_price_at"] == "2026-11-27 11:42:00"
    assert rules[0]["created_at"] == "2026-11-23 00:49:17"   # 末尾列没有错位


# ================= 页面 =================
def test_page_shows_both_time_columns_and_clears_editor_cache():
    src = (ROOT / "pages" / "st_monitor_price.py").read_text(encoding="utf-8")
    assert '"最近取价"' in src and '"最近定时检查"' in src
    assert "touch_checked=False" in src                        # 手动刷新不能推进门控
    assert 'st.session_state.pop("rules_editor", None)' in src  # 刷新后重建表格

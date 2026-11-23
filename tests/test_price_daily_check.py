"""价格规则每日定时检查（daily_check_at）。

A股/ETF 规则默认每天北京时间 09:30 检查一次：
- 未到时刻不检查；
- 今天该时刻后已检查过则跳过；
- 无 daily_check_at 的规则不受限制，跟随调度轮询。
"""

from datetime import datetime, timedelta, timezone

from app.application.monitors import price

BJ = timezone(timedelta(hours=8))


def _rule(daily=None, last_checked=None):
    return {"daily_check_at": daily, "last_checked_at": last_checked}


# ================= normalize_daily_check =================
def test_normalize_daily_check():
    assert price.normalize_daily_check("9:30") == "09:30"
    assert price.normalize_daily_check(" 09:05 ") == "09:05"
    assert price.normalize_daily_check(None) is None
    assert price.normalize_daily_check("") is None
    assert price.normalize_daily_check("abc") is None
    assert price.normalize_daily_check("9") is None


# ================= due_for_daily_check =================
def test_no_daily_check_follows_polling():
    """无 daily_check_at 的规则永远到期（跟随轮询）。"""
    assert price.due_for_daily_check(_rule(None)) is True


def test_before_target_time_not_due():
    now = datetime(2026, 11, 23, 8, 0, tzinfo=BJ)
    assert price.due_for_daily_check(_rule("09:30"), now=now) is False


def test_after_target_never_checked_is_due():
    now = datetime(2026, 11, 23, 9, 31, tzinfo=BJ)
    assert price.due_for_daily_check(_rule("09:30"), now=now) is True


def test_already_checked_today_not_due():
    """今天 09:30 后已查过（last_checked_at 为 UTC 存储）不再检查。"""
    now = datetime(2026, 11, 23, 17, 0, tzinfo=BJ)  # = UTC 09:00
    last_utc = "2026-11-23 09:05:00"  # 北京 17:05，晚于 09:30 目标
    assert price.due_for_daily_check(_rule("09:30", last_utc), now=now) is False


def test_checked_yesterday_is_due():
    now = datetime(2026, 11, 24, 9, 31, tzinfo=BJ)
    last_utc = "2026-11-23 01:31:00"  # 北京 11-23 09:31（昨天）
    assert price.due_for_daily_check(_rule("09:30", last_utc), now=now) is True


def test_checked_today_before_target_is_due():
    """极端情况：今天查过但早于目标时刻（如手动查价），到点后仍要查。"""
    now = datetime(2026, 11, 23, 9, 40, tzinfo=BJ)
    last_utc = "2026-11-23 00:10:00"  # 北京 08:10，早于 09:30
    assert price.due_for_daily_check(_rule("09:30", last_utc), now=now) is True


def test_invalid_daily_falls_back_to_polling():
    assert price.due_for_daily_check(_rule("bad")) is True


def test_naive_now_treated_as_beijing():
    """now 无 tzinfo 时按北京时间处理。"""
    assert price.due_for_daily_check(
        _rule("09:30"), now=datetime(2026, 11, 23, 8, 0)) is False


# ================= 表结构与列 =================
def test_schema_has_daily_check_at():
    assert "daily_check_at" in price.CREATE_TABLE_SQL
    assert price.ASHARE_DEFAULT_DAILY_CHECK == "09:30"
    assert any("daily_check_at" in ddl for ddl in price._MIGRATIONS)


def test_rule_columns_match_select_order():
    assert "daily_check_at" in price.RULE_COLUMNS
    assert price.RULE_COLUMNS.index("daily_check_at") == 10
    assert price.RULE_COLUMNS[-1] == "created_at"

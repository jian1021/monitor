from datetime import date, datetime

from app.infrastructure.market_data import trade_calendar as tc
from app.infrastructure.market_data.trade_calendar import TradeCalendar


class Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t


def test_holiday_skipped_and_cached():
    calls = []
    # 2026-10-01 国庆（周四）不在交易日集合里
    days = {date(2026, 9, 30), date(2026, 10, 9)}

    def fetch(y):
        calls.append(y)
        return days

    cal = TradeCalendar(fetch)
    assert cal.is_trading_day(date(2026, 9, 30)) is True
    assert cal.is_trading_day(datetime(2026, 10, 1, 15, 5)) is False
    assert cal.is_trading_day(date(2026, 10, 3)) is False   # 周末不查询也为 False
    assert calls == [2026]                                  # 一年只查一次


def test_failure_degrades_to_weekdays_and_retries_later():
    clock, calls = Clock(), []

    def fetch(y):
        calls.append(y)
        return None

    cal = TradeCalendar(fetch, clock=clock)
    assert cal.is_trading_day(date(2026, 10, 1)) is True    # 降级：工作日视为交易日
    assert cal.is_trading_day(date(2026, 10, 2)) is True
    assert calls == [2026]                                  # 失败期内不重复请求
    clock.t = tc.RETRY_SECONDS + 1
    cal.is_trading_day(date(2026, 10, 5))
    assert calls == [2026, 2026]


def test_policy_skips_national_day():
    from datetime import time as dtime
    from app.application.monitoring.lanes import TZ, DailyAtPolicy
    trading = {date(2026, 9, 30), date(2026, 10, 9)}
    cal = TradeCalendar(lambda y: trading)
    p = DailyAtPolicy(dtime(15, 5), is_trading_day=cal.is_trading_day)
    start = datetime(2026, 9, 30, 16, 0, tzinfo=TZ).timestamp()
    assert p.next_after(start) == datetime(2026, 10, 9, 15, 5, tzinfo=TZ).timestamp()

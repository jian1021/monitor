"""A 股交易日历：baostock query_trade_dates，按年缓存，失败降级为「仅排除周末」。

供调度器 DailyAtPolicy(is_trading_day=...) 使用，让收盘后任务跳过法定节假日。
- 每个自然年只查询一次并缓存；查询失败不缓存失败结果，RETRY_SECONDS 后重试。
- baostock 是进程级全局会话：内部加锁，且只在 close 通道（单线程）与启动时调用。
"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime
from typing import Callable

RETRY_SECONDS = 3600

FetchYear = Callable[[int], "set[date] | None"]


def _fetch_year_baostock(year: int) -> set[date] | None:
    """返回该年所有交易日；失败返回 None。"""
    try:
        import baostock as bs
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ 交易日历：baostock 不可用，降级为仅排除周末: {e}")
        return None
    logged_in = False
    try:
        lg = bs.login()
        logged_in = lg.error_code == "0"
        if not logged_in:
            print(f"⚠️ 交易日历：baostock 登录失败 {lg.error_msg}")
            return None
        rs = bs.query_trade_dates(start_date=f"{year}-01-01", end_date=f"{year}-12-31")
        if rs.error_code != "0":
            print(f"⚠️ 交易日历：查询失败 {rs.error_msg}")
            return None
        days: set[date] = set()
        idx_d = rs.fields.index("calendar_date")
        idx_t = rs.fields.index("is_trading_day")
        while rs.next():
            row = rs.get_row_data()
            if str(row[idx_t]) == "1":
                days.add(date.fromisoformat(row[idx_d]))
        if not days:
            return None  # 未来年份尚未发布等情况：视为不可用
        return days
    except Exception as e:  # noqa: BLE001
        print(f"⚠️ 交易日历：查询异常，降级为仅排除周末: {e}")
        return None
    finally:
        if logged_in:
            try:
                bs.logout()
            except Exception:  # noqa: BLE001
                pass


class TradeCalendar:
    def __init__(self, fetch_year: FetchYear = _fetch_year_baostock,
                 clock: Callable[[], float] = time.time) -> None:
        self._fetch, self._clock = fetch_year, clock
        self._years: dict[int, set[date]] = {}
        self._failed_at: dict[int, float] = {}
        self._lock = threading.Lock()

    def _year(self, year: int) -> set[date] | None:
        if year in self._years:
            return self._years[year]
        failed = self._failed_at.get(year)
        if failed is not None and self._clock() - failed < RETRY_SECONDS:
            return None
        days = self._fetch(year)
        if days:
            self._years[year] = days
            self._failed_at.pop(year, None)
            print(f"📅 交易日历：已加载 {year} 年 {len(days)} 个交易日")
            return days
        self._failed_at[year] = self._clock()
        return None

    def is_trading_day(self, d: date | datetime) -> bool:
        day = d.date() if isinstance(d, datetime) else d
        if day.weekday() >= 5:
            return False
        with self._lock:
            days = self._year(day.year)
        if days is None:
            return True  # 降级：工作日一律视为交易日（宁可多跑，不漏跑）
        return day in days


_default = TradeCalendar()


def a_share_trading_day(d: date | datetime) -> bool:
    return _default.is_trading_day(d)

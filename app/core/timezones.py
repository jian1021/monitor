"""全站统一时区契约：所有面向用户展示的时间固定用 UTC+8（北京时间）。

数据库仍统一存 UTC（`datetime('now')` 不带时区，或带时区的 ISO 串），这里只约束
「展示」与「文案」：Streamlit Cloud / GCE 的运行环境时区不一定是中国时区
（Cloud 默认 UTC），若用裸 `.astimezone()` 跟随本机时区，同一条记录在不同环境
会显示成不同时间，看起来像「时间没更新 / 告警没发出去」。

改时间口径只改这里，不要在页面里各自 `.astimezone()`。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# 固定 UTC+8，不依赖 TZ 环境变量或系统 tzdata
DISPLAY_TZ = timezone(timedelta(hours=8), "UTC+8")


def now_display(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """当前北京时间的字符串，不依赖运行环境时区。"""
    return datetime.now(DISPLAY_TZ).strftime(fmt)


def now_iso() -> str:
    """当前北京时间的带时区 ISO 串（用于飞书消息等直接展示的文案）。

    注意：不要拿它写入需要按字符串比较的去重表（如 meteora_scanner.alerts.ts），
    那边继续用 UTC 偏移的 `_now_iso()` 以保持与历史数据一致。
    """
    return datetime.now(DISPLAY_TZ).isoformat(timespec="seconds")

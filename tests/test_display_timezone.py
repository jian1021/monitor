"""时间展示固定 UTC+8（北京时间）的契约与回归守卫。

背景：原 `to_local_display` 用裸 `.astimezone()` 跟随本机时区，本地 Mac 看着
正常，Streamlit Cloud / GCE 默认 UTC 时页面时间就比北京时间早 8 小时。
"""
from __future__ import annotations

import ast
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.core.timezones import DISPLAY_TZ, now_display, now_iso
from app.interfaces.timefmt import to_local_display

ROOT = Path(__file__).resolve().parents[1]
TIMEFMT_SRC = (ROOT / "app" / "interfaces" / "timefmt.py").read_text(encoding="utf-8")

TIMEFMT_PAGES = (
    "pages/dashboard.py",
    "pages/st_onchain_token.py",
    "pages/st_mainstream_crypto.py",
    "pages/lp_position_alert.py",
    "pages/lp_range.py",
    "pages/st_monitor_price.py",
)


def test_display_tz_is_fixed_utc_plus_8():
    assert DISPLAY_TZ.utcoffset(None) == timedelta(hours=8)
    assert DISPLAY_TZ is not timezone.utc


def test_naive_utc_string_shows_beijing_time():
    assert to_local_display("2026-11-05 08:32:45") == "2026-11-05 16:32"
    assert to_local_display("2026-11-05 08:32:45", fmt="%Y-%m-%d %H:%M:%S") == "2026-11-05 16:32:45"


def test_aware_iso_strings_all_normalize_to_beijing():
    assert to_local_display("2026-11-05T00:32:45Z") == "2026-11-05 08:32"
    assert to_local_display("2026-11-05T00:32:45+00:00") == "2026-11-05 08:32"
    assert to_local_display("2026-11-05T08:32:45+08:00") == "2026-11-05 08:32"
    assert to_local_display("2026-11-05T03:32:45-05:00") == "2026-11-05 16:32"


def test_display_is_independent_of_process_timezone(monkeypatch):
    """换 TZ 环境变量后输出必须不变（这正是修复的核心）。"""
    if not hasattr(time, "tzset"):
        pytest.skip("tzset 不可用")
    old = os.environ.get("TZ")
    try:
        for zone in ("UTC", "America/New_York", "Asia/Shanghai"):
            monkeypatch.setenv("TZ", zone)
            time.tzset()
            assert to_local_display("2026-11-05 08:32:45") == "2026-11-05 16:32"
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def test_now_helpers_use_display_tz():
    assert now_display("%Y-%m-%d") == datetime.now(DISPLAY_TZ).strftime("%Y-%m-%d")
    assert now_iso().endswith("+08:00")


def test_timefmt_has_no_bare_astimezone():
    """禁止回退到跟随本机时区的写法（AST 判定，忽略文档字符串里的举例）。"""
    assert "astimezone(DISPLAY_TZ)" in TIMEFMT_SRC
    tree = ast.parse(TIMEFMT_SRC)
    bare = [n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "astimezone"
            and not n.args and not n.keywords]
    assert bare == []


def test_all_time_pages_use_fresh_timefmt():
    """6 个展示时间的页面必须走重载守卫，否则 Cloud 热更新后拿到旧函数。"""
    for rel in TIMEFMT_PAGES:
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "fresh_timefmt()" in src, f"{rel} 未接入重载守卫"
        assert "from app.interfaces.timefmt import to_local_display" not in src, \
            f"{rel} 仍在用值拷贝 import（Cloud 缓存旧模块）"


def test_meteora_status_time_and_feishu_copy_use_display_tz():
    page = (ROOT / "pages" / "meteora_scanner.py").read_text(encoding="utf-8")
    assert "def now_str() -> str:\n    return now_display()" in page
    assert ".astimezone()" not in page

    feishu = (ROOT / "app" / "application" / "meteora_scanner" / "feishu.py").read_text(
        encoding="utf-8")
    assert "from app.core.timezones import now_iso" in feishu
    # 去重窗口仍用原来的本机时区 ISO，不能混用 +08:00 破坏 alerts.ts 的字符串比较
    assert "datetime.now(timezone.utc).astimezone() - timedelta(hours=window)" in feishu
    db = (ROOT / "app" / "application" / "meteora_scanner" / "db.py").read_text(
        encoding="utf-8")
    assert 'return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")' in db


def test_fresh_timefmt_recovers_stale_cached_module():
    """模拟 Cloud：sys.modules 里是旧实现，页面调用守卫后应拿回磁盘新版。"""
    from app.interfaces import timefmt

    original = timefmt.to_local_display
    original_loaded_at = getattr(timefmt, "_ui_loaded_at", None)

    def stale(v, *a, **k):
        return "STALE"

    timefmt.to_local_display = stale
    timefmt._ui_loaded_at = 0.0  # 强制判定「源文件已更新」
    try:
        from app.interfaces.reload_guard import fresh_timefmt

        fn = fresh_timefmt()
        assert fn is not stale
        assert fn("2026-11-05 08:32:45") == "2026-11-05 16:32"
    finally:
        timefmt.to_local_display = original
        if original_loaded_at is None:
            timefmt.__dict__.pop("_ui_loaded_at", None)
        else:
            timefmt._ui_loaded_at = original_loaded_at

"""时间戳展示与重复标的守卫。

1. 库里 `datetime('now')` 是 UTC，页面直接展示会比北京时间早 8 小时（看起来像没告警）。
2. add_new_asset 早年没有去重，库里因此留下 ASTER-USDT 的 1W / 1D 两条，
   后台会重复取数、重复告警。
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.application.assets import service as asset_service
from app.interfaces.timefmt import to_local_display


def test_utc_timestamp_converted_to_local():
    expected = datetime(2026, 11, 5, 8, 32, 45, tzinfo=timezone.utc).astimezone().strftime(
        "%Y-%m-%d %H:%M")
    assert to_local_display("2026-11-05 08:32:45") == expected


def test_empty_timestamp_uses_fallback():
    assert to_local_display(None) == "-"
    assert to_local_display("") == "-"
    assert to_local_display(None, fallback="—") == "—"


def test_unparsable_timestamp_returned_as_is():
    assert to_local_display("not-a-time") == "not-a-time"


def test_iso_with_timezone_is_converted():
    expected = datetime(2026, 11, 5, 0, 32, 45, tzinfo=timezone.utc).astimezone().strftime(
        "%Y-%m-%d %H:%M")
    assert to_local_display("2026-11-05T00:32:45Z") == expected


def test_add_new_asset_rejects_duplicate():
    db = SimpleNamespace(
        execute=lambda *a, **k: SimpleNamespace(rows=[(1, "ASTER-USDT", "1W")]),
        close=lambda: None,
    )
    with patch.object(asset_service, "get_db_client", return_value=db):
        assert asset_service.add_new_asset("crypto", "ASTER-USDT", "ASTER") is False


def test_add_new_asset_inserts_when_absent():
    calls = []

    def execute(sql, params=None):
        calls.append((sql, params))
        if sql.strip().startswith("SELECT"):
            return SimpleNamespace(rows=[])
        return SimpleNamespace(rows=[])

    db = SimpleNamespace(execute=execute, close=lambda: None)
    with patch.object(asset_service, "get_db_client", return_value=db):
        assert asset_service.add_new_asset("token", "0xabc", "ABC", "base", "1h") is True
    assert any(sql.strip().startswith("INSERT") for sql, _ in calls)
    insert_params = next(p for s, p in calls if s.strip().startswith("INSERT"))
    assert insert_params[1] == "0xabc"
    assert insert_params[3] == "base"

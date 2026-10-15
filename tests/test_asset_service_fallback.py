# -*- coding: utf-8 -*-
"""回归：老库缺列时 fetch_all_assets 必须真的逐档回退，而不是重复同一条 SQL.

历史 bug：except 分支复制了主查询（列完全相同），一旦主查询因缺列失败，
回退查询会以同样的原因失败并把异常抛到页面，监控标的管理页整页报错。
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.application.assets import service


def test_fetch_all_assets_degrades_when_newer_columns_are_missing():
    db = Mock()
    db.execute.side_effect = [
        Exception("no such column: alarm_active"),
        Exception("no such column: chain"),
        SimpleNamespace(rows=[(1, "etf", "510300", "沪深300ETF", 1, "2026-01-01")]),
    ]
    with patch.object(service, "get_db_client", return_value=db):
        df = service.fetch_all_assets()

    assert list(df.columns) == service.ASSET_COLUMNS
    row = df.iloc[0].to_dict()
    assert row["code"] == "510300"
    assert row["chain"] == "sol"        # 缺列时回退默认值
    assert row["timeframe"] == "1d"
    assert row["alarm_active"] is False
    assert row["last_alert_at"] is None
    db.close.assert_called_once()


def test_fetch_all_assets_keeps_full_columns_when_query_succeeds():
    db = Mock()
    db.execute.return_value = SimpleNamespace(rows=[
        (2, "token", "mint", "MEME", 1, "2026-01-02", "base", "4h", 1, "2026-01-03"),
    ])
    with patch.object(service, "get_db_client", return_value=db):
        df = service.fetch_all_assets()

    row = df.iloc[0].to_dict()
    assert row["chain"] == "base"
    assert row["timeframe"] == "4h"
    assert row["alarm_active"] is True
    assert row["last_alert_at"] == "2026-01-03"
    assert db.execute.call_count == 1

"""module_intervals 表读写与默认回退测试."""
from unittest.mock import patch, MagicMock

import db
from db import DEFAULT_INTERVALS


class FakeRows:
    def __init__(self, rows):
        self.rows = rows


def make_fake_client(rows=None, fail_execute=False):
    client = MagicMock()
    if rows is not None:
        client.execute.return_value = FakeRows(rows)
    if fail_execute:
        client.execute.side_effect = RuntimeError("boom")
    return client


def test_defaults_used_when_no_db():
    with patch("db.get_db_client", return_value=None):
        intervals = db.get_module_intervals()
    assert intervals["rsi"] == 1440
    assert intervals["crypto"] == 30
    assert intervals["lp_alert"] == 5


def test_db_values_override_defaults():
    rows = [("rsi", "60"), ("crypto", 15)]
    with patch("db.get_db_client", return_value=make_fake_client(rows)):
        intervals = db.get_module_intervals()
    assert intervals["rsi"] == 60
    assert intervals["crypto"] == 15
    assert intervals["meteora_pump"] == 5


def test_unknown_module_from_db_is_kept():
    rows = [("future_mod", 7)]
    with patch("db.get_db_client", return_value=make_fake_client(rows)):
        intervals = db.get_module_intervals()
    assert intervals["future_mod"] == 7
    assert intervals["rsi"] == 1440


def test_invalid_db_value_falls_back_to_default():
    rows = [("rsi", "not-a-number"), ("crypto", 0)]
    with patch("db.get_db_client", return_value=make_fake_client(rows)):
        intervals = db.get_module_intervals()
    assert intervals["rsi"] == 1440
    assert intervals["crypto"] == 1  # 0 被钳制到最小 1 分钟


def test_custom_defaults_dict_respected():
    custom = {"rsi": 99}
    with patch("db.get_db_client", return_value=None):
        intervals = db.get_module_intervals(defaults=custom)
    assert intervals["rsi"] == 99


def test_get_module_intervals_fails_gracefully():
    client = make_fake_client(fail_execute=True)
    with patch("db.get_db_client", return_value=client):
        intervals = db.get_module_intervals()
    assert intervals == DEFAULT_INTERVALS


def test_update_module_interval_writes_upsert():
    client = make_fake_client()
    with patch("db.get_db_client", return_value=client):
        assert db.update_module_interval("rsi", 30) is True
    sql = client.execute.call_args[0][0]
    assert "ON CONFLICT(module_name) DO UPDATE" in sql
    args = client.execute.call_args[0][1]
    assert args[0] == "rsi"
    assert args[1] == 30


def test_update_module_interval_clamps_to_min_one():
    client = make_fake_client()
    with patch("db.get_db_client", return_value=client):
        db.update_module_interval("lp_alert", 0)
    args = client.execute.call_args[0][1]
    assert args[1] == 1


def test_update_module_interval_fails_gracefully():
    client = make_fake_client(fail_execute=True)
    with patch("db.get_db_client", return_value=client):
        assert db.update_module_interval("rsi", 30) is False
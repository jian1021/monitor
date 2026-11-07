"""ETF 专属飞书 Webhook 的守卫测试。

需求：ETF 告警发到独立的飞书群，不能污染全局告警群；未配置时仍要
安全回落到全局 Webhook，避免「配错一次，告警全静默」。
"""

from pathlib import Path

from app.infrastructure.db import module_webhooks


MAIN_PY = Path(__file__).resolve().parents[1] / "main.py"


class _FakeResult:
    def __init__(self, rows):
        self.rows = rows


class _FakeClient:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []
        self.closed = False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if sql.strip().upper().startswith("SELECT"):
            return _FakeResult(self.rows)
        return _FakeResult([])

    def close(self):
        self.closed = True


def test_get_module_webhook_returns_stored_value(monkeypatch):
    client = _FakeClient(rows=[("https://example.com/hook/abc",)])
    monkeypatch.setattr(module_webhooks, "ensure_table", lambda: True)
    monkeypatch.setattr(module_webhooks, "get_db_client", lambda: client)
    assert module_webhooks.get_module_webhook("etf_rsi", "global") == "https://example.com/hook/abc"


def test_get_module_webhook_falls_back_when_absent(monkeypatch):
    client = _FakeClient(rows=[])
    monkeypatch.setattr(module_webhooks, "ensure_table", lambda: True)
    monkeypatch.setattr(module_webhooks, "get_db_client", lambda: client)
    assert module_webhooks.get_module_webhook("etf_rsi", "global") == "global"


def test_get_module_webhook_falls_back_without_db(monkeypatch):
    monkeypatch.setattr(module_webhooks, "ensure_table", lambda: True)
    monkeypatch.setattr(module_webhooks, "get_db_client", lambda: None)
    assert module_webhooks.get_module_webhook("etf_rsi", "global") == "global"


def test_set_module_webhook_empty_deletes_row(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(module_webhooks, "ensure_table", lambda: True)
    monkeypatch.setattr(module_webhooks, "get_db_client", lambda: client)
    assert module_webhooks.set_module_webhook("etf_rsi", "  ") is True
    sql, _params = client.executed[-1]
    assert sql.strip().upper().startswith("DELETE")


def test_etf_runner_uses_module_webhook():
    """ETF 告警必须走模块专属 Webhook，别再用全局 FEISHU_WEBHOOK。"""
    source = MAIN_PY.read_text(encoding="utf-8")
    start = source.index("def run_etf_monitor(")
    end = source.index("def run_crypto_monitor(")
    body = source[start:end]
    assert 'module_webhook("etf_rsi")' in body


def test_module_webhook_helper_defaults_to_global():
    source = MAIN_PY.read_text(encoding="utf-8")
    start = source.index("def module_webhook(")
    end = source.index("def _send_rsi_messages(")
    body = source[start:end]
    assert "get_module_webhook(module_key, FEISHU_WEBHOOK)" in body

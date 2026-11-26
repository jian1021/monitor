"""全局测试夹具。

告警发送前会调用 resolve_webhook() 查询模块专属 Webhook（module_webhooks 表）。
本地 .env 里配有真实 LIBSQL_URL 时，没打桩的测试会真的连 Turso：慢，而且断网就卡住。
这里默认把 module_webhooks 的数据库连接置空（resolve_webhook 回落到全局 Webhook）；
需要模拟数据库的测试（如 test_module_webhook.py）用自己的 monkeypatch 覆盖即可。
"""

import pytest


@pytest.fixture(autouse=True)
def _no_real_webhook_db(monkeypatch):
    from app.infrastructure.db import module_webhooks

    monkeypatch.setattr(module_webhooks, "get_db_client", lambda: None)

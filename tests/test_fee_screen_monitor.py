# -*- coding: utf-8 -*-
"""fee-screen 监控模块单测（打桩，无网络 / 无飞书）。"""

from types import SimpleNamespace

from app.application.fee_screen import monitor as fee_monitor


class _StubNotifier:
    def __init__(self):
        self.sent = []

    def send(self, webhook, message):
        self.sent.append(message)
        return SimpleNamespace(ok=True, detail="stub")


def _patch_common(monkeypatch, webhook="https://feishu.example/hook"):
    monkeypatch.setattr(fee_monitor, "get_webhook", lambda: webhook)
    monkeypatch.setattr(fee_monitor, "FeishuNotifier", _StubNotifier)
    monkeypatch.setattr(fee_monitor, "load_monitor_config",
                        lambda: dict(fee_monitor.DEFAULT_MONITOR))
    # 数据库不可用时去重放行（宁可重复不漏报），这里直接打桩避免依赖真实库
    monkeypatch.setattr(fee_monitor, "filter_unpushed",
                        lambda module, keys, ttl_hours: list(keys))
    monkeypatch.setattr(fee_monitor, "mark_pushed", lambda module, keys: True)
    monkeypatch.setattr(fee_monitor, "log_alert", lambda *a, **k: True)
    monkeypatch.setattr(fee_monitor, "load_pool_state", lambda: {})
    monkeypatch.setattr(fee_monitor, "save_pool_state", lambda state: True)


def test_ci_without_service_skips_quietly(monkeypatch):
    """RHP_AUTO_START=0（CI）且服务不可用：静默跳过，不发数据源告警刷屏。"""
    _patch_common(monkeypatch)

    class _NoService:
        @staticmethod
        def auto_start_enabled():
            return False

    monkeypatch.setattr(fee_monitor.src, "rhpools_client", _NoService)
    monkeypatch.setattr(
        "app.application.fee_screen.service.run_screening",
        lambda cfg=None: SimpleNamespace(ok=False, reason="rhpools 索引服务不可用",
                                         data_warnings=[], evaluations=[],
                                         all_evaluations=[], index_status=None,
                                         chain={"ok": True}, meteora={},
                                         fetched_at=0.0))
    notifier = _StubNotifier()
    summary = fee_monitor.run_fee_screen_monitor(notifier=notifier)
    assert summary["alerts_sent"] == 0
    assert "静默跳过" in summary["reason"]
    assert notifier.sent == []          # 不推送


def test_source_down_alerts_outside_ci(monkeypatch):
    """正常环境（允许自启）下服务不可用：应发数据源异常告警。"""
    _patch_common(monkeypatch)

    class _AutoStart:
        @staticmethod
        def auto_start_enabled():
            return True

    monkeypatch.setattr(fee_monitor.src, "rhpools_client", _AutoStart)
    monkeypatch.setattr(
        "app.application.fee_screen.service.run_screening",
        lambda cfg=None: SimpleNamespace(ok=False, reason="rhpools 索引服务不可用",
                                         data_warnings=[], evaluations=[],
                                         all_evaluations=[], index_status=None,
                                         chain={"ok": True}, meteora={},
                                         fetched_at=0.0))
    notifier = _StubNotifier()
    summary = fee_monitor.run_fee_screen_monitor(notifier=notifier)
    assert summary["alerts_sent"] == 1
    assert any("数据源异常" in m for m in notifier.sent)


def test_missing_webhook_skips_before_screening(monkeypatch):
    """Webhook 未配置：不拉数据、不告警，直接跳过。"""
    _patch_common(monkeypatch, webhook="")

    def _boom(cfg=None):
        raise AssertionError("不应执行筛选")

    monkeypatch.setattr("app.application.fee_screen.service.run_screening", _boom)
    summary = fee_monitor.run_fee_screen_monitor(notifier=_StubNotifier())
    assert summary["alerts_sent"] == 0
    assert "Webhook 未配置" in summary["reason"]

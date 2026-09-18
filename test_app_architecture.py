from dataclasses import dataclass

import pytest


def test_modular_packages_import_without_runtime_side_effects():
    import app
    import app.application.monitoring
    import app.core
    import app.domain.monitoring
    import app.infrastructure.notifications

    assert app is not None


def test_monitor_registry_rejects_duplicates_and_preserves_order():
    from app.core.contracts import MonitorSpec
    from app.domain.monitoring.registry import MonitorRegistry

    registry = MonitorRegistry()
    first = MonitorSpec(name="rsi", interval_key="rsi", runner=lambda: None)
    second = MonitorSpec(name="price", interval_key="price", runner=lambda: None)
    registry.register(first)
    registry.register(second)

    assert registry.names() == ("rsi", "price")
    assert registry.get("rsi") is first
    with pytest.raises(ValueError, match="duplicate"):
        registry.register(first)


def test_scheduler_sleep_matches_enabled_due_tasks_and_poll_cap():
    from app.application.monitoring.scheduler import compute_sleep_seconds

    intervals = {"rsi": 600, "price": 120}
    last_run = {"rsi": 1000.0, "price": 1100.0}

    assert compute_sleep_seconds(intervals, {"rsi": True, "price": True}, last_run, 1150.0) == 30.0
    assert compute_sleep_seconds(intervals, {"rsi": True, "price": False}, last_run, 1150.0) == 30.0
    assert compute_sleep_seconds(intervals, {"rsi": False, "price": False}, last_run, 1150.0) == 30.0


def test_notifier_uses_injected_sender():
    from app.infrastructure.notifications.feishu import FeishuNotifier

    sent = []
    notifier = FeishuNotifier(sender=lambda webhook, message: sent.append((webhook, message)))
    notifier.send("hook", "hello")

    assert sent == [("hook", "hello")]

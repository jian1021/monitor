"""Architecture guard tests for the phase-1 `app` package boundaries."""

import ast
import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DOMAIN_DIR = REPO_ROOT / "app" / "domain"
FORBIDDEN_IN_DOMAIN = {"streamlit", "requests", "libsql_client", "pandas", "db", "send_feishu_msg"}

APP_MODULES = [
    "app",
    "app.core",
    "app.core.contracts",
    "app.core.settings",
    "app.domain.monitoring",
    "app.domain.monitoring.registry",
    "app.application.monitoring",
    "app.application.monitoring.scheduler",
    "app.infrastructure.notifications.feishu",
]


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_app_package_imports_without_side_effects():
    for name in APP_MODULES:
        assert importlib.import_module(name) is not None


def test_domain_layer_has_no_framework_or_io_imports():
    offenders = {}
    for path in DOMAIN_DIR.rglob("*.py"):
        bad = _top_level_imports(path) & FORBIDDEN_IN_DOMAIN
        if bad:
            offenders[str(path.relative_to(REPO_ROOT))] = sorted(bad)
    assert not offenders, f"domain 层不得依赖框架/IO：{offenders}"


def test_registry_rejects_duplicate_and_empty_names():
    from app.core.contracts import MonitorSpec
    from app.domain.monitoring.registry import MonitorRegistry

    reg = MonitorRegistry()
    spec = MonitorSpec(name="rsi", interval_key="rsi", runner=lambda: None)
    reg.register(spec)

    assert reg.get("rsi") is spec
    assert reg.names() == ("rsi",)

    with pytest.raises(ValueError):
        reg.register(MonitorSpec(name="rsi", interval_key="rsi", runner=lambda: None))
    with pytest.raises(ValueError):
        reg.register(MonitorSpec(name="   ", interval_key="x", runner=lambda: None))


def test_registry_preserves_registration_order():
    from app.core.contracts import MonitorSpec
    from app.domain.monitoring.registry import MonitorRegistry

    reg = MonitorRegistry()
    for name in ("b", "a", "c"):
        reg.register(MonitorSpec(name=name, interval_key=name, runner=lambda: None))

    assert reg.names() == ("b", "a", "c")


def test_compute_sleep_seconds_empty_and_disabled_fall_back_to_poll_interval():
    from app.application.monitoring.scheduler import POLL_INTERVAL, compute_sleep_seconds

    assert compute_sleep_seconds({}, {}, {}, now=1000.0) == POLL_INTERVAL
    assert compute_sleep_seconds({"a": 60}, {"a": False}, {"a": 0.0}, now=1000.0) == POLL_INTERVAL


def test_compute_sleep_seconds_due_task_and_poll_cap():
    from app.application.monitoring.scheduler import compute_sleep_seconds

    assert compute_sleep_seconds({"a": 1}, {"a": True}, {"a": 0.0}, now=1000.0) == 1.0
    assert compute_sleep_seconds({"a": 3600}, {"a": True}, {"a": 1000.0}, now=1000.0) == 30.0


def test_monitoring_scheduler_selects_due_specs():
    from app.application.monitoring.scheduler import MonitoringScheduler
    from app.core.contracts import MonitorSpec
    from app.domain.monitoring.registry import MonitorRegistry

    reg = MonitorRegistry()
    reg.register(MonitorSpec(name="rsi", interval_key="rsi", runner=lambda: None))
    reg.register(MonitorSpec(name="crypto", interval_key="crypto", runner=lambda: None))
    sched = MonitoringScheduler(reg)

    intervals = {"rsi": 3600, "crypto": 60}
    last_run = {"rsi": 1000.0, "crypto": 1000.0}

    due = sched.due(last_run, {"rsi": True, "crypto": True}, intervals, now=1100.0)
    assert [spec.name for spec in due] == ["crypto"]

    assert sched.due(last_run, {"rsi": False, "crypto": False}, intervals, now=9_999_999.0) == ()

    only_rsi = sched.due(last_run, {"rsi": True, "crypto": True}, {"rsi": 3600}, now=9_999_999.0)
    assert [spec.name for spec in only_rsi] == ["rsi"]


def test_notifier_reports_explicit_result_via_injected_sender():
    from app.infrastructure.notifications.feishu import FeishuNotifier

    calls = []

    def ok_sender(webhook, message):
        calls.append((webhook, message))
        return True

    result = FeishuNotifier(sender=ok_sender).send("https://hook", "hello")
    assert result.ok is True
    assert calls == [("https://hook", "hello")]

    assert FeishuNotifier(sender=lambda w, m: False).send("https://hook", "x").ok is False
    assert FeishuNotifier(sender=ok_sender).send("", "x").ok is False


def test_notifier_does_not_swallow_sender_exception():
    from app.infrastructure.notifications.feishu import FeishuNotifier

    def boom(webhook, message):
        raise RuntimeError("network down")

    with pytest.raises(RuntimeError):
        FeishuNotifier(sender=boom).send("https://hook", "x")

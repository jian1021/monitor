"""Pure scheduling logic extracted behind a testable boundary."""

from app.core.contracts import MonitorSpec
from app.domain.monitoring.registry import MonitorRegistry


POLL_INTERVAL = 30.0


def compute_sleep_seconds(
    intervals: dict[str, int],
    module_settings: dict[str, bool],
    last_run: dict[str, float],
    now: float,
    poll_interval: float = POLL_INTERVAL,
) -> float:
    enabled_due = [
        last_run[key] + interval
        for key, interval in intervals.items()
        if key in last_run and module_settings.get(key, True)
    ]
    if not enabled_due:
        return poll_interval
    next_due = min(enabled_due)
    return max(1.0, min(next_due - now, poll_interval))


class MonitoringScheduler:
    """Decides which registered monitors are due; it executes nothing itself."""

    def __init__(self, registry: MonitorRegistry, poll_interval: float = POLL_INTERVAL) -> None:
        self._registry = registry
        self._poll_interval = poll_interval

    def specs(self) -> tuple[MonitorSpec, ...]:
        return tuple(self._registry.get(name) for name in self._registry.names())

    def due(
        self,
        last_run: dict[str, float],
        module_settings: dict[str, bool],
        intervals: dict[str, int],
        now: float,
    ) -> tuple[MonitorSpec, ...]:
        due_specs = []
        for spec in self.specs():
            if not module_settings.get(spec.interval_key, True):
                continue
            interval = intervals.get(spec.interval_key)
            if interval is None:
                continue
            if last_run.get(spec.interval_key, 0.0) + interval <= now:
                due_specs.append(spec)
        return tuple(due_specs)

    def sleep_seconds(
        self,
        intervals: dict[str, int],
        module_settings: dict[str, bool],
        last_run: dict[str, float],
        now: float,
    ) -> float:
        return compute_sleep_seconds(intervals, module_settings, last_run, now, self._poll_interval)

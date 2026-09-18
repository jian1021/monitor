"""Pure scheduling logic extracted behind a testable boundary."""


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
        if module_settings.get(key, True)
    ]
    if not enabled_due:
        return poll_interval
    next_due = min(enabled_due)
    return max(1.0, min(next_due - now, poll_interval))

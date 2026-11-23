import threading
import time
from datetime import datetime, time as dtime

from app.application.monitoring.lanes import (
    TZ, CachedSource, DailyAtPolicy, IntervalPolicy, Job, Lane, MemoryStateStore,
    weekday_trading_day,
)


class FakeClock:
    def __init__(self, t): self.t = t
    def __call__(self): return self.t


def ts(y, m, d, H=0, M=0):
    return datetime(y, m, d, H, M, tzinfo=TZ).timestamp()


def test_interval_aligned_no_drift():
    p = IntervalPolicy(lambda: 60)
    assert p.next_after(0) == 60
    assert p.next_after(59.9) == 60
    assert p.next_after(60) == 120          # 严格大于


def test_slow_job_does_not_drift_or_backfill():
    clock = FakeClock(1000.0)
    calls = []

    def slow():
        calls.append(clock.t)
        clock.t += 250                       # 执行耗时跨越 4 个周期

    lane = Lane("t", [Job("j", slow, IntervalPolicy(lambda: 60), run_on_start=True)],
                MemoryStateStore(), lambda _: True, clock=clock)
    lane.run_due_once()
    assert lane.next_due["j"] == 1260        # 跳到网格上的下一个未来点，不堆积补跑
    assert lane.run_due_once() == []


def test_daily_at_after_close_skips_weekend():
    p = DailyAtPolicy(dtime(15, 5), is_trading_day=weekday_trading_day)
    # 2026-11-20 是周五，16:00 之后的下一次应是周一 11-23 15:05
    assert p.next_after(ts(2026, 11, 20, 16, 0)) == ts(2026, 11, 23, 15, 5)
    assert p.next_after(ts(2026, 11, 23, 10, 0)) == ts(2026, 11, 23, 15, 5)


def test_weekly_friday_only():
    p = DailyAtPolicy(dtime(15, 10), weekdays=frozenset({4}))
    assert p.next_after(ts(2026, 11, 22, 9, 0)) == ts(2026, 11, 27, 15, 10)


def test_restart_uses_persisted_state():
    store = MemoryStateStore()
    store.save("j", 5000.0)
    lane = Lane("t", [Job("j", lambda: None, IntervalPolicy(lambda: 60), run_on_start=True)],
                store, lambda _: True, clock=FakeClock(1000.0))
    assert lane.next_due["j"] == 5000.0      # 不因重启立即重跑


def test_disabled_job_skipped_but_schedule_advances():
    clock = FakeClock(100.0)
    ran = []
    lane = Lane("t", [Job("j", lambda: ran.append(1), IntervalPolicy(lambda: 60), run_on_start=True)],
                MemoryStateStore(), lambda _: False, clock=clock)
    lane.run_due_once()
    assert ran == [] and lane.next_due["j"] == 120


def test_unhealthy_callback_after_n_failures():
    alerts = []

    def boom(): raise RuntimeError("x")

    clock = FakeClock(0.0)
    lane = Lane("t", [Job("j", boom, IntervalPolicy(lambda: 1), run_on_start=True, max_failures_alert=3)],
                MemoryStateStore(), lambda _: True,
                on_unhealthy=lambda n, c, e: alerts.append((n, c)), clock=clock)
    for _ in range(5):
        lane.run_due_once()
        clock.t += 1
    assert alerts == [("j", 3)]              # 只在第 3 次告警一次


def test_cached_source_falls_back_on_error():
    clock = FakeClock(0.0)
    state = {"fail": False}

    def loader():
        if state["fail"]:
            raise ConnectionError("db down")
        return {"rsi": True}

    src = CachedSource(loader, default={}, ttl=10, clock=clock)
    assert src.get() == {"rsi": True}
    state["fail"], clock.t = True, 20
    assert src.get() == {"rsi": True}        # DB 故障不崩溃，沿用缓存


def test_lanes_isolated_slow_does_not_block_fast():
    fast_hits = []
    slow = Lane("slow", [Job("s", lambda: time.sleep(1.5), IntervalPolicy(lambda: 1), run_on_start=True)],
                MemoryStateStore(), lambda _: True, tick=0.05)
    fast = Lane("fast", [Job("f", lambda: fast_hits.append(time.time()), IntervalPolicy(lambda: 1), run_on_start=True)],
                MemoryStateStore(), lambda _: True, tick=0.05)
    slow.start(); fast.start()
    time.sleep(1.2)
    slow.stop(); fast.stop(); slow.join(3); fast.join(3)
    assert len(fast_hits) >= 1               # 慢通道 sleep 期间快通道照常执行

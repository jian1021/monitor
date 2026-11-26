"""lanes.py — 分通道调度器（重构方案）.

解决的问题：
  1. 单线程串行：慢扫描（A 股/链上 RSI）阻塞止损类任务（exit_strategy / lp_alert）。
  2. 时间漂移：last_run = 执行结束时间 → 实际周期 = 间隔 + 耗时。
  3. 不对齐收盘：日线/周线 RSI 在盘中计算，用到未收盘 K 线。
  4. 重启即全量重跑：last_run 只在内存。
  5. 主循环读 module_settings 无异常保护，DB 抖动即崩溃。

设计：
  - SchedulePolicy：决定「下一次计划时间」。IntervalPolicy（对齐到网格，防漂移）
    与 DailyAtPolicy（指定时刻 + 可选星期/交易日过滤，用于收盘后执行）。
  - Lane：一个独立线程，串行执行属于自己的任务；不同 Lane 互不阻塞。
  - StateStore：持久化 next_due，重启后不重复执行（默认内存实现，可换 DB）。
  - CachedSource：带缓存与失败兜底的配置读取（module_settings / intervals）。
"""

from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, time as dtime
from typing import Callable, Iterable, Protocol
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")


# ================= 调度策略 =================
class SchedulePolicy(Protocol):
    def next_after(self, ts: float) -> float:
        """返回严格大于 ts 的下一次计划执行时间（epoch 秒）。"""

    def signature(self) -> str:
        """策略配置指纹；写入持久化状态，配置变更时让旧 next_due 自动失效。"""


@dataclass
class IntervalPolicy:
    """固定间隔，对齐到 epoch 网格：每次都落在 k*interval(+offset) 上，不会漂移。

    interval_getter 支持热更新（UI 改间隔后下一轮即生效）。
    """
    interval_getter: Callable[[], int]   # 秒
    offset: int = 0                      # 错峰，避免多个模块同一秒打外部 API

    def next_after(self, ts: float) -> float:
        iv = max(1, int(self.interval_getter()))
        k = int((ts - self.offset) // iv) + 1
        return k * iv + self.offset

    def signature(self) -> str:
        # 间隔本身是热更新的（UI 可改），不写进签名；沿用旧 next_due 是安全的，
        # 下一轮 run_due_once 会用新间隔的 next_after 自然对齐。只有错峰 offset 变更才需重算。
        return f"interval:offset={self.offset}"


@dataclass
class DailyAtPolicy:
    """每天指定时刻执行（本地时区），可限制星期几 / 交易日。

    例：A 股日线 RSI 收盘后 18:00；ETF 周线 RSI 周五 18:10。
    """
    at: dtime
    weekdays: frozenset[int] = frozenset(range(7))      # 0=周一
    is_trading_day: Callable[[datetime], bool] | None = None

    def next_after(self, ts: float) -> float:
        cur = datetime.fromtimestamp(ts, TZ)
        day = cur.date()
        for _ in range(400):
            cand = datetime.combine(day, self.at, TZ)
            if (cand.timestamp() > ts and cand.weekday() in self.weekdays
                    and (self.is_trading_day is None or self.is_trading_day(cand))):
                return cand.timestamp()
            day += timedelta(days=1)
        raise RuntimeError("DailyAtPolicy: 400 天内找不到可执行日")

    def signature(self) -> str:
        # 时刻 / 星期 / 是否交易日过滤任一变更，都必须让持久化的旧 next_due 失效，
        # 否则重启后仍会按旧时刻跑一次（例如 15:05 改成 18:00 后仍取到未收盘 K 线）。
        days = ",".join(str(d) for d in sorted(self.weekdays))
        return f"daily:{self.at.strftime('%H:%M')}:wd={days}:td={self.is_trading_day is not None}"


def weekday_trading_day(dt: datetime) -> bool:
    """最简交易日判定（仅排除周末）；生产建议换成交易所日历（如 baostock query_trade_dates）。"""
    return dt.weekday() < 5


# ================= 状态持久化 =================
class StateStore(Protocol):
    def load(self, key: str) -> float | None: ...
    def load_state(self, key: str) -> tuple[float | None, str]:
        """返回 (next_due, signature)；老数据无签名时 signature 为空串。"""
    def save(self, key: str, next_due: float, signature: str = "") -> None: ...


class MemoryStateStore:
    def __init__(self) -> None:
        self._d: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def load(self, key):
        with self._lock:
            v = self._d.get(key)
            return v[0] if v else None

    def load_state(self, key):
        with self._lock:
            v = self._d.get(key)
            return (v[0], v[1]) if v else (None, "")

    def save(self, key, next_due, signature=""):
        with self._lock:
            self._d[key] = (next_due, signature)


class DbStateStore:
    """持久化到 libsql：表 module_schedule_state(module_name PK, next_due REAL, signature TEXT)。

    signature 记录算出 next_due 时的调度策略指纹；重启时若策略已变更则丢弃旧值重算，
    避免「改了收盘时刻但重启后仍按旧时刻执行」。写失败只打印，不影响调度。
    """

    def __init__(self, get_client: Callable[[], object]) -> None:
        self._get_client = get_client
        self._ensure()

    def _exec(self, sql, args=None):
        client = self._get_client()
        if not client:
            return None
        try:
            return client.execute(sql, args or [])
        finally:
            client.close()

    def _ensure(self):
        try:
            self._exec("CREATE TABLE IF NOT EXISTS module_schedule_state ("
                       "module_name TEXT PRIMARY KEY, next_due REAL NOT NULL, "
                       "signature TEXT NOT NULL DEFAULT '', "
                       "updated_at TEXT DEFAULT (datetime('now')))")
            # 老库补列：CREATE TABLE IF NOT EXISTS 不会改动已存在的表
            try:
                self._exec("ALTER TABLE module_schedule_state "
                           "ADD COLUMN signature TEXT NOT NULL DEFAULT ''")
            except Exception:
                pass  # 列已存在
        except Exception as e:  # noqa: BLE001
            print(f"⚠️ 创建 module_schedule_state 失败: {e}")

    def load(self, key):
        due, _ = self.load_state(key)
        return due

    def load_state(self, key):
        try:
            rs = self._exec("SELECT next_due, signature FROM module_schedule_state "
                            "WHERE module_name = ?", [key])
            if rs and rs.rows:
                row = rs.rows[0]
                return float(row[0]), str(row[1] or "")
        except Exception as e:  # noqa: BLE001
            print(f"⚠️ 读取调度状态失败({key}): {e}")
        return None, ""

    def save(self, key, next_due, signature=""):
        try:
            self._exec(
                "INSERT INTO module_schedule_state (module_name, next_due, signature, updated_at) "
                "VALUES (?, ?, ?, datetime('now')) ON CONFLICT(module_name) DO UPDATE SET "
                "next_due = excluded.next_due, signature = excluded.signature, "
                "updated_at = datetime('now')",
                [key, float(next_due), signature])
        except Exception as e:  # noqa: BLE001
            print(f"⚠️ 保存调度状态失败({key}): {e}")


# ================= 带兜底的配置源 =================
class CachedSource:
    """按 ttl 刷新；读取失败沿用上次成功的值（首次失败用 default）。线程安全。"""

    def __init__(self, loader: Callable[[], dict], default: dict, ttl: float = 60.0,
                 clock: Callable[[], float] = time.time) -> None:
        self._loader, self._value, self._ttl, self._clock = loader, dict(default), ttl, clock
        self._ts = float("-inf")
        self._lock = threading.Lock()

    def get(self) -> dict:
        with self._lock:
            now = self._clock()
            if now - self._ts >= self._ttl:
                try:
                    self._value = dict(self._loader())
                except Exception as e:  # noqa: BLE001
                    print(f"⚠️ 配置读取失败，沿用缓存: {e}")
                self._ts = now  # 失败也推进，避免 DB 故障时每秒重试
            return self._value


# ================= 任务与通道 =================
@dataclass
class Job:
    name: str
    runner: Callable[[], None]
    policy: SchedulePolicy
    run_on_start: bool = False      # 无持久化状态时是否立即跑一次（止损类建议 True）
    max_failures_alert: int = 3     # 连续失败 N 次触发健康告警
    _failures: int = field(default=0, repr=False)


class Lane(threading.Thread):
    """独立线程：串行执行本通道内到期任务。慢任务只阻塞本通道。"""

    def __init__(self, name: str, jobs: Iterable[Job], store: StateStore,
                 is_enabled: Callable[[str], bool],
                 on_unhealthy: Callable[[str, int, BaseException], None] | None = None,
                 tick: float = 1.0, clock: Callable[[], float] = time.time) -> None:
        super().__init__(name=f"lane-{name}", daemon=True)
        self.jobs = list(jobs)
        self.store, self.is_enabled, self.on_unhealthy = store, is_enabled, on_unhealthy
        self.tick, self.clock = tick, clock
        self._stop_evt = threading.Event()
        self.next_due: dict[str, float] = {}
        now = clock()
        for job in self.jobs:
            saved, saved_sig = store.load_state(job.name)
            current_sig = job.policy.signature()
            if saved is not None and saved_sig == current_sig:
                # 策略未变：沿用持久化状态，停机期间错过的执行只补一次，不连补 N 次
                self.next_due[job.name] = saved
            else:
                # 策略变更 / 首次启动 / 老数据无签名：丢弃旧 next_due，按当前策略重算
                if saved is not None:
                    print(f"♻️ [{job.name}] 调度策略变更"
                          f"（{saved_sig or '无签名'} → {current_sig}），按新策略重排")
                self.next_due[job.name] = now if job.run_on_start else job.policy.next_after(now)
                store.save(job.name, self.next_due[job.name], current_sig)

    def stop(self) -> None:
        self._stop_evt.set()

    def run_due_once(self) -> list[str]:
        """执行一遍当前到期的任务（可在测试里直接调用）。返回执行了的任务名。"""
        ran = []
        for job in self.jobs:
            planned = self.next_due[job.name]
            if self.clock() < planned:
                continue
            if self.is_enabled(job.name):
                self._execute(job)
                ran.append(job.name)
            # 关键：基于「计划时间」推进而非执行结束时间 → 无漂移；
            # 若执行超时跨过多个周期，next_after(now) 直接跳到未来，不会堆积补跑。
            nxt = job.policy.next_after(max(planned, self.clock()))
            self.next_due[job.name] = nxt
            self.store.save(job.name, nxt, job.policy.signature())
        return ran

    def _execute(self, job: Job) -> None:
        start = time.time()
        print(f"[{time.strftime('%H:%M:%S')}][{self.name}] ▶ {job.name}")
        try:
            job.runner()
            job._failures = 0
        except Exception as exc:  # noqa: BLE001
            job._failures += 1
            print(f"❌ [{self.name}] {job.name} 异常（连续 {job._failures} 次）: {exc}")
            traceback.print_exc()
            if self.on_unhealthy and job._failures == job.max_failures_alert:
                try:
                    self.on_unhealthy(job.name, job._failures, exc)
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
        finally:
            print(f"[{self.name}] ✔ {job.name} 耗时 {time.time() - start:.1f}s")

    def seconds_until_next(self) -> float:
        nearest = min(self.next_due.values(), default=self.clock() + self.tick)
        return max(0.0, nearest - self.clock())

    def run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self.run_due_once()
            except Exception:  # noqa: BLE001  调度器自身永不退出
                traceback.print_exc()
            # 可被 stop() 立即唤醒；上限 tick 以便感知 UI 改间隔/开关
            self._stop_evt.wait(min(self.tick, self.seconds_until_next()) or 0.05)


class LaneScheduler:
    def __init__(self, lanes: Iterable[Lane]) -> None:
        self.lanes = list(lanes)

    def start(self) -> None:
        for lane in self.lanes:
            lane.start()

    def run_all_once(self) -> None:
        """--once 模式：所有启用模块各跑一次（各通道并行），等待全部完成。"""
        threads = []
        for lane in self.lanes:
            def _go(l=lane):
                for job in l.jobs:
                    if l.is_enabled(job.name):
                        l._execute(job)
            t = threading.Thread(target=_go, name=f"once-{lane.name}")
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

    def stop(self, timeout: float = 30.0) -> None:
        for lane in self.lanes:
            lane.stop()
        for lane in self.lanes:
            lane.join(timeout)

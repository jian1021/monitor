"""main.py — 监控主循环入口（支持单轮与本地常驻）.

职责边界：本文件只做「装配 + 调度」——读资产配置、把各模块注册进
MonitorRegistry、按 module_settings / module_intervals 判定到期并执行。
各类监控的扫描与告警规则在 app/application/monitors/（runners / rsi_alerts）。
"""

import argparse
import threading
import time
import traceback
import signal
from datetime import time as dtime
from typing import Callable

from app.application.monitors import runners
from app.application.monitors import price as price_monitor
from app.application.monitors.meme_rsi_batch import run_batch_meme_rsi_monitor
from app.application.monitors.meme_underval import run_meme_underval_monitor
from app.application.lp_alert.service import run_once as run_lp_alert_once
from app.application.lp_tools.monitor import run_lp_apr_monitor
from app.application.exit_strategy.monitor import run_monitor as run_exit_strategy_monitor
from app.application.meteora_scanner import config as meteora_config
from app.application.meteora_scanner.alerts import evaluate_saved_scan
from app.application.meteora_scanner.db import Database as MeteoraDatabase
from app.application.meteora_scanner.feishu import FeishuAlerter as MeteoraFeishuAlerter
from app.application.meteora_scanner.scanner import Scanner as MeteoraScanner
from app.application.monitoring.scheduler import MonitoringScheduler
from app.application.monitoring.lanes import (
    CachedSource, DailyAtPolicy, DbStateStore, IntervalPolicy, Job, Lane,
    LaneScheduler, MemoryStateStore,
)
from app.infrastructure.market_data.trade_calendar import a_share_trading_day
from app.infrastructure.db.client import get_db_client
from app.infrastructure.notifications.sender import send_feishu_msg
from app.infrastructure.db.module_webhooks import resolve_webhook
from app.core.contracts import MonitorSpec
from app.core.settings import FEISHU_WEBHOOK, LIBSQL_TOKEN, LIBSQL_URL
from app.domain.monitoring.registry import MonitorRegistry
from app.infrastructure.db.assets import load_instruments
from app.infrastructure.db.intervals import DEFAULT_INTERVALS, get_module_intervals
from app.infrastructure.db.lp_alert import ensure_table as ensure_lp_alert_table
from app.infrastructure.db.module_settings import get_module_settings


# 主循环轮询间隔（秒）——决定对「到期」的感知精度
POLL_INTERVAL = 30

# 间隔缓存（已换算为秒）：读取失败时沿用，避免每次轮询都打一次 DB
_intervals_cache: dict[str, int] = {k: v * 60 for k, v in DEFAULT_INTERVALS.items()}
_intervals_cache_ts: float = 0.0

_meteora_scan_lock = threading.Lock()
_meteora_scanner: MeteoraScanner | None = None
_meteora_scan_thread: threading.Thread | None = None


def _run_meteora_scan():
    """在后台线程运行一次可断点续扫的 Meteora DLMM 全池扫描。"""
    global _meteora_scanner, _meteora_scan_thread
    if not _meteora_scan_lock.acquire(blocking=False):
        print("⏭️ Meteora 全池扫描仍在运行，跳过本轮调度")
        return

    try:
        if _meteora_scanner is None:
            _meteora_scanner = MeteoraScanner(MeteoraDatabase(meteora_config.DB_PATH))
        scanner = _meteora_scanner
        if scanner.is_running():
            print("⏭️ Meteora 扫描器报告仍在运行，跳过本轮调度")
            _meteora_scan_lock.release()
            return
        scanner.reset_stop()
    except Exception:
        _meteora_scan_lock.release()
        raise

    def _scan_worker():
        try:
            print(f"\n[{time.strftime('%H:%M:%S')}] ▶ 开始执行 Meteora DLMM 全池扫描")
            result = scanner.scan(
                list(meteora_config.SUPPORTED_POOL_TYPES), resume=True
            )
            print(f"✅ Meteora 全池扫描完成：{result}")
            if result and all(item.get("state") == "done" for item in result.values()):
                alerter = MeteoraFeishuAlerter(
                    scanner.db,
                    resolve_webhook("meteora_scan"),
                    meteora_config.FEISHU_WEBHOOK_SECRET,
                )
                alert_stats = evaluate_saved_scan(scanner.db, alerter)
                print(f"📨 Meteora 筛选结果飞书评估：{alert_stats}")
            else:
                print("⏭️ 扫描未完整完成，本轮不评估筛选结果或发送飞书报警")
        except Exception as exc:  # noqa: BLE001
            print(f"❌ Meteora 全池扫描异常：{exc}")
            traceback.print_exc()
        finally:
            _meteora_scan_lock.release()

    _meteora_scan_thread = threading.Thread(
        target=_scan_worker, name="meteora-full-pool-scan", daemon=True
    )
    try:
        _meteora_scan_thread.start()
    except Exception:
        _meteora_scan_lock.release()
        raise


def refresh_intervals(force: bool = False) -> dict[str, int]:
    """返回各子程序执行间隔（秒）：DB 优先，失败回退缓存/默认。"""
    global _intervals_cache, _intervals_cache_ts
    now = time.time()
    if not force and now - _intervals_cache_ts < 60:
        return _intervals_cache
    try:
        minutes = get_module_intervals(DEFAULT_INTERVALS)
        _intervals_cache = {k: max(1, int(v)) * 60 for k, v in minutes.items()}
    except Exception as e:
        print(f"⚠️ 读取模块执行间隔失败，沿用缓存: {e}")
    _intervals_cache_ts = now
    return _intervals_cache


def _lp_alert_job() -> None:
    ensure_lp_alert_table()
    run_lp_alert_once()


def _price_alert_job() -> None:
    """价格到达告警：链上代币走 Dexscreener，A股/ETF 走腾讯行情。"""
    price_monitor.ensure_table()
    price_monitor.run_once()


def _runner(label: str, action) -> Callable[[], None]:
    """统一的执行外壳：打印分隔线与标题，异常只记录不打断主循环。"""
    def wrapped():
        print(f"\n{'='*50}")
        print(f"[{time.strftime('%H:%M:%S')}] ▶ 开始执行 {label}")
        try:
            action()
        except Exception as e:  # noqa: BLE001
            print(f"❌ {label} 发生异常：{e}")
            traceback.print_exc()
    return wrapped


def build_scheduler(get_config: Callable[[], dict]) -> MonitoringScheduler:
    """注册所有监控模块；注册顺序即同一轮内的执行顺序。

    get_config 每轮返回最新资产配置——主循环会周期性重载配置，
    若在建表时把 config 固化进闭包，界面上的增删改就永远不生效。
    """
    registry = MonitorRegistry()
    registry.register(MonitorSpec(
        "rsi", "rsi", _runner("RSI 监控", lambda: runners.run_rsi_monitor(get_config()))))
    registry.register(MonitorSpec(
        "etf_rsi", "etf_rsi", _runner("ETF 周线 RSI 监控", lambda: runners.run_etf_monitor(get_config()))))
    registry.register(MonitorSpec(
        "crypto", "crypto", _runner("加密货币（OKX）RSI 监控", lambda: runners.run_crypto_monitor(get_config()))))
    registry.register(MonitorSpec(
        "onchain_token", "onchain_token",
        _runner("链上代币 RSI 监控", lambda: runners.run_onchain_token_monitor(get_config()))))
    registry.register(MonitorSpec(
        "meme_underval", "meme_underval",
        _runner("Meme 低估监控", run_meme_underval_monitor)))
    registry.register(MonitorSpec(
        "meme_rsi_batch", "meme_rsi_batch",
        _runner("批量 Meme 超卖超买监控", run_batch_meme_rsi_monitor)))
    registry.register(MonitorSpec(
        "lp_alert", "lp_alert",
        _runner("LP 仓位 / 池子价格告警", _lp_alert_job)))
    registry.register(MonitorSpec(
        "price", "price", _runner("价格到达告警", _price_alert_job)))
    registry.register(MonitorSpec(
        "lp_apr", "lp_apr", _runner("LP 区间 APR 监控", run_lp_apr_monitor)))
    registry.register(MonitorSpec(
        "meteora_scan", "meteora_scan", _runner("Meteora DLMM 全池扫描", _run_meteora_scan)))
    registry.register(MonitorSpec(
        "exit_strategy", "exit_strategy", _runner("退出策略监控", run_exit_strategy_monitor)))
    return MonitoringScheduler(registry, poll_interval=POLL_INTERVAL)



def build_lanes(once: bool = False) -> LaneScheduler:
    """分通道调度：fast（止损/仓位）/ crypto（7x24 固定间隔）/ close（A 股收盘后）。

    各通道独立线程互不阻塞；计划时间对齐网格不漂移；next_due 持久化，重启不全量重跑；
    module_settings / intervals / 资产配置读取失败时沿用缓存，不会让进程崩溃。
    """
    settings_src = CachedSource(get_module_settings, default={}, ttl=30)
    intervals_src = CachedSource(lambda: get_module_intervals(DEFAULT_INTERVALS),
                                 default=DEFAULT_INTERVALS, ttl=60)
    config_src = CachedSource(load_instruments, default=load_instruments(), ttl=60)
    cfg = config_src.get

    def enabled(name: str) -> bool:
        return bool(settings_src.get().get(name, True))

    def every(key: str, offset: int = 0) -> IntervalPolicy:
        return IntervalPolicy(
            lambda: max(1, int(intervals_src.get().get(key, DEFAULT_INTERVALS.get(key, 60)))) * 60,
            offset)

    def on_unhealthy(job: str, n: int, exc: BaseException) -> None:
        send_feishu_msg(resolve_webhook("health"), f"🩺【监控健康告警】{job} 连续失败 {n} 次：{exc}")

    fast = [
        Job("exit_strategy", _runner("退出策略监控", run_exit_strategy_monitor),
            every("exit_strategy"), run_on_start=True),
        Job("lp_alert", _runner("LP 仓位 / 池子价格告警", _lp_alert_job),
            every("lp_alert", offset=15), run_on_start=True),
    ]
    crypto = [
        Job("crypto", _runner("加密货币（OKX）RSI 监控", lambda: runners.run_crypto_monitor(cfg())),
            every("crypto", offset=5), run_on_start=True),
        Job("onchain_token", _runner("链上代币 RSI 监控", lambda: runners.run_onchain_token_monitor(cfg())),
            every("onchain_token", offset=10), run_on_start=True),
        Job("meme_underval", _runner("Meme 低估监控", run_meme_underval_monitor),
            every("meme_underval", offset=20)),
        Job("meme_rsi_batch", _runner("批量 Meme 超卖超买监控", run_batch_meme_rsi_monitor),
            every("meme_rsi_batch", offset=40)),
        Job("lp_apr", _runner("LP 区间 APR 监控", run_lp_apr_monitor), every("lp_apr", offset=50)),
        Job("meteora_scan", _runner("Meteora DLMM 全池扫描", _run_meteora_scan),
            every("meteora_scan", offset=90)),
        # 价格到达告警：链上代币 7x24、A股/ETF 盘中，一起按 5 分钟节奏检查
        Job("price", _runner("价格到达告警", _price_alert_job),
            every("price", offset=0), run_on_start=True),
    ]
    # 收盘后执行：A 股 RSI 优先取 baostock，其日/周 K 线在收盘后数小时才入库（15:00 刚收盘时
    # 取到的仍是上一根），所以放到 18:00 之后，确保用的是当天 / 当周已收盘的 K 线。
    close = [
        Job("rsi", _runner("RSI 监控", lambda: runners.run_rsi_monitor(cfg())),
            DailyAtPolicy(dtime(18, 0), is_trading_day=a_share_trading_day)),
        Job("etf_rsi", _runner("ETF 周线 RSI 监控", lambda: runners.run_etf_monitor(cfg())),
            DailyAtPolicy(dtime(18, 10), weekdays=frozenset({4}), is_trading_day=a_share_trading_day)),
    ]
    store = MemoryStateStore() if once else DbStateStore(get_db_client)
    return LaneScheduler([
        Lane("fast", fast, store, enabled, on_unhealthy, tick=1.0),
        Lane("crypto", crypto, store, enabled, on_unhealthy, tick=5.0),
        Lane("close", close, store, enabled, on_unhealthy, tick=10.0),
    ])


def run_legacy(once: bool = False) -> None:
    """旧版单线程调度（--legacy 回退用），逻辑与分通道改造前一致。

    直接复用本文件的 build_scheduler，不依赖 main.py.bak——.bak 已被 .gitignore 忽略，
    换台机器或重新 clone 后不存在，原来的写法会让 --legacy 直接崩溃。
    """
    state = {"config": load_instruments(), "loaded_at": time.time()}
    scheduler = build_scheduler(lambda: state["config"])
    last_run: dict[str, float] = {key: 0.0 for key in DEFAULT_INTERVALS}
    module_settings: dict[str, bool] = {}
    refresh_intervals(force=True)
    print("🚀 旧版单线程调度器已启动" + ("（单轮模式）" if once else ""))
    while True:
        now = time.time()
        if now - state["loaded_at"] >= 60:
            try:
                state["config"] = load_instruments()
            except Exception as e:  # noqa: BLE001
                print(f"⚠️ 刷新资产配置失败，沿用上一次配置: {e}")
            state["loaded_at"] = now
        try:
            module_settings = get_module_settings()
        except Exception as e:  # noqa: BLE001  读失败沿用上一轮的开关，避免把已停用的模块误开
            print(f"⚠️ 读取模块开关失败，沿用上一轮: {e}")
        intervals = refresh_intervals()
        for spec in scheduler.due(last_run, module_settings, intervals, now):
            spec.runner()
            last_run[spec.interval_key] = time.time()
        if once:
            if _meteora_scan_thread is not None:
                _meteora_scan_thread.join()
            print("✅ 单轮监控完成，退出。")
            return
        time.sleep(scheduler.sleep_seconds(intervals, module_settings, last_run, now))


# ================= 主程序逻辑 =================
if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="市场与链上资产监控调度器（分通道）")
    parser.add_argument("--once", action="store_true", help="每个已启用模块立即执行一轮后退出")
    parser.add_argument("--legacy", action="store_true", help="使用旧版单线程调度器（回退用）")
    args = parser.parse_args()

    if not LIBSQL_URL or not LIBSQL_TOKEN:
        print("停止运行：后台监控必须配置 LIBSQL_URL 和 LIBSQL_TOKEN。")
        raise SystemExit(2)
    if not FEISHU_WEBHOOK:
        print("⚠️ 未配置 FEISHU_WEBHOOK；需要飞书告警的模块不会发送通知。")
    try:
        load_instruments()
    except Exception as exc:
        print(f"停止运行：读取后台资产配置失败：{exc}")
        raise SystemExit(2) from exc

    if args.legacy:
        run_legacy(once=args.once)
        raise SystemExit(0)

    scheduler = build_lanes(once=args.once)

    if args.once:
        scheduler.run_all_once()
        if _meteora_scan_thread is not None:
            _meteora_scan_thread.join()
        print("✅ 单轮监控完成，退出。")
        raise SystemExit(0)

    stop_evt = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop_evt.set())
    signal.signal(signal.SIGINT, lambda *_: stop_evt.set())

    scheduler.start()
    print("🚀 分通道调度器已启动：fast / crypto / close")
    for lane in scheduler.lanes:
        for job in lane.jobs:
            due = time.strftime("%m-%d %H:%M:%S", time.localtime(lane.next_due[job.name]))
            print(f"   [{lane.name}] {job.name}: 下次执行 {due}")

    while not stop_evt.is_set():
        for lane in scheduler.lanes:
            if not lane.is_alive():
                send_feishu_msg(resolve_webhook("health"), f"🩺 调度通道 {lane.name} 已停止，进程退出待重启")
                stop_evt.set()
        stop_evt.wait(30)
    scheduler.stop()
    print("👋 调度器已退出")

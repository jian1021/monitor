"""main.py — 监控主循环入口（支持单轮与本地常驻）.

职责边界：本文件只做「装配 + 调度」——读资产配置、把各模块注册进
MonitorRegistry、按 module_settings / module_intervals 判定到期并执行。
各类监控的扫描与告警规则在 app/application/monitors/（runners / rsi_alerts）。
"""

import argparse
import threading
import time
import traceback
from typing import Callable

from app.application.monitors import runners
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
                    FEISHU_WEBHOOK,
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
        "lp_apr", "lp_apr", _runner("LP 区间 APR 监控", run_lp_apr_monitor)))
    registry.register(MonitorSpec(
        "meteora_scan", "meteora_scan", _runner("Meteora DLMM 全池扫描", _run_meteora_scan)))
    registry.register(MonitorSpec(
        "exit_strategy", "exit_strategy", _runner("退出策略监控", run_exit_strategy_monitor)))
    return MonitoringScheduler(registry, poll_interval=POLL_INTERVAL)


# ================= 主程序逻辑 =================
if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="市场与链上资产监控调度器")
    parser.add_argument(
        "--once", action="store_true",
        help="每个已启用模块立即执行一轮后退出",
    )
    args = parser.parse_args()

    if not LIBSQL_URL or not LIBSQL_TOKEN:
        print("停止运行：后台监控必须配置 LIBSQL_URL 和 LIBSQL_TOKEN。")
        raise SystemExit(2)
    if not FEISHU_WEBHOOK:
        print("⚠️ 未配置 FEISHU_WEBHOOK；需要飞书告警的模块不会发送通知。")
    try:
        config = load_instruments()
    except Exception as exc:
        print(f"停止运行：读取后台资产配置失败：{exc}")
        raise SystemExit(2) from exc

    # 界面增删改资产后，常驻进程需周期性重载配置才会生效
    CONFIG_REFRESH_SECONDS = 60
    last_config_refresh = time.time()

    # 各子任务上次执行时间（初始化为 0，让程序启动时立即执行一轮）
    last_run: dict[str, float] = {key: 0.0 for key in DEFAULT_INTERVALS}

    intervals = refresh_intervals(force=True)

    print("🚀 启动监控调度器" + ("（单轮模式）" if args.once else "（常驻模式）") + "，各子程序间隔：")
    for name in DEFAULT_INTERVALS:
        print(f"   {name}: 每 {intervals[name] // 60} 分钟执行一次")

    def _current_config() -> dict:
        """由主循环周期性刷新；闭包读全局，保证 runner 每轮拿到最新标的。"""
        return config

    scheduler = build_scheduler(_current_config)

    while True:
        now = time.time()
        # 定期重载资产配置：让界面上的删除/新增在 60 秒内生效
        if now - last_config_refresh >= CONFIG_REFRESH_SECONDS:
            try:
                config = load_instruments()
            except Exception as e:
                print(f"⚠️ 刷新资产配置失败，沿用上一次配置: {e}")
            last_config_refresh = now

        module_settings = get_module_settings()
        intervals = refresh_intervals()

        # 到期任务由 MonitoringScheduler 统一判定，注册顺序即执行顺序
        for spec in scheduler.due(last_run, module_settings, intervals, now):
            spec.runner()
            last_run[spec.interval_key] = time.time()

        if args.once:
            # 单轮模式也必须等全池扫描完成，避免 daemon 线程随进程退出被中断。
            if _meteora_scan_thread is not None:
                _meteora_scan_thread.join()
            print("✅ 单轮监控完成，退出。")
            break

        # 计算距离下一个最近到期任务还有多少秒，精准 sleep
        sleep_secs = scheduler.sleep_seconds(intervals, module_settings, last_run, now)
        print(f"\n💤 [{time.strftime('%H:%M:%S')}] 等待 {sleep_secs:.0f} 秒后检查下一轮...")
        time.sleep(sleep_secs)

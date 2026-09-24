"""main.py — 监控主循环入口（GitHub Actions / 本地常驻）.

各子程序注册进 MonitorRegistry，由 MonitoringScheduler 统一计算到期与睡眠；
执行间隔与启停由数据库 module_settings / module_intervals 控制（界面可改）。
"""

import random
import time
import traceback

import baostock as bs

import lp_position_alert
import monitor_rsi
from config import FEISHU_WEBHOOK
from db import DEFAULT_INTERVALS, get_module_intervals, get_module_settings
from db import load_instruments, update_asset_alert_state
from monitor_meteora_pump import run_pump_strategy_monitor
from monitor_robinhood_pump import run_monitor
from send_feishu_msg import send_feishu_msg

from app.application.monitoring.scheduler import MonitoringScheduler
from app.core.contracts import MonitorSpec
from app.domain.monitoring.registry import MonitorRegistry


# ================= 各子程序执行间隔 =================
# 默认值（分钟）定义在 db.DEFAULT_INTERVALS；启动时从 module_intervals 表读取，
# 界面（pages/dashboard.py）可修改，主循环每轮最多每 60 秒刷新一次。
# 主循环轮询间隔（秒）—— 决定对"到期"的感知精度，设短一点即可
POLL_INTERVAL = 30

# 间隔缓存（已换算为秒）：读取失败时沿用，避免每次轮询都打一次 DB
_intervals_cache: dict[str, int] = {k: v * 60 for k, v in DEFAULT_INTERVALS.items()}
_intervals_cache_ts: float = 0.0


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


def should_push_rsi_alert(item, rsi, price, settings) -> bool:
    """写入标的告警状态并返回本轮是否应推送；latch 未解除时抑制重复告警。"""
    low, high = settings["rsi_low"], settings["rsi_high"]
    if not (rsi < low or rsi > high):
        update_asset_alert_state(item.get("id"), active=False, latched=False,
                                 rsi=float(rsi), price=float(price), alert=False)
        return False
    already_latched = bool(item.get("alert_latched"))
    update_asset_alert_state(
        item.get("id"),
        active=bool(item.get("alarm_active")) or not already_latched,
        latched=True, rsi=float(rsi), price=float(price), alert=not already_latched)
    return not already_latched


# ================= RSI 监控（Meteora 池 / 可转债 / ETF） =================
def run_rsi_monitor(config: dict) -> None:
    messages = []
    bs_login_ok = False
    try:
        lg_ret = bs.login()
        if lg_ret.error_code == '0':
            bs_login_ok = True
            print("✅ baostock login success")
    except Exception as e:
        print(f"⚠️ baostock登录失败，A股源将全部降级到腾讯源 {e}")

    # 1. Meteora 流动池监控
    meteora_list = config.get("meteora", [])
    m_set = monitor_rsi.DEFAULT_SETTINGS["meteora"]
    for pool in meteora_list:
        if not pool.get("enabled", True):
            continue
        address = pool.get("code")
        pool_name = pool.get("name", address)
        if not address:
            continue
        rsi, price = monitor_rsi.get_meteora_rsi(address, m_set["timeframe"], m_set["aggregate"], m_set["period"])
        if rsi is not None and price is not None:
            print(f"✅ [Meteora] {pool_name} 现价: ${price:.6f}, RSI({m_set['period']}): {rsi:.2f}")
            if should_push_rsi_alert(pool, rsi, price, m_set):
                if rsi < m_set["rsi_low"]:
                    messages.append(f"🚨 【Meteora {pool_name} 超卖】现价 ${price:.6f}，1H RSI: {rsi:.2f} (低于 {m_set['rsi_low']})")
                else:
                    messages.append(f"⚠️ 【Meteora {pool_name} 超买】现价 ${price:.6f}，1H RSI: {rsi:.2f} (高于 {m_set['rsi_high']})")
        time.sleep(random.uniform(1.0, 2.0))

    # 2. 可转债监控
    cb_list = config.get("convertible_bonds", [])
    b_set = monitor_rsi.DEFAULT_SETTINGS["bond"]
    bond_tf = monitor_rsi.timeframe_label(b_set["frequency"])
    for item in cb_list:
        if not item.get("enabled", True):
            continue
        code = str(item.get("code"))
        cfg_name = item.get("name", code)
        rsi, price = monitor_rsi.get_a_share_rsi(code, bs_login_ok, length=b_set["period"], frequency=b_set["frequency"])
        if rsi is not None and price is not None:
            print(f"✅ [可转债] {cfg_name}({code}) 现价: {price:.2f}, {bond_tf} RSI({b_set['period']}): {rsi:.2f}")
            if should_push_rsi_alert(item, rsi, price, b_set):
                if rsi < b_set["rsi_low"]:
                    messages.append(f"🚨 【可转债 RSI 超卖】{cfg_name}({code}) 现价: {price:.2f} 元，{bond_tf} RSI: {rsi:.2f} (低于 {b_set['rsi_low']})")
                else:
                    messages.append(f"⚠️ 【可转债 RSI 超买】{cfg_name}({code}) 现价: {price:.2f} 元，{bond_tf} RSI: {rsi:.2f} (高于 {b_set['rsi_high']})")
        time.sleep(random.uniform(0.8, 1.5))

    # 3. ETF 监控
    etf_list = config.get("etfs", [])
    e_set = monitor_rsi.DEFAULT_SETTINGS["etf"]
    etf_tf = monitor_rsi.timeframe_label(e_set["frequency"])
    for item in etf_list:
        if not item.get("enabled", True):
            continue
        code = str(item.get("code"))
        cfg_name = item.get("name", code)
        rsi, price = monitor_rsi.get_a_share_rsi(code, bs_login_ok, length=e_set["period"], frequency=e_set["frequency"])
        if rsi is not None and price is not None:
            print(f"✅ [ETF] {cfg_name}({code}) 现价: {price:.3f}, {etf_tf} RSI({e_set['period']}): {rsi:.2f}")
            if should_push_rsi_alert(item, rsi, price, e_set):
                if rsi < e_set["rsi_low"]:
                    messages.append(f"🚨 【ETF RSI 超卖】{cfg_name}({code}) 现价: {price:.3f} 元，{etf_tf} RSI: {rsi:.2f} (低于 {e_set['rsi_low']})")
                else:
                    messages.append(f"⚠️ 【ETF RSI 超买】{cfg_name}({code}) 现价: {price:.3f} 元，{etf_tf} RSI: {rsi:.2f} (高于 {e_set['rsi_high']})")
        time.sleep(random.uniform(0.8, 1.5))

    if bs_login_ok:
        bs.logout()

    # 发送 RSI 告警
    if messages:
        full_msg = "\n\n".join(messages)
        send_feishu_msg(FEISHU_WEBHOOK, full_msg)
    else:
        print("所有标的 RSI 均处于正常区间，不触发推送。")


# ================= 加密货币（OKX）RSI 监控 =================
def run_crypto_monitor(config: dict) -> None:
    """独立子程序：加密货币（OKX）RSI 监控，独立间隔与告警。"""
    messages = []
    crypto_list = config.get("crypto_okx", [])
    c_set = monitor_rsi.DEFAULT_SETTINGS["crypto"]

    for coin in crypto_list:
        if not coin.get("enabled", True):
            continue
        symbol = coin.get("symbol")
        if not symbol:
            continue
        interval = coin.get("timeframe") or c_set["interval"]
        crypto_tf = monitor_rsi.timeframe_label(interval)
        rsi, price = monitor_rsi.get_okx_rsi(symbol, interval, c_set["period"])
        if rsi is not None and price is not None:
            print(f"✅ [OKX] {symbol} 现价: ${price:.4f}, {crypto_tf} RSI({c_set['period']}): {rsi:.2f}")
            if should_push_rsi_alert(coin, rsi, price, c_set):
                if rsi < c_set["rsi_low"]:
                    messages.append(f"🚨 【{symbol} 超卖】现价 ${price:.4f}，{crypto_tf} RSI: {rsi:.2f} (低于 {c_set['rsi_low']})")
                else:
                    messages.append(f"⚠️ 【{symbol} 超买】现价 ${price:.4f}，{crypto_tf} RSI: {rsi:.2f} (高于 {c_set['rsi_high']})")

    if messages:
        send_feishu_msg(FEISHU_WEBHOOK, "\n\n".join(messages))
    else:
        print("所有加密货币 RSI 均处于正常区间，不触发推送。")


# ================= 链上代币 RSI 监控 =================
def run_onchain_token_monitor(config: dict) -> None:
    """独立子程序：链上代币 RSI 监控，独立间隔与告警；时间级别取每个标的的 timeframe。"""
    messages = []
    token_list = config.get("tokens", [])
    t_set = monitor_rsi.DEFAULT_SETTINGS["token"]

    for item in token_list:
        if not item.get("enabled", True):
            continue
        code = str(item.get("code"))
        chain = item.get("chain") or "sol"
        cfg_name = item.get("name", code)
        resolution = item.get("timeframe") or t_set["resolution"]
        days = monitor_rsi.TOKEN_RESOLUTION_DAYS.get(resolution, t_set.get("days"))
        token_tf = monitor_rsi.timeframe_label(resolution)
        rsi, price = monitor_rsi.get_token_rsi(
            chain, code, resolution, t_set["period"], days)
        if rsi is not None and price is not None:
            short = code[:10]
            print(f"✅ [链上代币] {cfg_name}({short}...) {chain} 现价: {price:.8g}, "
                  f"{token_tf} RSI({t_set['period']}): {rsi:.2f}")
            if should_push_rsi_alert(item, rsi, price, t_set):
                if rsi < t_set["rsi_low"]:
                    messages.append(f"🚨 【链上代币 RSI 超卖】{cfg_name}({short}...) 链 {chain} "
                                    f"现价: {price:.8g}，{token_tf} RSI: {rsi:.2f} (低于 {t_set['rsi_low']})")
                else:
                    messages.append(f"⚠️ 【链上代币 RSI 超买】{cfg_name}({short}...) 链 {chain} "
                                    f"现价: {price:.8g}，{token_tf} RSI: {rsi:.2f} (高于 {t_set['rsi_high']})")
        time.sleep(random.uniform(0.8, 1.5))

    if messages:
        send_feishu_msg(FEISHU_WEBHOOK, "\n\n".join(messages))
    else:
        print("所有链上代币 RSI 均处于正常区间，不触发推送。")


def _run_rsi():
    print(f"\n{'='*50}")
    print(f"[{time.strftime('%H:%M:%S')}] ▶ 开始执行 RSI 监控")
    try:
        run_rsi_monitor(config)
    except Exception as e:
        print(f"❌ run_rsi_monitor() 发生异常：{e}")
        traceback.print_exc()


def _run_crypto():
    print(f"\n{'='*50}")
    print(f"[{time.strftime('%H:%M:%S')}] ▶ 开始执行加密货币（OKX）RSI 监控")
    try:
        run_crypto_monitor(config)
    except Exception as e:
        print(f"❌ run_crypto_monitor() 发生异常：{e}")
        traceback.print_exc()


def _run_onchain_token():
    print(f"\n{'='*50}")
    print(f"[{time.strftime('%H:%M:%S')}] ▶ 开始执行链上代币 RSI 监控")
    try:
        run_onchain_token_monitor(config)
    except Exception as e:
        print(f"❌ run_onchain_token_monitor() 发生异常：{e}")
        traceback.print_exc()


def _run_meteora_pump():
    print(f"\n{'='*50}")
    print(f"[{time.strftime('%H:%M:%S')}] ▶ 开始执行 Meteora pump 策略监控")
    try:
        run_pump_strategy_monitor()
    except Exception as e:
        print(f"❌ run_pump_strategy_monitor() 发生异常：{e}")
        traceback.print_exc()


def _run_robinhood_pump():
    print(f"\n{'='*50}")
    print(f"[{time.strftime('%H:%M:%S')}] ▶ 开始执行 RobinHood pump 策略监控")
    try:
        run_monitor()
    except Exception as e:
        print(f"❌ run_monitor() 发生异常：{e}")
        traceback.print_exc()


def _run_lp_alert():
    print(f"\n{'='*50}")
    print(f"[{time.strftime('%H:%M:%S')}] ▶ 开始执行 LP 仓位 / 池子价格告警")
    try:
        lp_position_alert.ensure_table()
        lp_position_alert.run_once()
    except Exception as e:
        print(f"❌ lp_position_alert 发生异常：{e}")
        traceback.print_exc()


def build_scheduler() -> MonitoringScheduler:
    registry = MonitorRegistry()
    registry.register(MonitorSpec("rsi", "rsi", _run_rsi))
    registry.register(MonitorSpec("crypto", "crypto", _run_crypto))
    registry.register(MonitorSpec("onchain_token", "onchain_token", _run_onchain_token))
    registry.register(MonitorSpec("meteora_pump", "meteora_pump", _run_meteora_pump))
    registry.register(MonitorSpec("robinhood_pump", "robinhood_pump", _run_robinhood_pump))
    registry.register(MonitorSpec("lp_alert", "lp_alert", _run_lp_alert))
    return MonitoringScheduler(registry, poll_interval=POLL_INTERVAL)


# ================= 主程序逻辑 =================
if __name__ == "__main__":

    config = load_instruments()
    if not config:
        print("停止运行：未能加载有效的配置文件。")
        exit(1)

    # 界面增删改资产后，常驻进程需周期性重载配置才会生效
    CONFIG_REFRESH_SECONDS = 60
    last_config_refresh = time.time()

    # 各子任务上次执行时间（初始化为 0，让程序启动时立即执行一轮）
    last_run: dict[str, float] = {key: 0.0 for key in DEFAULT_INTERVALS}

    intervals = refresh_intervals(force=True)

    print("🚀 主循环启动，各子程序独立间隔：")
    for name in DEFAULT_INTERVALS:
        print(f"   {name}: 每 {intervals[name] // 60} 分钟执行一次")

    scheduler = build_scheduler()

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

        # 计算距离下一个最近到期任务还有多少秒，精准 sleep
        sleep_secs = scheduler.sleep_seconds(intervals, module_settings, last_run, now)
        print(f"\n💤 [{time.strftime('%H:%M:%S')}] 等待 {sleep_secs:.0f} 秒后检查下一轮...")
        time.sleep(sleep_secs)

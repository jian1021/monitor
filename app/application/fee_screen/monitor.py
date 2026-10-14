"""fee-screen 监控主循环（主进程模块 fee_screen）。

每轮：
1. 链验证 + 数据源健康检查（异常/过期 → 告警，1 小时去重）
2. 筛选 + 排名
3. 对比上一轮状态：
   - 新池进入筛选名单 → 告警（24h 去重）
   - 成交量 / 手续费 / Fees-Active-TVL 大幅下降 → 告警（6h 去重）
   - 明显单边下跌风险 → 告警（6h 去重）
4. 保存状态，飞书推送（去重表复用 pump_alert_sent）

报警内容含池子地址、链、成交量、手续费率、Fees/Active TVL、价格变化、
风险原因和数据时间；同一风险 TTL 内不重复发送。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict

from app.core.settings import FEISHU_WEBHOOK
from app.domain.fee_screen.models import PoolEvaluation, ScreenConfig
from app.infrastructure.db.fee_screen_store import (
    get_setting,
    load_pool_state,
    log_alert,
    save_pool_state,
    set_setting,
)
from app.infrastructure.db.pump_alerts import filter_unpushed, mark_pushed
from app.infrastructure.market_data import rh_fee_sources as src
from app.infrastructure.notifications.feishu import FeishuNotifier

MODULE = "fee_screen"
NEW_TTL_H = 24
DROP_TTL_H = 6
TREND_TTL_H = 6
SOURCE_TTL_H = 1

DEFAULT_MONITOR = {
    "volume_drop_pct": 50.0,   # 成交量较上轮下降 ≥ 50% 告警
    "fees_drop_pct": 50.0,     # 手续费较上轮下降 ≥ 50% 告警
    "fat_drop_pct": 40.0,      # Fees/Active TVL 下降 ≥ 40% 告警
    "top_n": 20,               # 关注排名前 N
}


def load_monitor_config() -> dict:
    raw = get_setting("monitor_config")
    cfg = dict(DEFAULT_MONITOR)
    if raw:
        try:
            for k, v in json.loads(raw).items():
                if k in cfg:
                    cfg[k] = float(v)
        except (TypeError, ValueError):
            pass
    return cfg


def save_monitor_config(cfg: dict) -> bool:
    return set_setting(
        "monitor_config",
        json.dumps({k: cfg.get(k) for k in DEFAULT_MONITOR}, ensure_ascii=False))


def get_webhook() -> str:
    return get_setting("webhook_url", "") or FEISHU_WEBHOOK or ""


def _fmt_pct(value: float | None) -> str:
    return "数据不可用" if value is None else f"{value * 100:.2f}%"


def _pool_lines(ev: PoolEvaluation) -> str:
    pool = ev.pool
    from datetime import datetime
    data_time = datetime.fromtimestamp(pool.last_event_at or pool.fetched_at or time.time()) \
        .strftime("%m-%d %H:%M")
    return (
        f"池子: {pool.pair} ({pool.protocol.upper()})\n"
        f"地址: {pool.pool_address_display}\n"
        f"链: Robinhood Chain (4663)\n"
        f"24h 成交量: {_fmt_usd(pool.volume_usd)}\n"
        f"24h 手续费: {_fmt_usd(pool.fees_usd)}\n"
        f"实际手续费率: {_fmt_pct(ev.actual_fee_rate)}\n"
        f"Fees/Active TVL: {_fmt_pct(ev.fees_active_tvl)}\n"
        f"价格变化(24h): {_fmt_price_change(pool)}\n"
        f"数据时间: {data_time}"
    )


def _fmt_usd(value: float | None) -> str:
    return "数据不可用" if value is None else f"${value:,.0f}"


def _fmt_price_change(pool) -> str:
    ds = pool.ds_price_change or {}
    parts = []
    for key, label in (("h1", "1h"), ("h6", "6h"), ("h24", "24h")):
        v = ds.get(key)
        parts.append(f"{label} {v:+.2f}%" if v is not None else f"{label} 不可用")
    return " / ".join(parts)


def _dedupe_and_send(notifier: FeishuNotifier, webhook: str, module: str,
                     keys: list[str], messages: list[str],
                     ttl_hours: int) -> int:
    """TTL 内已推送的 key 不再发送，返回实际发送条数。"""
    unpushed = filter_unpushed(module, keys, ttl_hours=ttl_hours)
    sent = 0
    for key, message in zip(keys, messages):
        if key not in unpushed:
            continue
        result = notifier.send(webhook, message)
        if result.ok:
            mark_pushed(module, [key])
            sent += 1
    return sent


def run_fee_screen_monitor(cfg: ScreenConfig | None = None,
                           notifier: FeishuNotifier | None = None) -> dict:
    """执行一轮 fee-screen 监控，返回执行摘要（供主循环与测试断言）。"""
    notifier = notifier or FeishuNotifier()
    webhook = get_webhook()
    mon_cfg = load_monitor_config()
    summary: dict = {"alerts_sent": 0, "pools_in_list": 0, "reason": ""}

    if not webhook:
        summary["reason"] = "飞书 Webhook 未配置，跳过推送（页面四可配置）"
        return summary

    from app.application.fee_screen.service import run_screening
    result = run_screening(cfg)
    messages: list[str] = []

    # ---- 数据源异常 / 数据过期
    if not result.ok:
        # 禁用服务自动启动时，rhpools 不可用属于预期状态，静默跳过。
        if not src.rhpools_client.auto_start_enabled():
            summary["reason"] = "已禁用 rhpools 自动启动且服务不可用，本轮静默跳过"
            return summary
        messages.append(
            "⚠️【fee-screen 数据源异常】\n"
            f"原因: {result.reason}\n"
            f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        sent = _dedupe_and_send(
            notifier, webhook, f"{MODULE}_src", ["source_down"],
            messages, SOURCE_TTL_H)
        log_alert("source_error", result.reason)
        summary["alerts_sent"] += sent
        summary["reason"] = result.reason
        return summary

    lag = (result.index_status or {}).get("lag_s")
    if isinstance(lag, (int, float)) and lag > src.INDEX_BACKFILL_LAG_S:
        msg = (
            "⚠️【fee-screen 数据过期】\n"
            f"rhpools 索引落后 {lag / 3600:.1f} 小时，窗口数据为部分历史\n"
            f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        sent = _dedupe_and_send(
            notifier, webhook, f"{MODULE}_src", ["index_lag"], [msg], SOURCE_TTL_H)
        log_alert("source_stale", f"索引落后 {lag}s")
        summary["alerts_sent"] += sent

    # ---- 池子级对比
    current: dict[str, dict] = {}
    previous = load_pool_state()
    top = result.evaluations[: int(mon_cfg.get("top_n", 20))]
    summary["pools_in_list"] = len(top)

    new_keys, new_msgs = [], []
    drop_keys, drop_msgs = [], []
    trend_keys, trend_msgs = [], []

    for ev in top:
        pool = ev.pool
        current[pool.pool_id] = {
            "volume_usd": pool.volume_usd,
            "fees_usd": pool.fees_usd,
            "fees_active_tvl": ev.fees_active_tvl,
            "in_list": True,
        }
        header = f"🚨【fee-screen】\n{_pool_lines(ev)}\n"

        # 1) 新池进入筛选名单
        if pool.pool_id not in previous:
            new_keys.append(pool.pool_id)
            new_msgs.append(header + "事件: 新池进入筛选名单\n"
                                     f"风险原因: {ev.risk_level.value}"
                                     + (f"；{'；'.join(ev.trend.reasons)}" if ev.trend.reasons else ""))
        else:
            prev = previous[pool.pool_id]
            # 2) 成交量 / 手续费 / Fees-Active-TVL 大幅下降
            for field_name, prev_v, cur_v, threshold in (
                ("24h 成交量", prev.get("volume_usd"), pool.volume_usd,
                 mon_cfg.get("volume_drop_pct")),
                ("24h 手续费", prev.get("fees_usd"), pool.fees_usd,
                 mon_cfg.get("fees_drop_pct")),
                ("Fees/Active TVL", prev.get("fees_active_tvl"), ev.fees_active_tvl,
                 mon_cfg.get("fat_drop_pct")),
            ):
                if (prev_v is None or cur_v is None or prev_v <= 0
                        or threshold is None):
                    continue
                drop_pct = (prev_v - cur_v) / prev_v * 100
                if drop_pct >= threshold:
                    drop_keys.append(f"{pool.pool_id}:{field_name}")
                    drop_msgs.append(
                        header + f"事件: {field_name}较上轮下降 {drop_pct:.0f}%"
                                 f"（阈值 {threshold:g}%）")

        # 3) 明显单边下跌风险
        if ev.trend.one_sided_downtrend:
            trend_keys.append(pool.pool_id)
            trend_msgs.append(header + "事件: 明显单边下跌风险\n"
                                       f"风险原因: {'；'.join(ev.trend.reasons) or '价格持续走低'}")

    summary["alerts_sent"] += _dedupe_and_send(
        notifier, webhook, f"{MODULE}_new", new_keys, new_msgs, NEW_TTL_H)
    summary["alerts_sent"] += _dedupe_and_send(
        notifier, webhook, f"{MODULE}_drop", drop_keys, drop_msgs, DROP_TTL_H)
    summary["alerts_sent"] += _dedupe_and_send(
        notifier, webhook, f"{MODULE}_trend", trend_keys, trend_msgs, TREND_TTL_H)
    for kind, keys, msgs in (("new_pool", new_keys, new_msgs),
                             ("metric_drop", drop_keys, drop_msgs),
                             ("trend_risk", trend_keys, trend_msgs)):
        for key, msg in zip(keys, msgs):
            log_alert(kind, msg, pool_id=key.split(":")[0])

    save_pool_state(current)
    summary["reason"] = (
        f"本轮 {len(top)} 个池子在名单，发送 {summary['alerts_sent']} 条告警")
    return summary


def monitoring_config_snapshot() -> dict:
    """页面四展示用：当前监控配置。"""
    return {
        **load_monitor_config(),
        "webhook": get_webhook(),
        "screening": asdict(load_screening_config_cached()),
    }


def load_screening_config_cached():
    from app.application.fee_screen.service import load_screening_config
    return load_screening_config()

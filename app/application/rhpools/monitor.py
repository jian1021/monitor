"""Robinhood Pools（rhpools）观测站监控：服务健康 + 池间价差告警.

数据全部来自本地 rhpools 服务的索引证据（/api/lp/*），缺失字段不补零。
推送去重复用 pump_alert_sent（module + key + TTL），失败时宁可重复也不漏报。
"""

from app.core.settings import FEISHU_WEBHOOK
from app.infrastructure.db.pump_alerts import filter_unpushed, mark_pushed
from app.infrastructure.notifications.feishu import FeishuNotifier
from app.infrastructure.rhpools import client

DISLOCATION_MODULE = "rhpools_dislocation"
STATUS_MODULE = "rhpools_status"

DISLOCATION_TTL_HOURS = 2     # 同一价差对 2 小时内不重复推
STATUS_TTL_HOURS = 1          # 服务/索引异常 1 小时内不重复推

MIN_BPS = 50.0                # 价差告警阈值（基点）
MIN_DEPTH_USD = 300.0         # 浅边深度下限（美元）
MAX_AGE_S = 3600              # 只看最近 1 小时有状态更新的池
LAG_ALERT_S = 600             # 索引落后超过 10 分钟告警


def _usd(value) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if abs(v) >= 1e6:
        return f"${v / 1e6:.2f}M"
    if abs(v) >= 1e3:
        return f"${v / 1e3:.1f}k"
    return f"${v:.2f}"


def _fee_bps(fee_ppm) -> str:
    try:
        return f"{int(fee_ppm) / 10_000:.3g}%"
    except (TypeError, ValueError):
        return "?"


def build_status_alerts(status: dict) -> list[tuple[str, str]]:
    """从 /api/lp/status 生成 (去重键, 消息) 列表；健康时为空.

    warming / catching_up 是启动与追块的正常状态，不告警——
    真正的落后由 lag_s 阈值兜底；degraded/stopped 才是故障。
    """
    alerts: list[tuple[str, str]] = []
    state = str(status.get("state") or "unknown")
    if state not in {"live", "warming", "catching_up"}:
        alerts.append((
            f"state:{state}",
            f"⚠️ 【rhpools 索引异常】state={state}（服务在跑但数据源持续报错）",
        ))
    lag_s = status.get("lag_s")
    if isinstance(lag_s, (int, float)) and lag_s > LAG_ALERT_S:
        lag_blocks = status.get("lag_blocks")
        blocks = f" / {lag_blocks} 块" if isinstance(lag_blocks, int) else ""
        alerts.append((
            "lag",
            f"⚠️ 【rhpools 索引落后】head 滞后 {lag_s:.0f} 秒{blocks}",
        ))
    warm_error = status.get("warm_error")
    if warm_error:
        alerts.append(("warm_error", f"⚠️ 【rhpools 预热失败】{warm_error}"))
    return alerts


def build_dislocation_messages(rows: list[dict]) -> list[tuple[str, str]]:
    """把价差行转成 (去重键, 消息)；键为 token0:token1。"""
    out: list[tuple[str, str]] = []
    for row in rows:
        key = f"{row.get('token0')}:{row.get('token1')}"
        buy, sell = row.get("buy") or {}, row.get("sell") or {}
        spread = row.get("spread_bps")
        net = row.get("net_bps")
        net_txt = f"（净 {net:.1f} bps）" if isinstance(net, (int, float)) else ""
        lines = [
            f"🚨 【rhpools 池间价差】{row.get('pair') or key}",
            f"价差 {float(spread):.1f} bps{net_txt}，浅边深度 {_usd(row.get('depth_usd'))}",
            f"低 {buy.get('protocol', '?')}({_fee_bps(buy.get('fee_ppm'))}) @ {buy.get('price')}"
            f" | 高 {sell.get('protocol', '?')}({_fee_bps(sell.get('fee_ppm'))}) @ {sell.get('price')}",
            "（价格为已索引证据的 mid price，非可执行报价）",
        ]
        out.append((key, "\n".join(lines)))
    return out


def _push(messages: list[str]) -> None:
    if not messages:
        return
    FeishuNotifier().send(FEISHU_WEBHOOK, "\n\n".join(messages))


def run_rhpools_monitor() -> None:
    status = client.ensure_running()

    if status is None:
        if client.available() and client.auto_start_enabled():
            key = "service_down"
            if filter_unpushed(STATUS_MODULE, [key], ttl_hours=STATUS_TTL_HOURS):
                _push([f"🚨 【rhpools 服务不可用】{client.base_url()} 无法访问，自动启动后仍未就绪，"
                       f"请查看 logs/rhpools.log"])
                mark_pushed(STATUS_MODULE, [key])
        else:
            print("⏭️ rhpools 服务不可用且未启用自动启动，本轮跳过")
        return

    # 1) 索引健康告警
    alerts = build_status_alerts(status)
    if alerts:
        fresh = set(filter_unpushed(STATUS_MODULE, [k for k, _ in alerts],
                                   ttl_hours=STATUS_TTL_HOURS))
        msgs = [msg for key, msg in alerts if key in fresh]
        if msgs:
            _push(msgs)
            mark_pushed(STATUS_MODULE, [k for k, _ in alerts if k in fresh])

    # 2) 池间价差（同一交易对在多个池的定价偏离，去掉手续费后的净价差可套利）
    data = client.get("/api/lp/dislocations", {
        "min_bps": MIN_BPS,
        "min_depth_usd": MIN_DEPTH_USD,
        "max_age_s": MAX_AGE_S,
        "limit": 20,
        "sort": "net",
    })
    rows = (data or {}).get("rows") or []
    if not rows:
        return
    entries = build_dislocation_messages(rows)
    fresh = set(filter_unpushed(DISLOCATION_MODULE, [k for k, _ in entries],
                                ttl_hours=DISLOCATION_TTL_HOURS))
    msgs = [msg for key, msg in entries if key in fresh]
    if msgs:
        _push(msgs)
        mark_pushed(DISLOCATION_MODULE, [k for k, _ in entries if k in fresh])

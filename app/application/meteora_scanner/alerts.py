"""
报警评估编排（文档 10.）。

触发场景：
  - 新发现符合筛选条件的池（汇总为一条消息，避免刷屏）。
  - 市值首次达到筛选条件（按池+事件去重）。
  - 成交量 / Fees/TVL 达到指定阈值（按池+事件去重）。
  - 重点池指标变化（见 monitor.py）。

单次运行对个体报警数量设上限，避免误配置导致海量消息。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from .db import Database
from .feishu import FeishuAlerter


def _recent(iso: Optional[str], hours: float) -> bool:
    if not iso:
        return False
    try:
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= datetime.now(timezone.utc).astimezone() - timedelta(hours=hours)


def evaluate_after_scan(
    db: Database,
    alerter: FeishuAlerter,
    rows: list[dict],
    *,
    volume_threshold: Optional[float] = None,
    fee_tvl_threshold: Optional[float] = None,
    new_window_hours: float = 24.0,
    max_individual: int = 30,
) -> dict:
    """对扫描结果执行报警评估。rows 需含 evaluate_pool 的附加字段。"""
    stats = {"new_matching": 0, "mcap_reached": 0, "volume_threshold": 0,
             "feetvl_threshold": 0, "sent": 0, "skipped_duplicate": 0, "capped": 0,
             "configured": alerter.configured}

    if not alerter.configured:
        return stats

    passed = [r for r in rows if r.get("passed")]

    # 1) 新发现且符合筛选条件 → 汇总
    new_matching = [r for r in passed if _recent(r.get("first_seen"), new_window_hours)]
    stats["new_matching"] = len(new_matching)
    if new_matching:
        items = [
            f"{r.get('pool_type')} {r.get('token_x_symbol') or '?'}/"
            f"{r.get('token_y_symbol') or '?'} TVL {r.get('tvl')} "
            f"Fe/TVL {r.get('fee_tvl_ratio_24h')}% {r.get('pool_address')}"
            for r in new_matching[:20]
        ]
        ok, msg = alerter.send_digest("new_pool", "新发现符合筛选条件的池",
                                      items, dedup_key="new_pool:digest")
        if ok:
            stats["sent"] += 1

    individual_sent = 0

    # 2) 市值首次达标
    for r in passed:
        if individual_sent >= max_individual:
            stats["capped"] += 1
            break
        ok, msg = alerter.send_pool_alert("mcap_reached", r,
                                          reason="代币市值达到筛选条件（>阈值）")
        if ok:
            stats["mcap_reached"] += 1
            stats["sent"] += 1
            individual_sent += 1
        elif msg == "duplicate":
            stats["skipped_duplicate"] += 1

    # 3) 成交量阈值
    if volume_threshold is not None:
        for r in rows:
            if individual_sent >= max_individual:
                stats["capped"] += 1
                break
            vol = r.get("volume_24h")
            if vol is not None and vol >= volume_threshold:
                ok, msg = alerter.send_pool_alert(
                    "volume_threshold", r,
                    reason=f"24h 成交量 {vol:,.0f} >= 阈值 {volume_threshold:,.0f}")
                if ok:
                    stats["volume_threshold"] += 1
                    stats["sent"] += 1
                    individual_sent += 1
                elif msg == "duplicate":
                    stats["skipped_duplicate"] += 1

    # 4) Fees/TVL 阈值
    if fee_tvl_threshold is not None:
        for r in rows:
            if individual_sent >= max_individual:
                stats["capped"] += 1
                break
            ftr = r.get("fee_tvl_ratio_24h")
            if ftr is not None and ftr >= fee_tvl_threshold:
                ok, msg = alerter.send_pool_alert(
                    "feetvl_threshold", r,
                    reason=f"24h Fees/TVL {ftr:.3f}% >= 阈值 {fee_tvl_threshold:.3f}%")
                if ok:
                    stats["feetvl_threshold"] += 1
                    stats["sent"] += 1
                    individual_sent += 1
                elif msg == "duplicate":
                    stats["skipped_duplicate"] += 1

    return stats

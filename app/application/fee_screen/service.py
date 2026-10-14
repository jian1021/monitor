"""fee-screen 应用层：编排「链验证 → 数据拉取 → 归一化 → 筛选 → 排名」。

所有失败都降级为"数据不可用"并记录原因，不编造数据。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.domain.fee_screen.models import (
    PoolSnapshot,
    ScreenConfig,
    to_float,
)
from app.domain.fee_screen.screening import (
    evaluate_pool,
    normalize_pool,
    rank_pools,
)
from app.infrastructure.market_data import rh_fee_sources as src


@dataclass
class ScreenResult:
    ok: bool = False
    reason: str = ""
    chain: dict = field(default_factory=dict)
    meteora: dict = field(default_factory=dict)
    index_status: dict | None = None
    evaluations: list = field(default_factory=list)   # 已排名（通过的）
    all_evaluations: list = field(default_factory=list)  # 全部（含未通过）
    fetched_at: float = 0.0
    data_warnings: list[str] = field(default_factory=list)


def _decode_screening_config(payload: dict[str, Any] | None) -> ScreenConfig:
    cfg = ScreenConfig()
    if not payload:
        return cfg
    for key in (
        "min_volume_24h", "min_fees_24h", "min_fee_rate", "min_fees_active_tvl",
        "min_history_days", "min_persistence_ratio", "max_drop_1h", "max_drop_4h",
        "big_candle_drop_pct", "big_candle_count", "min_candles_for_trend",
        "w_fees", "w_fees_active_tvl", "w_volume", "w_persistence", "w_trend",
    ):
        value = to_float(payload.get(key))
        if value is not None:
            setattr(cfg, key, value)
    return cfg


def load_screening_config() -> ScreenConfig:
    from app.infrastructure.db.fee_screen_store import get_screening_config_json
    import json
    payload = get_screening_config_json()
    if not payload:
        return ScreenConfig()
    try:
        return _decode_screening_config(json.loads(payload))
    except (TypeError, ValueError):
        return ScreenConfig()


def collect_snapshots(cfg: ScreenConfig,
                      enrich_top: int = 50) -> tuple[list[PoolSnapshot], list[str], dict | None]:
    """拉取 24h + 7d 池子并合并，附带 DexScreener 市场数据。

    enrich_top：仅对前 N 个池子逐个调用 DexScreener 补充涨跌幅/创建时间
    （列表页已按成交量降序），避免全量请求拖慢与限流；其余池子明确标注
    "市场数据未补充"，不用假数据填充。5 分钟 K 线仅在单池详情页按需重建。
    """
    warnings: list[str] = []
    index_status: dict | None = None

    def fetch_all(window: str) -> dict:
        rows = []
        offset = 0
        total = None
        status = None
        while total is None or offset < total:
            page = src.fetch_pools(window=window, sort="volume", limit=150, offset=offset)
            if not page.get("ok"):
                return page
            page_rows = page.get("rows") or []
            status = page.get("status") or status
            rows.extend(page_rows)
            try:
                total = int(page.get("total"))
            except (TypeError, ValueError):
                total = offset + len(page_rows)
            if not page_rows or len(page_rows) < 150:
                break
            offset += len(page_rows)
        return {"ok": True, "rows": rows, "total": total, "status": status}

    day = fetch_all(window="24h")
    if not day.get("ok"):
        return [], [day.get("reason") or "24h 数据拉取失败"], None
    index_status = day.get("status")

    # 7d 列表按 7d 成交量排序，与 24h 排序不同：取更大的 limit 减少 join 漏配
    # （漏配会把有 7d 数据的池子误标"持续性数据不可用"而错误落选）
    week = fetch_all(window="7d")
    week_rows = {str(r.get("id")): r for r in (week.get("rows") or [])} if week.get("ok") else {}
    if not week.get("ok"):
        warnings.append("7 天成交量拉取失败：成交量持续性指标将标记为数据不可用")

    # 索引追赶期提示
    if index_status:
        lag = to_float(index_status.get("lag_s"))
        if lag is not None and lag > src.INDEX_BACKFILL_LAG_S:
            warnings.append(
                f"索引仍在追赶（落后 {lag/3600:.1f} 小时），窗口数据为部分历史，"
                "筛选结果仅供参考")

    snapshots: list[PoolSnapshot] = []
    enriched = 0
    skipped_enrich = 0
    for row in day.get("rows") or []:
        pool = normalize_pool(row)
        if pool is None:
            continue
        week_row = week_rows.get(pool.pool_id)
        pool.volume_7d = to_float(week_row.get("volume_usd")) if week_row else None
        pool.fees_7d = to_float(week_row.get("fees_usd")) if week_row else None
        pool.window_7d_complete = bool(
            (week_row or {}).get("coverage", {}).get("complete") is True)

        # DexScreener 补充（独立数据源；失败不阻塞，仅前 N 个）
        if enriched < enrich_top:
            enriched += 1
            pair_addr = pool.pool_address or pool.pool_id
            market = src.fetch_pair_market(pair_addr)
            if market:
                change = market.get("priceChange") or {}
                pool.ds_price_change = {
                    k: to_float(change.get(k), 0.0) or 0.0
                    for k in ("m5", "h1", "h6", "h24") if change.get(k) is not None
                }
                pool.ds_pair_created_at = to_float(market.get("pairCreatedAt"))
                pool.ds_liquidity_usd = to_float(
                    (market.get("liquidity") or {}).get("usd"))
                pool.ds_url = str(market.get("url") or "")
                if pool.created_at is None and pool.ds_pair_created_at is not None:
                    pool.created_at = pool.ds_pair_created_at
            else:
                warnings.append(
                    f"{pool.pair}：DexScreener 无该池市场数据，涨跌幅/创建时间标记不可用")
        else:
            skipped_enrich += 1
        snapshots.append(pool)
    if skipped_enrich:
        warnings.append(
            f"另有 {skipped_enrich} 个低成交量池子未做 DexScreener 补充"
            "（涨跌幅等显示为数据不可用，可调大 enrich_top）")
    return snapshots, warnings, index_status


def run_screening(cfg: ScreenConfig | None = None) -> ScreenResult:
    """完整筛选流程：链验证 → Meteora 部署拦截 → 拉取 → 筛选 → 排名。"""
    result = ScreenResult(fetched_at=time.time())

    # 1. 链验证（失败则停止筛选，不得混入其他链数据）
    chain = src.verify_chain()
    result.chain = chain
    if not chain.get("ok"):
        result.reason = f"链验证失败，停止筛选：{chain.get('reason')}"
        result.data_warnings.append(result.reason)
        return result

    # 2. Meteora 部署检查（未部署则明确提示，筛选宇宙为真实存在的池）
    result.meteora = src.check_meteora_deployment()
    if result.meteora.get("deployed_on_robinhood_chain"):
        result.data_warnings.append(
            "Meteora 部署状态异常：检测到部署，请人工复核筛选宇宙")

    # 3. 拉取 + 归一化 + 补充
    if cfg is None:
        cfg = load_screening_config()
    snapshots, warnings, index_status = collect_snapshots(cfg)
    result.data_warnings.extend(warnings)
    result.index_status = index_status
    if not snapshots:
        result.reason = "无可用池子数据（rhpools 索引服务不可用或尚无数据）"
        return result

    # 4. 评估（含未通过池子，用于展示扣分原因）
    week_lookup: dict[str, PoolSnapshot] = {}
    for pool in snapshots:
        if pool.volume_7d is not None:
            week = PoolSnapshot(
                pool_id=pool.pool_id, pair=pool.pair, protocol=pool.protocol,
                token0=pool.token0, token1=pool.token1,
                volume_usd=pool.volume_7d, fees_usd=pool.fees_7d,
                window="7d", window_complete=pool.window_7d_complete,
            )
            week_lookup[pool.pool_id] = week

    evaluations = [
        evaluate_pool(pool, cfg, week_lookup.get(pool.pool_id))
        for pool in snapshots
    ]
    result.all_evaluations = evaluations
    result.evaluations = rank_pools(evaluations, cfg)
    result.ok = True
    result.reason = (
        f"筛选完成：{len(snapshots)} 个池子中 {len(result.evaluations)} 个通过全部条件"
    )
    return result

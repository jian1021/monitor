"""fee-screen Streamlit 页面共享的缓存数据加载（仅 UI 层使用 streamlit）。"""

from __future__ import annotations

import json

import streamlit as st

from app.application.fee_screen.service import run_screening
from app.domain.fee_screen.models import ScreenConfig
from app.infrastructure.market_data import rh_fee_sources as src


@st.cache_data(ttl=300, show_spinner="正在拉取 Robinhood Chain 池子数据（rhpools + DexScreener）...")
def run_screening_cached(cfg_dict: str) -> dict:
    """缓存筛选结果（300s），页面二/三共用同一份缓存。"""
    cfg = ScreenConfig(**json.loads(cfg_dict))
    result = run_screening(cfg)
    return {
        "ok": result.ok,
        "reason": result.reason,
        "chain": result.chain,
        "meteora": result.meteora,
        "warnings": result.data_warnings,
        "fetched_at": result.fetched_at,
        "evaluations": result.evaluations,
        "all_evaluations": result.all_evaluations,
    }


def current_screening_cfg() -> ScreenConfig:
    from app.application.fee_screen.service import load_screening_config
    return load_screening_config()


def screening_cfg_json() -> str:
    cfg = current_screening_cfg()
    return json.dumps({k: getattr(cfg, k) for k in vars(cfg)}, ensure_ascii=False)


def reevaluate_with_candles(pool_id: str) -> dict | None:
    """单池详情：重建 5 分钟 K 线后重新评估（列表页的评估不含 K 线趋势）。

    返回 {evaluation, market}；池子不在当前筛选结果时返回 None。
    """
    data = run_screening_cached(screening_cfg_json())
    target = None
    week = None
    for ev in data.get("all_evaluations") or []:
        if ev.pool.pool_id == pool_id:
            target = ev.pool
            break
    if target is None:
        return None

    market = pool_market_series(pool_id)
    target.candles_5m = market["candles"]
    target.candles_source = market["source"]

    from app.application.fee_screen.service import load_screening_config
    from app.domain.fee_screen.models import PoolSnapshot
    from app.domain.fee_screen.screening import evaluate_pool

    if target.volume_7d is not None:
        week = PoolSnapshot(
            pool_id=target.pool_id, pair=target.pair, protocol=target.protocol,
            token0=target.token0, token1=target.token1,
            volume_usd=target.volume_7d, fees_usd=target.fees_7d,
            window="7d", window_complete=target.window_7d_complete,
        )
    evaluation = evaluate_pool(target, load_screening_config(), week)
    return {"evaluation": evaluation, "market": market}


@st.cache_data(ttl=300, show_spinner="拉取池子窗口数据（1h/24h/7d/30d）...")
def pool_window_series(pool_id: str) -> dict:
    """单池四个窗口的 volume/fees/active_tvl（窗口聚合对比，非逐日序列）。"""
    series = {"windows": [], "volume": [], "fees": [], "active_tvl": [],
              "fees_daily": [], "fat_daily": []}
    for window, days in (("1h", 1 / 24), ("24h", 1.0), ("7d", 7.0), ("30d", 30.0)):
        payload = src.fetch_pools(window=window, sort="fees", limit=100)
        row = None
        for candidate in (payload.get("rows") or [] if payload.get("ok") else []):
            if str(candidate.get("id")) == pool_id:
                row = candidate
                break
        if row is None:
            # 未进前 100 的池子单独标注，不编造
            series["windows"].append(window)
            series["volume"].append(None)
            series["fees"].append(None)
            series["active_tvl"].append(None)
            series["fees_daily"].append(None)
            series["fat_daily"].append(None)
            continue
        volume = row.get("volume_usd")
        fees = row.get("fees_usd")
        active = row.get("observed_active_tvl_usd")
        fees_daily = fees / days if isinstance(fees, (int, float)) else None
        fat_daily = (fees_daily / active) if (
            isinstance(fees_daily, (int, float)) and isinstance(active, (int, float))
            and active > 0) else None
        series["windows"].append(window)
        series["volume"].append(volume)
        series["fees"].append(fees)
        series["active_tvl"].append(active)
        series["fees_daily"].append(fees_daily)
        series["fat_daily"].append(fat_daily)
    return series


@st.cache_data(ttl=60, show_spinner="重建 5 分钟 K 线（链上逐事件价格，样本有限）...")
def pool_market_series(pool_id: str) -> dict:
    """从 tape 逐事件重建价格 K 线与 5 分钟成交量/手续费序列。"""
    events = src.fetch_pool_events(pool_id, limit=600)
    candles = src.build_candles_5m(events)
    # 5 分钟成交量/手续费（仅 swap 事件，usd 口径来自事件价格）
    buckets: dict[int, dict] = {}
    for event in events:
        if event.get("kind") != "swap":
            continue
        ts = src.to_ts(event.get("timestamp"))
        if ts <= 0:
            continue
        p0 = src.to_float_or_none(event.get("price0_usd")) or 0.0
        p1 = src.to_float_or_none(event.get("price1_usd")) or 0.0
        a0 = src.to_float_or_none(event.get("amount0")) or 0.0
        a1 = src.to_float_or_none(event.get("amount1")) or 0.0
        f0 = src.to_float_or_none(event.get("fee_amount0")) or 0.0
        f1 = src.to_float_or_none(event.get("fee_amount1")) or 0.0
        volume = abs(a0 * p0) + abs(a1 * p1)
        fees = abs(f0 * p0) + abs(f1 * p1)
        if volume <= 0 and fees <= 0:
            continue
        bucket = int(ts // 300) * 300
        item = buckets.setdefault(bucket, {"ts": bucket, "volume": 0.0, "fees": 0.0,
                                           "swaps": 0})
        item["volume"] += volume
        item["fees"] += fees
        item["swaps"] += 1
    series = [buckets[k] for k in sorted(buckets)]
    return {
        "candles": candles,
        "flow": series,
        "event_count": len(events),
        "swap_count": len(series) and sum(s["swaps"] for s in series),
        "source": (
            f"链上事件重建（tape 最近 {len(events)} 个事件、{len(series)} 根 5 分钟线；"
            "rhpools 公开 API 不提供逐日历史序列）"
            if events else "数据不可用：tape 无该池事件（池子过冷或索引回填未覆盖）"),
    }

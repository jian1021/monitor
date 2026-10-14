"""第一层：代币基本属性与风险筛选。

核心原则（来自方案）：
* **先筛代币基本属性，再看 RSI。**
* 每个池子输出 ``PASS`` / ``REJECT`` / ``REVIEW``。
* 无法获取的风险信息一律标记为「未知」，绝不能默认视为安全。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from src.meteora_api import (
    is_meme_pool,
    meme_market_cap,
    meme_mint,
    meme_side,
    meme_symbol,
    to_float,
)

PASS = "PASS"
REJECT = "REJECT"
REVIEW = "REVIEW"

_ADDR_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

# 数据源目前没有稳定的「近期交易笔数」字段，命中任意一个就当作可用。
_TRADE_FIELDS = ("trades_24h", "trade_count_24h", "txns_24h", "swaps_24h")


@dataclass
class ScreenResult:
    pool_address: str
    status: str
    reasons: list = field(default_factory=list)
    unknowns: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    pool: dict | None = field(default=None, repr=False)

    @property
    def passed(self) -> bool:
        return self.status == PASS


def pool_age_hours(pool, now=None) -> float | None:
    created_ms = to_float((pool or {}).get("created_at"))
    if created_ms <= 0:
        return None
    base = now if now is not None else time.time()
    return (base - created_ms / 1000.0) / 3600.0


def pool_metrics(pool, now=None) -> dict:
    """把 datapi 池子字段归一化成筛选用指标。"""
    tvl_raw = (pool or {}).get("tvl")
    volume_block = (pool or {}).get("volume") or {}
    volume_raw = volume_block.get("24h") if isinstance(volume_block, dict) else None
    side = meme_side(pool) or {}

    tvl = to_float(tvl_raw) if tvl_raw is not None else None
    volume_24h = to_float(volume_raw) if volume_raw is not None else None
    ratio = None
    if tvl is not None and volume_24h is not None and tvl > 0:
        ratio = volume_24h / tvl

    trades = None
    for name in _TRADE_FIELDS:
        if (pool or {}).get(name) is not None:
            trades = to_float(pool.get(name))
            break

    pool_config = (pool or {}).get("pool_config") or {}
    return {
        "symbol": meme_symbol(pool) or str((pool or {}).get("name") or "")[:24],
        "name": str((pool or {}).get("name") or ""),
        "meme_mint": meme_mint(pool),
        "meme_price_usd": to_float(side.get("price")) or None,
        "tvl": tvl,
        "volume_24h_usd": volume_24h,
        "volume_tvl_ratio": ratio,
        "trades_24h": trades,
        "market_cap": meme_market_cap(pool) if side else None,
        "holders": side.get("holders"),
        "freeze_authority_disabled": side.get("freeze_authority_disabled"),
        "is_verified": side.get("is_verified"),
        "is_blacklisted": bool((pool or {}).get("is_blacklisted")),
        "bin_step": to_float((pool_config or {}).get("bin_step")),
        "base_fee_pct": to_float((pool_config or {}).get("base_fee_pct")),
        "age_hours": pool_age_hours(pool, now=now),
    }


def screen_pool(pool, cfg, previous_tvl=None, now=None) -> ScreenResult:
    """对单个池子执行第一层筛选。"""
    if not isinstance(pool, dict):
        return ScreenResult(pool_address="", status=REJECT, reasons=["池子数据缺失"])

    address = str(pool.get("address") or "")
    if not _ADDR_RE.match(address):
        return ScreenResult(pool_address=address, status=REJECT, reasons=["池子地址非法或缺失"])
    if not is_meme_pool(pool):
        return ScreenResult(pool_address=address, status=REJECT,
                            reasons=["非 meme 池（两边都是计价币）"])

    metrics = pool_metrics(pool, now=now)
    reasons: list[str] = []
    review_reasons: list[str] = []
    unknowns: list[str] = []

    # --- 硬性排除：黑名单 / 冻结权限 ---
    if metrics["is_blacklisted"]:
        reasons.append("池子被 Meteora 拉黑 (is_blacklisted)")
    freeze = metrics["freeze_authority_disabled"]
    if freeze is False:
        reasons.append("代币冻结权限未关闭 (freeze_authority_disabled=false)")
    elif freeze is None:
        unknowns.append("冻结权限未知")

    # --- 流动性 / TVL ---
    if metrics["tvl"] is None:
        review_reasons.append("TVL 缺失，数据不足")
    elif metrics["tvl"] < cfg.min_pool_tvl_usd:
        reasons.append(
            f"TVL 过低 ${metrics['tvl']:,.0f} < ${cfg.min_pool_tvl_usd:,.0f}")
    if (previous_tvl and cfg.max_tvl_drop_ratio > 0 and metrics["tvl"] is not None
            and previous_tvl > 0):
        drop = 1 - (metrics["tvl"] / previous_tvl)
        if drop > cfg.max_tvl_drop_ratio:
            reasons.append(f"TVL 较上轮骤降 {drop * 100:.0f}%")

    # --- 成交量与活跃度 ---
    if metrics["volume_24h_usd"] is None:
        review_reasons.append("24h 成交量缺失，数据不足")
    elif metrics["volume_24h_usd"] < cfg.min_volume_24h_usd:
        reasons.append(
            f"24h 成交量过低 ${metrics['volume_24h_usd']:,.0f} < "
            f"${cfg.min_volume_24h_usd:,.0f}")
    if (metrics["volume_tvl_ratio"] is not None
            and metrics["volume_tvl_ratio"] < cfg.min_volume_tvl_ratio):
        reasons.append(
            f"成交量/TVL 过低 {metrics['volume_tvl_ratio']:.2f} < "
            f"{cfg.min_volume_tvl_ratio:.2f}")
    if cfg.min_recent_trades > 0:
        if metrics["trades_24h"] is None:
            unknowns.append("近期交易笔数未知")
        elif metrics["trades_24h"] < cfg.min_recent_trades:
            reasons.append(
                f"近期交易笔数过低 {metrics['trades_24h']:.0f} < "
                f"{cfg.min_recent_trades}")

    # --- 市值 ---
    if cfg.min_market_cap_usd > 0:
        if not metrics["market_cap"]:
            review_reasons.append("市值缺失，数据不足")
        elif metrics["market_cap"] < cfg.min_market_cap_usd:
            reasons.append(
                f"市值过低 ${metrics['market_cap']:,.0f} < "
                f"${cfg.min_market_cap_usd:,.0f}")

    # --- 数据源无法提供、只能标未知的风险项 ---
    if metrics["holders"] is None:
        unknowns.append("持仓人数未知")
    unknowns.append("增发/铸造权限未知")
    unknowns.append("持币集中度未知")

    if reasons:
        return ScreenResult(address, REJECT, reasons=reasons, unknowns=unknowns,
                            metrics=metrics, pool=pool)
    if review_reasons:
        return ScreenResult(address, REVIEW, reasons=review_reasons,
                            unknowns=unknowns, metrics=metrics, pool=pool)
    if unknowns and cfg.strict_risk_unknown:
        return ScreenResult(address, REVIEW,
                            reasons=["关键风险字段未知，暂不进入自动候选"],
                            unknowns=unknowns, metrics=metrics, pool=pool)
    return ScreenResult(address, PASS, unknowns=unknowns, metrics=metrics, pool=pool)


def screen_pools(pools, cfg, tvl_history=None, logger=None, now=None) -> list[ScreenResult]:
    """批量筛选；tvl_history 为 {pool_address: 上轮 TVL} 用于流动性骤降检查。"""
    tvl_history = tvl_history or {}
    now = now if now is not None else time.time()
    results = []
    for pool in pools or []:
        address = str((pool or {}).get("address") or "")
        result = screen_pool(pool, cfg, previous_tvl=tvl_history.get(address), now=now)
        results.append(result)
        if logger is not None:
            logger.debug("第一层 %s: %s %s", address[:8], result.status, result.reasons)
    passed = sum(1 for r in results if r.status == PASS)
    rejected = sum(1 for r in results if r.status == REJECT)
    review = sum(1 for r in results if r.status == REVIEW)
    message = f"第一层筛选：PASS {passed} / REJECT {rejected} / REVIEW {review}"
    (logger.info if logger else print)(message)
    return results


def dedupe_by_meme_mint(pools) -> list:
    """同一 meme 代币可能有多个池子，只保留 TVL 最高的那个。"""
    best: dict[str, dict] = {}
    for pool in pools or []:
        if not isinstance(pool, dict):
            continue
        key = meme_mint(pool)
        if not key:
            continue
        if key not in best or to_float(pool.get("tvl")) > to_float(best[key].get("tvl")):
            best[key] = pool
    return list(best.values())

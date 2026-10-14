"""
“手续费机会观察评分”（文档 6.），范围 0~100。

组成与默认权重：
  - 24h 成交量        25
  - 24h Fees/TVL      25
  - TVL               20
  - 历史手续费稳定性   15
  - 价格波动风险       15

要求：
  - 权重可在页面调整。
  - 不同量纲先归一化/分档（本实现采用对数归一化 + 分档回退）。
  - 市值>100万属于硬性筛选条件，不作为加分项。
  - 历史数据不足时对应分项不计入并明确标记（剩余权重按比例重分配）。
  - 评分仅用于排序，不代表未来盈利概率。
"""

from __future__ import annotations

import math
from typing import Optional

DEFAULT_WEIGHTS = {
    "volume": 25.0,
    "fee_tvl": 25.0,
    "tvl": 20.0,
    "stability": 15.0,
    "volatility": 15.0,
}

COMPONENT_LABELS = {
    "volume": "24h 成交量",
    "fee_tvl": "24h Fees/TVL",
    "tvl": "TVL",
    "stability": "历史手续费稳定性",
    "volatility": "价格波动风险",
}


def _fnum(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def log_score(value: Optional[float], lo: float, hi: float) -> Optional[float]:
    """对数归一化：value<=lo → 0，value>=hi → 100。"""
    if value is None or value <= 0:
        return 0.0 if value is not None else None
    if value <= lo:
        return 0.0
    if value >= hi:
        return 100.0
    span = math.log10(hi) - math.log10(lo)
    if span <= 0:
        return 0.0
    return (math.log10(value) - math.log10(lo)) / span * 100.0


def stability_score(fee_series: list[float]) -> Optional[float]:
    """由历史 24h 手续费序列计算稳定性分（变异系数越小分越高）。至少 3 个点。"""
    vals = [v for v in fee_series if v is not None and v >= 0]
    if len(vals) < 3:
        return None
    mean = sum(vals) / len(vals)
    if mean <= 0:
        return 0.0
    var = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
    cv = math.sqrt(var) / mean
    return max(0.0, min(100.0, 100.0 * (1.0 - cv)))


def volatility_score_from_candles(candles: list[dict]) -> Optional[float]:
    """由 K 线计算波动风险分（波动越大分越低）。数据不足返回 None。"""
    from .indicators import realized_volatility

    hourly = realized_volatility(candles)
    if hourly is None:
        return None
    daily = hourly * math.sqrt(24.0)
    return max(0.0, min(100.0, 100.0 * (1.0 - min(daily / 0.15, 1.0))))


def volatility_score_fallback(price_change_24h: Optional[float]) -> Optional[float]:
    """无 K 线时，用 24h 涨跌幅绝对值近似波动风险。"""
    pc = _fnum(price_change_24h)
    if pc is None:
        return None
    return max(0.0, min(100.0, 100.0 * (1.0 - min(abs(pc) / 15.0, 1.0))))


def compute_score(
    row: dict,
    weights: Optional[dict] = None,
    *,
    stability: Optional[float] = None,
    volatility: Optional[float] = None,
    volatility_is_fallback: bool = False,
) -> dict:
    """
    计算评分。返回 {score, breakdown, missing_components, notes}。
    """
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        for k in w:
            if k in weights:
                try:
                    w[k] = float(weights[k])
                except (TypeError, ValueError):
                    pass

    components: dict[str, Optional[float]] = {
        "volume": log_score(_fnum(row.get("volume_24h")), 1e4, 1e7),
        "fee_tvl": log_score(_fnum(row.get("fee_tvl_ratio_24h")), 0.1, 5.0),
        "tvl": log_score(_fnum(row.get("tvl")), 1e4, 1e7),
        "stability": stability,
        "volatility": volatility,
    }

    notes: list[str] = []
    missing: list[str] = []
    if components["stability"] is None:
        missing.append("stability")
        notes.append("历史手续费稳定性数据不足（不计入评分）")
    if components["volatility"] is None:
        missing.append("volatility")
        notes.append("价格波动数据不足（不计入评分）")
    if volatility_is_fallback and components["volatility"] is not None:
        notes.append("波动风险使用 24h 涨跌幅近似（无 K 线）")

    present_weight = sum(w[k] for k in components if components[k] is not None)
    if present_weight <= 0:
        return {"score": 0.0, "breakdown": components, "missing_components": missing,
                "notes": notes + ["无可用指标，评分为 0"]}

    total = 0.0
    breakdown: dict[str, dict] = {}
    for k, val in components.items():
        if val is None:
            breakdown[k] = {"raw": None, "weight": w[k], "contribution": 0.0}
            continue
        contribution = val * (w[k] / present_weight)
        total += contribution
        breakdown[k] = {"raw": round(val, 2), "weight": w[k], "contribution": round(contribution, 2)}

    return {
        "score": round(max(0.0, min(100.0, total)), 2),
        "breakdown": breakdown,
        "missing_components": missing,
        "notes": notes,
    }


def formula_text(weights: Optional[dict] = None) -> str:
    """生成可读的评分公式说明。"""
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update({k: weights[k] for k in w if k in weights})
    parts = [f"{COMPONENT_LABELS[k]}×{w[k]:g}" for k in DEFAULT_WEIGHTS]
    return "评分 = ( " + " + ".join(parts) + " ) / (有效权重之和)，各指标先归一化到 0~100。"

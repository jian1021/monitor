"""fee-screen 核心筛选逻辑（纯函数，无 IO，可单测）。

设计原则（需求书）：
- 缺失字段一律 None，界面显示"数据不可用"，绝不把缺失当 0。
- 实际平均手续费率 = 真实手续费 / 对应成交量（计算值），不与界面显示的基础费率混为一谈。
- Active TVL 缺失时不得用总 TVL 冒充。
- 净收益与手续费收入分开计算，不重复扣除已包含在资产价值变化中的损失。
"""

from __future__ import annotations

import math
from typing import Any

from app.domain.fee_screen.models import (
    FeeEstimate,
    NetReturnEstimate,
    PoolEvaluation,
    PoolSnapshot,
    RiskLevel,
    ScreenConfig,
    TokenInfo,
    TrendAnalysis,
    to_float,
)

# 排名归一化的固定锚点（候选池不足 2 个时使用，文档化而非隐含）
ANCHOR_FEES_USD = 10_000.0
ANCHOR_FEES_ACTIVE_TVL = 0.10   # 10%
ANCHOR_VOLUME_USD = 1_000_000.0
VOLUME_SPIKE_RATIO = 3.0        # 24h 成交量超过 7d 日均 3 倍视为暴增


# ---------------------------------------------------------------- 归一化
def normalize_pool(row: dict[str, Any]) -> PoolSnapshot | None:
    """rhpools /api/lp/pools 的一行 → PoolSnapshot。字段缺失保持 None。"""
    if not isinstance(row, dict):
        return None
    pool_id = row.get("id")
    if not pool_id:
        return None

    def token(raw: Any) -> TokenInfo:
        raw = raw or {}
        return TokenInfo(
            address=str(raw.get("address") or ""),
            symbol=str(raw.get("symbol") or ""),
            decimals=raw.get("decimals") if isinstance(raw.get("decimals"), int) else None,
            name=str(raw.get("name") or ""),
        )

    coverage = row.get("coverage") or {}
    risks = [str(r) for r in (row.get("risks") or []) if r]
    notes = [
        f"窗口 {coverage.get('window')} 仅覆盖 {coverage.get('observed_s')}s（部分历史）"
    ] if coverage.get("complete") is False else []
    if coverage.get("fees_basis"):
        notes.append(f"手续费口径：{coverage['fees_basis']}")

    return PoolSnapshot(
        pool_id=str(pool_id),
        pair=str(row.get("pair") or ""),
        protocol=str(row.get("protocol") or "unknown"),
        token0=token(row.get("token0")),
        token1=token(row.get("token1")),
        pool_address=(str(row["id"]) if str(row.get("id", "")).startswith("0x")
                      and len(str(row.get("id", ""))) == 42 else None),
        fee_ppm=to_float(row.get("fee_ppm")) if row.get("fee_ppm") is not None else None,
        tvl_usd=to_float(row.get("tvl_usd")),
        active_tvl_usd=to_float(row.get("observed_active_tvl_usd")),
        volume_usd=to_float(row.get("volume_usd")),
        fees_usd=to_float(row.get("fees_usd")),
        swaps=to_float(row.get("swaps")),
        lp_count=to_float(row.get("lp_count")),
        price=to_float(row.get("price")),
        price_change_pct=to_float(row.get("price_change_pct")),
        window=str(coverage.get("window") or "24h"),
        window_complete=bool(coverage.get("complete") is True),
        window_observed_s=to_float(coverage.get("observed_s")),
        last_event_at=to_float(row.get("last_event_at")),
        risks=risks,
        coverage_notes=notes,
    )


# ---------------------------------------------------------------- 指标计算
def actual_fee_rate(pool: PoolSnapshot) -> float | None:
    """实际平均手续费率 = 窗口手续费 / 窗口成交量。缺失即 None。"""
    if pool.fees_usd is None or pool.volume_usd is None or pool.volume_usd <= 0:
        return None
    return pool.fees_usd / pool.volume_usd


def fees_active_tvl_ratio(pool: PoolSnapshot) -> float | None:
    """Fees / Active TVL。Active TVL 不可用时返回 None（绝不拿总 TVL 冒充）。"""
    if pool.fees_usd is None or not pool.active_tvl_usd or pool.active_tvl_usd <= 0:
        return None
    return pool.fees_usd / pool.active_tvl_usd


def persistence_ratio(volume_24h: float | None, volume_7d: float | None) -> float | None:
    """成交量持续性（近似）：24h 成交量 ÷ 7 天日均成交量。

    公开 API 未暴露每日成交量序列，严格"近 3 天 vs 此前 4 天"指标暂不可用；
    该近似值 >= 1 表示近日活跃度高于 7 天日均。
    """
    if volume_24h is None or volume_7d is None or volume_7d <= 0:
        return None
    return volume_24h / (volume_7d / 7.0)


# ---------------------------------------------------------------- 价格趋势
def _consecutive_big_red_candles(candles: list[dict], drop_pct: float) -> int:
    """从后往前数连续大阴线（收盘价较前一根跌幅 >= drop_pct）。"""
    if len(candles) < 2:
        return 0
    run = 0
    for i in range(len(candles) - 1, 0, -1):
        prev = to_float(candles[i - 1].get("close"))
        cur = to_float(candles[i].get("close"))
        if prev is None or cur is None or prev <= 0:
            break
        if (cur - prev) / prev * 100 <= -abs(drop_pct):
            run += 1
        else:
            break
    return run


def _lower_highs_lows(candles: list[dict]) -> bool:
    """是否持续创更低的高点和更低的低点（至少 3 个连续分形）。"""
    if len(candles) < 7:
        return False
    highs, lows = [], []
    for i in range(1, len(candles) - 1):
        h = to_float(candles[i].get("high"))
        l = to_float(candles[i].get("low"))
        ph = to_float(candles[i - 1].get("high"))
        pl = to_float(candles[i - 1].get("low"))
        nh = to_float(candles[i + 1].get("high"))
        nl = to_float(candles[i + 1].get("low"))
        if None in (h, l, ph, pl, nh, nl):
            continue
        if h > ph and h > nh:
            highs.append(h)
        if l < pl and l < nl:
            lows.append(l)
    if len(highs) < 3 or len(lows) < 3:
        return False
    return all(highs[i] < highs[i - 1] for i in range(1, len(highs))) and \
        all(lows[i] < lows[i - 1] for i in range(1, len(lows)))


def _candle_volatility(candles: list[dict]) -> float | None:
    """收盘价对数收益率的年化波动率（%，按 5 分钟线年化）。"""
    rets = []
    for i in range(1, len(candles)):
        prev = to_float(candles[i - 1].get("close"))
        cur = to_float(candles[i].get("close"))
        if prev and cur and prev > 0:
            rets.append(math.log(cur / prev))
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    # 5 分钟线：每年约 105120 根
    return math.sqrt(var * 105120) * 100


def analyze_trend(pool: PoolSnapshot, cfg: ScreenConfig) -> TrendAnalysis:
    """价格趋势与风险标记。优先用 DexScreener 聚合涨跌幅；K 线缺失时明确标注。"""
    ds = pool.ds_price_change or {}
    trend = TrendAnalysis(
        drop_1h_pct=to_float(ds.get("h1")),
        drop_4h_pct=to_float(ds.get("h6")),   # 数据源无 4h，用 6h 代替
        drop_24h_pct=to_float(ds.get("h24")),
        candle_source=pool.candles_source,
    )
    candles = pool.candles_5m or []
    closes = [to_float(c.get("close")) for c in candles]
    closes = [c for c in closes if c is not None]
    if closes:
        trend.high_24h = max(closes)
        trend.low_24h = min(closes)
    trend.consecutive_big_red = _consecutive_big_red_candles(candles, cfg.big_candle_drop_pct)
    trend.lower_highs_lows = _lower_highs_lows(candles)
    trend.volatility_pct = _candle_volatility(candles)

    reasons = []
    if trend.drop_1h_pct is not None and trend.drop_1h_pct <= -abs(cfg.max_drop_1h):
        reasons.append(f"1 小时跌幅 {trend.drop_1h_pct:.2f}% 超过阈值 -{cfg.max_drop_1h:g}%")
    if trend.drop_4h_pct is not None and trend.drop_4h_pct <= -abs(cfg.max_drop_4h):
        reasons.append(f"{trend.drop_4h_label} 跌幅 {trend.drop_4h_pct:.2f}% 超过阈值 -{cfg.max_drop_4h:g}%")
    if trend.consecutive_big_red >= cfg.big_candle_count:
        reasons.append(f"最近 {cfg.min_candles_for_trend} 根 5 分钟线出现 {trend.consecutive_big_red} 根连续大阴线")
    if trend.lower_highs_lows:
        reasons.append("价格持续创更低的高点和更低的低点（单边下跌）")
    trend.one_sided_downtrend = bool(
        trend.lower_highs_lows
        or (trend.drop_1h_pct is not None and trend.drop_4h_pct is not None
            and trend.drop_1h_pct <= -abs(cfg.max_drop_1h)
            and trend.drop_4h_pct <= -abs(cfg.max_drop_4h))
    )
    if trend.one_sided_downtrend:
        reasons.append("判定：明显单边下跌风险")
    trend.reasons = reasons
    return trend


# ---------------------------------------------------------------- 池子评估
def evaluate_pool(pool: PoolSnapshot, cfg: ScreenConfig,
                  pool_7d: PoolSnapshot | None = None) -> PoolEvaluation:
    """对单个池子做完整评估：筛选条件 + 趋势风险 + 扣分原因。"""
    ev = PoolEvaluation(pool=pool)
    ev.actual_fee_rate = actual_fee_rate(pool)
    ev.fees_active_tvl = fees_active_tvl_ratio(pool)
    ev.active_tvl_available = bool(pool.active_tvl_usd)
    ev.fee_rate_available = ev.actual_fee_rate is not None
    ev.persistence_ratio = persistence_ratio(
        pool.volume_usd, pool_7d.volume_usd if pool_7d else None)

    # ---- 初筛条件（缺失即不通过，不以 0 冒充）
    if pool.volume_usd is None:
        ev.failures.append("24 小时成交量：数据不可用")
    elif pool.volume_usd < cfg.min_volume_24h:
        ev.failures.append(f"24 小时成交量 ${pool.volume_usd:,.0f} < ${cfg.min_volume_24h:,.0f}")

    if pool.fees_usd is None:
        ev.failures.append("24 小时手续费：数据不可用")
    elif pool.fees_usd < cfg.min_fees_24h:
        ev.failures.append(f"24 小时手续费 ${pool.fees_usd:,.0f} < ${cfg.min_fees_24h:,.0f}")

    if ev.actual_fee_rate is None:
        ev.failures.append("实际平均手续费率：数据不可用（缺少手续费或成交量）")
    elif ev.actual_fee_rate < cfg.min_fee_rate:
        ev.failures.append(f"实际平均手续费率 {ev.actual_fee_rate * 100:.3f}% < {cfg.min_fee_rate * 100:g}%")

    if ev.fees_active_tvl is None:
        ev.failures.append("Fees/Active TVL：数据不可用（Active TVL 缺失）")
        ev.deductions.append("Active TVL 不可用，无法计算 Fees/Active TVL（-15 分）")
    elif ev.fees_active_tvl < cfg.min_fees_active_tvl:
        ev.failures.append(f"Fees/Active TVL {ev.fees_active_tvl * 100:.2f}% < {cfg.min_fees_active_tvl * 100:g}%")

    if not pool.window_complete:
        ev.deductions.append(f"窗口数据不完整（仅覆盖 {pool.window_observed_s or '?'}s，索引回填中）（-10 分）")
    if pool_7d is not None and not pool_7d.window_complete:
        ev.deductions.append("7 天窗口数据不完整（-5 分）")

    # 池子历史天数（数据源不直接提供创建日久的覆盖口径，用窗口覆盖近似）
    if cfg.min_history_days > 7:
        ev.deductions.append(f"要求至少 {cfg.min_history_days} 天历史：数据源未提供池子历史长度，标记数据不可用（-5 分）")

    # ---- 成交量持续性
    if ev.persistence_ratio is None:
        ev.failures.append("成交量持续性：数据不可用（缺少 7 天成交量）")
    elif ev.persistence_ratio < cfg.min_persistence_ratio:
        ev.failures.append(
            f"成交量持续性 {ev.persistence_ratio:.2f} < {cfg.min_persistence_ratio:g}"
            "（24h 成交量低于 7 天日均的设定比例，疑似快速衰退）")
    elif ev.persistence_ratio >= VOLUME_SPIKE_RATIO:
        ev.deductions.append(
            f"24h 成交量是 7 天日均的 {ev.persistence_ratio:.1f} 倍（疑似暴增），"
            "无每日序列无法确认是否快速衰退（-5 分）")

    # ---- 价格趋势
    ev.trend = analyze_trend(pool, cfg)
    if ev.trend.one_sided_downtrend:
        ev.deductions.append("明显单边下跌风险：降级（-30 分）")
    for reason in ev.trend.reasons:
        if "超过阈值" in reason:
            ev.deductions.append(f"价格风险：{reason}（-10 分）")

    # ---- 风险等级
    high = ev.trend.one_sided_downtrend or any("超过阈值" in r for r in ev.trend.reasons)
    incomplete = not pool.window_complete or not ev.active_tvl_available
    ev.risk_level = RiskLevel.HIGH if high else (RiskLevel.MEDIUM if incomplete else RiskLevel.LOW)

    ev.passed = not ev.failures
    return ev


# ---------------------------------------------------------------- 排名
def _min_max(values: list[float]) -> dict[int, float]:
    """min-max 归一化到 0-100；全部相同或为空时给中性分。"""
    if not values:
        return {}
    lo, hi = min(values), max(values)
    if hi <= lo:
        return {i: 50.0 for i in range(len(values))}
    return {i: (v - lo) / (hi - lo) * 100 for i, v in enumerate(values)}


def _anchor_score(value: float | None, anchor: float) -> float:
    """单池时的绝对锚点打分（文档化锚点，避免美元金额支配排名）。"""
    if value is None or value <= 0:
        return 0.0
    return min(100.0, 100.0 * math.log10(1 + value) / math.log10(1 + anchor))


def rank_pools(evaluations: list[PoolEvaluation], cfg: ScreenConfig) -> list[PoolEvaluation]:
    """按权重评分并排序（原地修改 score 后返回降序列表）。"""
    passed = [e for e in evaluations if e.passed]
    if not passed:
        return []

    fees_vals = [e.pool.fees_usd or 0.0 for e in passed]
    fat_vals = [e.fees_active_tvl or 0.0 for e in passed]
    vol_vals = [e.pool.volume_usd or 0.0 for e in passed]

    multi = len(passed) >= 2
    if multi:
        fees_scores = list(_min_max(fees_vals).values())
        fat_scores = list(_min_max(fat_vals).values())
        vol_scores = list(_min_max(vol_vals).values())
    else:
        fees_scores = [_anchor_score(v, ANCHOR_FEES_USD) for v in fees_vals]
        fat_scores = [_anchor_score(v, ANCHOR_FEES_ACTIVE_TVL) for v in fat_vals]
        vol_scores = [_anchor_score(v, ANCHOR_VOLUME_USD) for v in vol_vals]

    for i, ev in enumerate(passed):
        # 持续性：比例 >= 1.5 视为满分区间；缺失记 0 分（不通过筛选的池子不会进到这里）
        per_score = min(100.0, (ev.persistence_ratio or 0.0) / 1.5 * 100)
        # 趋势：扣分制
        trend_score = 100.0
        if ev.trend.one_sided_downtrend:
            trend_score -= 40
        trend_score -= 10 * sum(1 for r in ev.trend.reasons if "超过阈值" in r)
        trend_score -= 10 * (1 if ev.trend.consecutive_big_red >= cfg.big_candle_count else 0)
        trend_score = max(0.0, trend_score)

        components = {
            "24h手续费": fees_scores[i],
            "Fees/Active TVL": fat_scores[i],
            "24h成交量": vol_scores[i],
            "成交量持续性": per_score,
            "价格趋势与风险": trend_score,
        }
        ev.score_components = components
        ev.score = (
            cfg.w_fees * components["24h手续费"]
            + cfg.w_fees_active_tvl * components["Fees/Active TVL"]
            + cfg.w_volume * components["24h成交量"]
            + cfg.w_persistence * per_score
            + cfg.w_trend * trend_score
        )
        # 扣分项（数据质量）
        for d in ev.deductions:
            if "-15 分" in d:
                ev.score -= 15
            elif "-10 分" in d:
                ev.score -= 10
            elif "-5 分" in d:
                ev.score -= 5
            elif "-30 分" in d:
                ev.score -= 30
        ev.score = max(0.0, min(100.0, ev.score))

    passed.sort(key=lambda e: e.score, reverse=True)
    return passed


# ---------------------------------------------------------------- 手续费收入计算器
def estimate_fee_income(
    pool: PoolSnapshot,
    capital_usd: float,
    in_range_ratio: float,
    days: float = 1.0,
) -> FeeEstimate:
    """手续费收入估算。

    公式：用户每日手续费收入 = 池子每日实际手续费 × 用户有效流动性占比。
    有效流动性占比（集中流动性近似，标注假设）：
      share = (capital × in_range_ratio) / (active_tvl + capital × in_range_ratio)
    不用"资金 / 总 TVL"代替集中流动性下的真实分配。
    """
    assumptions = [
        "假设：资金全部为单边等价注入，且按 in_range_ratio 比例处于有效交易区间",
        "假设：有效流动性占比 = 资金×区间内时间占比 / (Active TVL + 资金×区间内时间占比)"
        "（集中流动性下手续费按区间内流动性单位分配，非按总 TVL 分配）",
        "手续费为毛收入口径（rhpools fees_basis: gross trading-fee estimate），未扣协议费分成",
    ]
    est = FeeEstimate(
        capital_usd=capital_usd,
        pool_daily_fees=pool.fees_usd,
        effective_share=None,
        daily_income=None,
        income_7d=None,
        income_30d=None,
        assumptions=assumptions,
        computable=False,
    )
    if pool.fees_usd is None:
        est.assumptions.insert(0, "池子 24h 手续费：数据不可用，无法估算收入")
        return est
    if not pool.active_tvl_usd or pool.active_tvl_usd <= 0:
        est.assumptions.insert(0, "Active TVL：数据不可用，无法计算有效流动性占比（不用总 TVL 冒充）")
        return est

    effective = capital_usd * max(0.0, min(1.0, in_range_ratio))
    share = effective / (pool.active_tvl_usd + effective) if (pool.active_tvl_usd + effective) > 0 else 0.0
    daily = pool.fees_usd * share
    est.effective_share = share
    est.daily_income = daily
    est.income_7d = daily * 7
    est.income_30d = daily * 30
    est.computable = True
    return est


# ---------------------------------------------------------------- 净收益分析
def estimate_net_return(
    pool: PoolSnapshot,
    capital_usd: float,
    fee_estimate: FeeEstimate,
    days: float,
    price_change_pct: float | None,
    entry_cost: float = 0.0,
    exit_cost: float = 0.0,
    rebalance_cost: float = 0.0,
) -> NetReturnEstimate:
    """LP 相对净收益（估算）。

    公式：LP 相对净收益 = LP 最终资产价值 - 基准策略最终资产价值
                        + 已收取手续费 + 奖励收入 - 额外交易成本

    基准策略 = 持有相同初始资产（50/50）。无常损失已体现在
    "LP 最终资产价值 - 基准最终资产价值"中，绝不重复扣除。
    采用全区间常积模型近似；集中流动性的实际 IL 取决于价格区间，
    缺完整价格路径时返回 computable=False 并说明原因。
    """
    result = NetReturnEstimate(
        capital_usd=capital_usd,
        days=days,
        fee_income=(fee_estimate.daily_income * days) if fee_estimate.computable else None,
        reward_income=0.0,  # Robinhood Chain 池无第三方奖励数据，按 0 处理并标注
        il_pct=None,
        entry_cost=entry_cost,
        exit_cost=exit_cost,
        rebalance_cost=rebalance_cost,
        computable=False,
        assumptions=[
            "无常损失模型：全区间常积近似（2√r/(1+r)）；集中流动性仓位的实际 IL 取决于价格区间，可能更大",
            "价格变动 r 取观测窗口价格变化作为持有期代理（非预测）",
            "奖励收入：数据源无 Robinhood Chain 池的第三方奖励，按 0 处理",
            "手续费收入沿用手续费估算的假设（有效流动性占比）",
        ],
    )
    if price_change_pct is None:
        result.unavailable_reason = (
            "缺少持有期价格变化，净收益无法准确计算（不将手续费收入直接当作净利润）")
        return result
    if not fee_estimate.computable:
        result.unavailable_reason = (
            "手续费收入无法估算（Active TVL 或手续费数据不可用），净收益无法准确计算")
        return result

    r = 1.0 + price_change_pct / 100.0
    if r <= 0:
        result.unavailable_reason = "价格变化导致价格非正，净收益无法计算"
        return result

    # LP 最终价值（常积）：V0·√r；基准（50/50 持有）：V0·(1+r)/2
    lp_final = capital_usd * math.sqrt(r)
    baseline_final = capital_usd * (1.0 + r) / 2.0
    il_pct = (2 * math.sqrt(r) / (1 + r) - 1) * 100  # 相对持有的损失，仅展示，不重复扣除

    fees_days = (fee_estimate.daily_income or 0.0) * days
    net = lp_final - baseline_final + fees_days + 0.0 - entry_cost - exit_cost - rebalance_cost

    result.lp_final_value = lp_final
    result.baseline_final_value = baseline_final
    result.il_pct = il_pct
    result.relative_net_return = net
    result.computable = True
    return result

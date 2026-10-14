"""fee-screen 领域模型（纯数据，无 IO）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Protocol(str, Enum):
    V2 = "v2"
    V3 = "v3"
    V4 = "v4"
    SLIPSTREAM = "slipstream"
    UNKNOWN = "unknown"


class RiskLevel(str, Enum):
    LOW = "低"
    MEDIUM = "中"
    HIGH = "高"


@dataclass
class TokenInfo:
    address: str
    symbol: str = ""
    decimals: int | None = None
    name: str = ""


@dataclass
class PoolSnapshot:
    """单个流动池的一个时间窗口快照（全部字段可空，缺失即"数据不可用"）。"""

    pool_id: str
    pair: str
    protocol: str
    token0: TokenInfo
    token1: TokenInfo
    # 池子身份
    pool_address: str | None = None      # v3/v2：20 字节池地址
    manager_address: str | None = None   # v4：单例管理器地址（非池子）
    fee_ppm: int | None = None           # 配置费率（ppm）；动态费率池可能为 null
    # 窗口指标（window 由调用方指定，通常 24h）
    tvl_usd: float | None = None
    active_tvl_usd: float | None = None  # 观测区间内 TVL；可能为 null
    volume_usd: float | None = None
    fees_usd: float | None = None
    swaps: int | None = None
    lp_count: int | None = None
    price: float | None = None
    price_change_pct: float | None = None
    window: str = "24h"
    window_complete: bool = False
    window_observed_s: float | None = None
    # 7 天窗口数据（join 自 rhpools window=7d，用于成交量持续性）
    volume_7d: float | None = None
    fees_7d: float | None = None
    window_7d_complete: bool = False
    created_at: int | None = None
    last_event_at: int | None = None
    risks: list[str] = field(default_factory=list)
    coverage_notes: list[str] = field(default_factory=list)
    # DexScreener 补充（独立数据源）
    ds_price_change: dict[str, float] = field(default_factory=dict)  # m5/h1/h6/h24
    ds_pair_created_at: int | None = None
    ds_liquidity_usd: float | None = None
    ds_url: str = ""
    # 重建的 5 分钟 K 线（rhpools 逐事件价格分桶，样本有限）
    candles_5m: list[dict] = field(default_factory=list)  # [{ts, open, high, low, close}]
    candles_source: str = "数据不可用"
    fetched_at: float = 0.0

    @property
    def pool_address_display(self) -> str:
        return self.pool_address or self.manager_address or self.pool_id


@dataclass
class ScreenConfig:
    """筛选参数（网页可改）。"""

    # 成交量与手续费
    min_volume_24h: float = 500_000.0
    min_fees_24h: float = 5_000.0
    min_fee_rate: float = 0.01          # 实际平均手续费率 1%
    min_fees_active_tvl: float = 0.05   # Fees/Active TVL 5%
    min_history_days: int = 7
    # 成交量持续性（24h vs 7d 日均的近似比例）
    min_persistence_ratio: float = 0.5
    # 价格趋势风险
    max_drop_1h: float = 8.0            # %
    max_drop_4h: float = 15.0           # %（数据源无 4h，用 6h 代替）
    big_candle_drop_pct: float = 1.0    # 连续大阴线阈值（收盘价跌幅 %）
    big_candle_count: int = 3           # 连续根数
    min_candles_for_trend: int = 12
    # 排名权重
    w_fees: float = 0.25
    w_fees_active_tvl: float = 0.25
    w_volume: float = 0.20
    w_persistence: float = 0.15
    w_trend: float = 0.15


@dataclass
class TrendAnalysis:
    drop_1h_pct: float | None = None
    drop_4h_pct: float | None = None        # 实际为 6h（数据源无 4h）
    drop_4h_label: str = "6h"
    drop_24h_pct: float | None = None
    high_24h: float | None = None
    low_24h: float | None = None
    consecutive_big_red: int = 0
    lower_highs_lows: bool = False
    one_sided_downtrend: bool = False
    volatility_pct: float | None = None
    candle_source: str = "数据不可用"
    reasons: list[str] = field(default_factory=list)


@dataclass
class PoolEvaluation:
    pool: PoolSnapshot
    passed: bool = False
    risk_level: RiskLevel = RiskLevel.MEDIUM
    score: float = 0.0
    score_components: dict[str, float] = field(default_factory=dict)
    deductions: list[str] = field(default_factory=list)   # 扣分原因
    failures: list[str] = field(default_factory=list)     # 未通过条件
    trend: TrendAnalysis = field(default_factory=TrendAnalysis)
    actual_fee_rate: float | None = None
    fees_active_tvl: float | None = None
    persistence_ratio: float | None = None
    fee_rate_available: bool = False
    active_tvl_available: bool = False


@dataclass
class FeeEstimate:
    capital_usd: float
    pool_daily_fees: float | None
    effective_share: float | None
    daily_income: float | None
    income_7d: float | None
    income_30d: float | None
    assumptions: list[str] = field(default_factory=list)
    computable: bool = False


@dataclass
class NetReturnEstimate:
    capital_usd: float
    days: float
    fee_income: float | None
    reward_income: float | None
    il_pct: float | None            # 无常损失（常积近似，%）
    entry_cost: float | None
    exit_cost: float | None
    rebalance_cost: float | None
    lp_final_value: float | None = None
    baseline_final_value: float | None = None
    relative_net_return: float | None = None
    computable: bool = False
    assumptions: list[str] = field(default_factory=list)
    unavailable_reason: str = ""


def to_float(value: Any, default: float | None = None) -> float | None:
    """安全转 float；None/NaN/非法 → default（绝不把缺失变成 0）。"""
    if value is None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if result != result:  # NaN
        return default
    return result

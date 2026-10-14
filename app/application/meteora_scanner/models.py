"""数据模型（与官方 API 字段一一对应，字段名以实测为准）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TokenInfo:
    """代币信息。

    来源：DLMM 官方 API 返回的 token_x / token_y 对象。

    市值口径：market_cap 为流通市值（若仅有 FDV 时会置入 fdv 并标注）。
    """

    mint: str
    name: str = ""
    symbol: str = ""
    market_cap: Optional[float] = None      # 流通市值 (USD)
    fdv: Optional[float] = None             # 完全稀释估值 (USD)
    price: Optional[float] = None           # 代币价格 (USD)
    price_change_24h: Optional[float] = None  # 24h 涨跌幅 (%)
    market_cap_type: str = ""               # "market_cap" 或 "fdv"
    source: str = ""                        # 数据来源标识
    holders: Optional[int] = None          # 持仓地址数
    top10_holders: Optional[float] = None  # 前10持仓占比 (%)
    dev_holders: Optional[float] = None     # 开发方持仓占比 (%)
    freeze_auth: Optional[bool] = None      # 是否冻结授权
    mint_auth: Optional[bool] = None        # 是否铸币授权

    def best_market_cap(self) -> Optional[float]:
        """返回可用于市值筛选的数值：优先流通市值，其次 FDV。"""
        if self.market_cap is not None and self.market_cap > 0:
            return self.market_cap
        if self.fdv is not None and self.fdv > 0:
            return self.fdv
        return None


@dataclass
class PoolRecord:
    """标准化后的池子记录。"""

    pool_address: str
    pool_type: str                       # "DLMM"
    token_x: TokenInfo
    token_y: TokenInfo
    name: str = ""
    tvl: Optional[float] = None
    volume_24h: Optional[float] = None
    fees_24h: Optional[float] = None
    fee_rate_pct: Optional[float] = None       # 池配置手续费率 (%)
    fee_tvl_ratio_24h: Optional[float] = None  # 24h Fees/TVL (%)，自行按公式计算
    price: Optional[float] = None              # 池当前价格
    price_change_24h: Optional[float] = None   # 24h 价格变化 (%)
    apr: Optional[float] = None
    apy: Optional[float] = None
    bin_step: Optional[int] = None             # DLMM bin step
    created_at: Optional[int] = None           # Unix 秒
    data_source: str = ""
    first_seen: Optional[str] = None
    last_updated: Optional[str] = None
    open_positions: Optional[int] = None       # 开仓人数

    def key(self) -> str:
        return f"{self.pool_type}:{self.pool_address}"

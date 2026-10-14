"""
全局配置与默认参数。

所有 API 地址、限频、分页大小均已针对官方接口实测验证（见 README 的“API 验证”一节）。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from app.core.settings import FEISHU_WEBHOOK

# Meteora 模块目录；其配置与持久化数据由主项目统一管理。
MODULE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = MODULE_ROOT
load_dotenv(MODULE_ROOT / ".env")


def _get_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


def _get_int(key: str, default: int) -> int:
    try:
        return int(float(os.getenv(key, str(default))))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------
# 官方 API 基础地址（均已实测可用）
# --------------------------------------------------------------------------
# DLMM 池列表/池详情：Meteora 前端的池发现接口，一次性返回「Token Info」面板全部字段
# （持有人数、前10持仓占比、开发方持仓占比、冻结/铸币授权、开仓人数 open_positions 等）。
DLMM_BASE_URL = os.getenv("METEORA_DLMM_BASE_URL", "https://pool-discovery-api.datapi.meteora.ag").rstrip("/")
# DLMM K 线接口（pool-discovery 不提供 OHLCV，仅此用途）。
DLMM_KLINE_BASE_URL = os.getenv("METEORA_DLMM_KLINE_BASE_URL", "https://dlmm.datapi.meteora.ag").rstrip("/")
# CoinGecko（页面⑨ 价格区间计算的数据源；填入 API Key 后自动切换到 pro 域名）
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "").strip()
COINGECKO_BASE_URL = (
    "https://pro-api.coingecko.com/api/v3" if COINGECKO_API_KEY else "https://api.coingecko.com/api/v3"
).rstrip("/")
# DEX Paprika（页面⑨ 池级 K 线数据源）。
# 免费注册 Key（console.dexpaprika.com）解锁 10m+ 粒度 + 最近 7 天；无 Key 仅 1h+ 且最近 24h。
DEXPAPRIKA_API_KEY = os.getenv("DEXPAPRIKA_API_KEY", "").strip()
DEXPAPRIKA_BASE_URL = os.getenv("DEXPAPRIKA_BASE_URL", "https://api.dexpaprika.com").rstrip("/")

# --------------------------------------------------------------------------
# 网络参数
# --------------------------------------------------------------------------
HTTP_TIMEOUT = _get_float("HTTP_TIMEOUT", 30.0)
HTTP_MAX_RETRIES = _get_int("HTTP_MAX_RETRIES", 4)

# 限频（默认保守取值）
DLMM_RPS = _get_float("DLMM_RPS", 25.0)
COINGECKO_RPS = _get_float("COINGECKO_RPS", 5.0)  # 免费接口限频较严，默认保守取值
DEXPAPRIKA_RPS = _get_float("DEXPAPRIKA_RPS", 10.0)  # 免费档 30 req/min，默认保守取值

# 分页大小
DLMM_PAGE_SIZE = _get_int("DLMM_PAGE_SIZE", 1000)

# --------------------------------------------------------------------------
# 数据库路径
# --------------------------------------------------------------------------
_db_env = os.getenv("DB_PATH", "data/meteora.db")
_db_path = Path(_db_env)
DB_PATH: Path = _db_path if _db_path.is_absolute() else (MODULE_ROOT / _db_path)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------
# 飞书
# --------------------------------------------------------------------------
FEISHU_WEBHOOK_SECRET = os.getenv("FEISHU_WEBHOOK_SECRET", "").strip()

# --------------------------------------------------------------------------
# 池类型
# --------------------------------------------------------------------------
POOL_TYPE_DLMM = "DLMM"
SUPPORTED_POOL_TYPES = [POOL_TYPE_DLMM]

# --------------------------------------------------------------------------
# 默认筛选参数（文档 3. 默认筛选参数）
# --------------------------------------------------------------------------
DEFAULT_FILTERS = {
    "min_market_cap": 1_000_000.0,       # 代币市值 严格大于 100 万 USD
    "market_cap_mode": "any",            # any=任一代币满足(默认) / both=两个都满足
    "min_volume_24h": 100_000.0,         # 24h 成交量 >= 10 万 USD
    "min_tvl": 50_000.0,                 # TVL >= 5 万 USD
    "min_fee_tvl_ratio": 0.5,            # 24h Fees/TVL >= 0.5 %
    "bin_step_min": 80,                  # DLMM bin step 最小值，默认 80
    "bin_step_max": 0,                   # DLMM bin step 最大值，0 表示不限制
    "min_fee_rate_pct": 0.0,             # 池配置费率下限(%)，默认 0%（不限制）
    "max_fee_rate_pct": 0.0,             # 池配置费率上限(%)，默认 0%（不限制）
    "min_token_x_holders": 10000,        # 代币X持仓地址数下限，默认 10K
    "max_token_x_top10_holders": 30.0,   # 代币X前10持仓占比上限，默认 30%
    "max_token_x_dev_holders": 0.0,      # 代币X开发方持仓占比上限，默认 0%
    "token_x_freeze_auth": "no",         # 代币X冻结授权，默认 no
    "token_x_mint_auth": "no",           # 代币X铸币授权，默认 no
    "min_open_positions": 100,            # 开仓人数下限，默认 100
    "pool_types": list(SUPPORTED_POOL_TYPES),
    "require_complete": True,            # 关键指标必须通过校验
}

# --------------------------------------------------------------------------
# 评分权重默认值（文档 6. 评分系统），范围 0~100
# --------------------------------------------------------------------------
DEFAULT_WEIGHTS = {
    "volume": 25.0,       # 24h 成交量
    "fee_tvl": 25.0,      # 24h Fees/TVL
    "tvl": 20.0,          # TVL
    "stability": 15.0,    # 历史手续费收入稳定性
    "volatility": 15.0,   # 价格波动风险
}

SIGNAL_NAMES = {
    "rsi3_oversold": "RSI(3) 超卖",
    "stop_new_low": "停止创新低",
    "break_local_high": "突破局部高点",
    "volume_recovery": "成交量恢复",
}

# 项目版本
APP_VERSION = "V3.1"
APP_TITLE = "Meteora 全池扫描与筛选器"

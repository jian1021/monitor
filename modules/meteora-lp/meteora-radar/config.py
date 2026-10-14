"""筛选阈值与运行配置。

所有阈值都集中在 ``RadarConfig`` 里，并且都可以用环境变量覆盖（环境变量名 =
``RADAR_`` + 字段大写）。方案要求「阈值配置化，具体数值根据数据与回测确定」，
因此这里只给保守默认值，方便后续回测后调整，不需要改代码。

用法::

    from config import load_config
    cfg = load_config()
"""

import os
from dataclasses import dataclass, fields
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # 允许在没装 python-dotenv 时仅依赖系统环境变量运行
    def load_dotenv(*_args, **_kwargs):
        return False


BASE_DIR = Path(__file__).resolve().parent

# 沿用现有 monitor 项目的约定：只加载项目自带的 .env，避免受启动 CWD 影响。
_ENV_PATH = BASE_DIR / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)


def _env(name, default):
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _env_str(name, default):
    return str(_env(name, default))


def _env_float(name, default):
    try:
        return float(_env(name, default))
    except (TypeError, ValueError):
        return float(default)


def _env_int(name, default):
    try:
        return int(float(_env(name, default)))
    except (TypeError, ValueError):
        return int(default)


def _env_bool(name, default):
    value = os.getenv(name)
    if value in (None, ""):
        return bool(default)
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def _env_horizons(name, default):
    raw = _env(name, "")
    if not raw:
        return tuple(default)
    out = []
    for part in str(raw).replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(float(part)))
        except (TypeError, ValueError):
            continue
    return tuple(out) or tuple(default)


DATA_DIR = Path(_env_str("RADAR_DATA_DIR", str(BASE_DIR / "data")))
LOG_DIR = Path(_env_str("RADAR_LOG_DIR", str(BASE_DIR / "logs")))
CANDIDATES_FILE = DATA_DIR / "candidates.json"
LOG_FILE = LOG_DIR / "radar.log"


@dataclass
class RadarConfig:
    """一次筛选/监控流程的全部可调参数。"""

    # ---- 第一层：代币基本属性筛选 ----
    min_pool_tvl_usd: float = 50_000.0        # 最低池子 TVL
    min_volume_24h_usd: float = 50_000.0      # 最低 24h 成交量
    min_volume_tvl_ratio: float = 0.5         # 最低 成交量/TVL
    min_recent_trades: int = 0                # 近期最低交易笔数；0=不启用（数据源暂不提供）
    min_market_cap_usd: float = 0.0           # 最低市值；0=不启用
    max_tvl_drop_ratio: float = 0.5           # 相对上一轮 TVL 的最大跌幅；0=不启用流动性骤降检查
    strict_risk_unknown: bool = False         # True=风险字段未知时把池子降为 REVIEW

    # ---- 第二层：1 小时 RSI 超跌 ----
    rsi_timeframe: str = "1h"
    rsi_period: int = 14                      # 现有 monitor 项目其他模块用的是 3，这里默认标准 14，可覆盖
    rsi_1h_max: float = 10.0                  # RSI < 10 视为超跌候选
    rsi_top_n: int = 40                       # 初筛通过后，只对成交量最大的前 N 个池算 RSI

    # ---- 第三层：5 分钟止跌 / 反弹信号 ----
    signal_timeframe: str = "5m"
    rebound_lookback_bars: int = 5            # 3~6 根 5m K 线窗口
    rebound_stabilize_bars: int = 2           # 最近几根不再创新低即视为止跌
    rebound_volume_factor: float = 1.0        # 突破量 / 近期均量的最低倍数
    require_volume_confirmation: bool = False # True=突破必须放量才提醒
    use_closed_bar: bool = True               # True=只使用已收线 K 线，忽略当前未完成那根

    # ---- 数据源 ----
    data_api_base: str = "https://dlmm.datapi.meteora.ag"
    pool_sort_by: str = "volume_24h:desc"
    pool_page_size: int = 200
    pool_max_pages: int = 3                   # 拉取活跃池子宇宙的页数
    request_timeout: int = 20
    request_sleep_seconds: float = 0.4        # 逐池请求之间的间隔，避免被限流

    # ---- 候选名单与提醒 ----
    rsi_alert_cooldown_minutes: int = 360     # 同一个池超跌提醒的冷却时间
    rebound_alert_cooldown_minutes: int = 120 # 同一个池反弹提醒的冷却时间
    candidate_ttl_hours: int = 48             # 候选记录保留时间
    followup_horizons_minutes: tuple = (15, 60, 240)  # 信号后价格表现的回看时点
    max_alert_items: int = 8                  # 单条飞书消息最多列出多少个

    # ---- 运行 ----
    poll_interval_seconds: int = 300          # --loop 模式下的轮询间隔
    feishu_webhook: str = ""
    dry_run: bool = False

    def __post_init__(self):
        # 容错：环境变量/调用方可能传进来字符串
        self.min_pool_tvl_usd = float(self.min_pool_tvl_usd)
        self.min_volume_24h_usd = float(self.min_volume_24h_usd)
        self.min_volume_tvl_ratio = float(self.min_volume_tvl_ratio)
        self.min_recent_trades = int(self.min_recent_trades)
        self.min_market_cap_usd = float(self.min_market_cap_usd)
        self.max_tvl_drop_ratio = float(self.max_tvl_drop_ratio)
        self.rsi_period = max(2, int(self.rsi_period))
        self.rsi_1h_max = float(self.rsi_1h_max)
        self.rsi_top_n = max(1, int(self.rsi_top_n))
        self.rebound_lookback_bars = max(3, int(self.rebound_lookback_bars))
        self.rebound_stabilize_bars = max(1, int(self.rebound_stabilize_bars))
        self.rebound_volume_factor = float(self.rebound_volume_factor)
        self.pool_page_size = max(1, int(self.pool_page_size))
        self.pool_max_pages = max(1, int(self.pool_max_pages))
        self.request_timeout = max(1, int(self.request_timeout))
        self.request_sleep_seconds = max(0.0, float(self.request_sleep_seconds))
        self.rsi_alert_cooldown_minutes = max(0, int(self.rsi_alert_cooldown_minutes))
        self.rebound_alert_cooldown_minutes = max(0, int(self.rebound_alert_cooldown_minutes))
        self.candidate_ttl_hours = max(1, int(self.candidate_ttl_hours))
        self.followup_horizons_minutes = tuple(
            int(h) for h in self.followup_horizons_minutes if int(h) > 0
        ) or (15, 60, 240)
        self.max_alert_items = max(1, int(self.max_alert_items))
        self.poll_interval_seconds = max(30, int(self.poll_interval_seconds))
        self.data_api_base = str(self.data_api_base).rstrip("/")
        self.feishu_webhook = str(self.feishu_webhook or "")
        self.dry_run = bool(self.dry_run)

    @classmethod
    def from_env(cls) -> "RadarConfig":
        """从环境变量（含 .env）构造配置，未设置的字段用默认值。"""
        kwargs = {}
        for field in fields(cls):
            env_name = "RADAR_" + field.name.upper()
            if field.name == "feishu_webhook":
                env_name = "FEISHU_WEBHOOK"
            if field.name == "followup_horizons_minutes":
                kwargs[field.name] = _env_horizons(env_name, field.default)
            elif field.type in ("float", float):
                kwargs[field.name] = _env_float(env_name, field.default)
            elif field.type in ("int", int):
                kwargs[field.name] = _env_int(env_name, field.default)
            elif field.type in ("bool", bool):
                kwargs[field.name] = _env_bool(env_name, field.default)
            else:
                kwargs[field.name] = _env_str(env_name, field.default)
        return cls(**kwargs)

    @property
    def candidates_file(self) -> Path:
        return CANDIDATES_FILE


def load_config(**overrides) -> RadarConfig:
    """环境变量配置 + 显式覆盖，返回本轮使用的配置对象。"""
    cfg = RadarConfig.from_env()
    for key, value in (overrides or {}).items():
        if value is not None and hasattr(cfg, key):
            setattr(cfg, key, value)
    cfg.__post_init__()
    return cfg

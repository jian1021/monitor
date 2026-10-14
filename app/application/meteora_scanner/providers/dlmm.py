"""
DLMM 数据接口（官方）。

实测来源：Meteora 前端使用的池发现接口
  - 基础地址: https://pool-discovery-api.datapi.meteora.ag
  - 池列表:   GET /pools?page_size={<=1000}&category=all&filter_by=pool_type=dlmm[&after_key=...]
  - 单池:     GET /pools?page_size=1&category=all&filter_by=pool_address={address}
  - 分页：游标式（响应含 after_key / has_more），非页码式；page/offset 被忽略。
  - K 线： 单独来自 https://dlmm.datapi.meteora.ag/pools/{address}/ohlcv（本接口不提供）。

该接口一次性返回 Meteora 前端「Token Info」面板展示的全部字段：
  token_x / token_y: holders, has_freeze_authority, has_mint_authority, market_cap, fdv,
                     total_supply, price, created_at, top_holders_pct(前10持仓占比%),
                     dev_balance_pct(开发方持仓占比%)
  池: tvl, volume(24h), fee(24h), fee_tvl_ratio(% = fees/tvl*100), pool_price,
      pool_price_change_pct, fee_pct(基础费率%), open_positions(开仓人数),
      unique_lps, unique_traders, swap_count, dlmm_params.bin_step, pool_created_at

注意：字段 has_freeze_authority/has_mint_authority 为 true 表示“存在该授权”（风险），
本程序统一用 freeze_auth/mint_auth 表示“是否仍有该授权”，与筛选条件（默认期望 no）一致。
"""

from __future__ import annotations

from typing import Any, Optional

from ..api_client import HttpClient
from ..config import POOL_TYPE_DLMM
from ..models import PoolRecord, TokenInfo

DISCOVERY_SOURCE = "pool-discovery-api.datapi.meteora.ag"


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _token(t: Optional[dict]) -> TokenInfo:
    t = t or {}
    mcap = _num(t.get("market_cap"))
    fdv = _num(t.get("fdv"))
    if mcap is not None and mcap > 0:
        mcap_type = "market_cap"
    elif fdv is not None and fdv > 0:
        mcap_type = "fdv"
    else:
        mcap_type = ""
    freeze = t.get("has_freeze_authority")
    mint = t.get("has_mint_authority")
    return TokenInfo(
        mint=t.get("address") or "",
        name=t.get("name") or "",
        symbol=t.get("symbol") or "",
        market_cap=mcap,
        fdv=fdv,
        price=_num(t.get("price")),
        price_change_24h=None,
        market_cap_type=mcap_type,
        source=DISCOVERY_SOURCE,
        holders=_int(t.get("holders")),
        top10_holders=_num(t.get("top_holders_pct")),
        dev_holders=_num(t.get("dev_balance_pct")),
        freeze_auth=bool(freeze) if freeze is not None else None,
        mint_auth=bool(mint) if mint is not None else None,
    )


def fetch_pools_page(
    client: HttpClient,
    *,
    after_key: Optional[str] = None,
    page_size: int = 1000,
    pool_type: str = "dlmm",
) -> dict:
    """获取一页池列表（游标分页）。返回原始响应（含 data / total / after_key / has_more）。"""
    params: dict[str, Any] = {
        "page_size": page_size,
        "category": "all",
        "filter_by": f"pool_type={pool_type}",
    }
    if after_key:
        params["after_key"] = after_key
    return client.get_json("/pools", params=params)


def parse_pool(raw: dict) -> PoolRecord:
    """将池发现接口的原始池对象标准化为 PoolRecord。"""
    dlmm_params = raw.get("dlmm_params") or {}
    created = raw.get("pool_created_at")
    created_s: Optional[int] = None
    if created is not None:
        try:
            created_s = int(int(created) / 1000)  # 毫秒时间戳 -> 秒
        except (TypeError, ValueError):
            created_s = None

    return PoolRecord(
        pool_address=raw.get("pool_address") or "",
        pool_type=POOL_TYPE_DLMM,
        name=raw.get("name") or "",
        token_x=_token(raw.get("token_x")),
        token_y=_token(raw.get("token_y")),
        tvl=_num(raw.get("tvl")),
        volume_24h=_num(raw.get("volume")),
        fees_24h=_num(raw.get("fee")),
        fee_rate_pct=_num(raw.get("fee_pct")),
        fee_tvl_ratio_24h=_num(raw.get("fee_tvl_ratio")),
        price=_num(raw.get("pool_price")),
        price_change_24h=_num(raw.get("pool_price_change_pct")),
        apr=None,
        apy=None,
        bin_step=_int(dlmm_params.get("bin_step")),
        created_at=created_s,
        data_source=DISCOVERY_SOURCE,
        open_positions=_int(raw.get("open_positions")),
    )


def fetch_pool(client: HttpClient, pool_address: str) -> dict:
    """获取单个 DLMM 池的实时状态（字段与列表接口一致，用于刷新单个池）。"""
    resp = client.get_json(
        "/pools",
        params={"page_size": 1, "category": "all", "filter_by": f"pool_address={pool_address}"},
    )
    data = resp.get("data") or []
    return data[0] if data else {}


def fetch_ohlcv(
    client: HttpClient,
    pool_address: str,
    timeframe: str = "1h",
    start_time: Optional[int] = None,
    end_time: Optional[int] = None,
) -> list[dict]:
    """获取单池 K 线（timeframe 允许 5m/30m/1h/2h/4h/12h/24h）。

    注意：本函数必须传入以 DLMM_KLINE_BASE_URL（dlmm.datapi.meteora.ag）为基址的客户端。
    """
    params: dict[str, Any] = {"timeframe": timeframe}
    if start_time is not None:
        params["start_time"] = int(start_time)
    if end_time is not None:
        params["end_time"] = int(end_time)
    resp = client.get_json(f"/pools/{pool_address}/ohlcv", params=params)
    return resp.get("data") or []

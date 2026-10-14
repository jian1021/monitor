"""Meteora DLMM datapi 数据源：池子列表与池子元数据。

只做「取数 + 基础字段归一化」，不做任何筛选判断（筛选在 token_filter.py）。
接口文档：https://docs.meteora.ag/developer-guides/dlmm/api-reference/overview
"""

from __future__ import annotations

import time

import requests

# 计价币（quote）mint：用来从池子两边里挑出 meme 代币那一侧。
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMEdh6CkPauqoXNqcy2FWT8MFL5Sdf5N8V6etG5h"
QUOTE_MINTS = frozenset({SOL_MINT, USDC_MINT, USDT_MINT})

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}


def to_float(value, default: float = 0.0) -> float:
    """宽松转 float，NaN/None/非法值回退 default。"""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result == result else default  # 过滤 NaN


def pool_sides(pool) -> list[dict]:
    """返回池子两边的 token 元数据（过滤掉空/非 dict）。"""
    if not isinstance(pool, dict):
        return []
    return [
        side
        for side in (pool.get("token_x"), pool.get("token_y"))
        if isinstance(side, dict) and side
    ]


def is_meme_pool(pool) -> bool:
    """至少有一边不是 SOL/USDC/USDT 才算 meme 池（纯蓝筹对直接排除）。"""
    sides = pool_sides(pool)
    return any(str(s.get("address", "")) not in QUOTE_MINTS for s in sides)


def meme_side(pool) -> dict | None:
    """挑出代表这个池子的 meme 代币那一侧；两边都是计价币时返回 None。

    多非计价币（例如 meme-meme 池）时取市值较小的一边，与现有项目口径一致。
    """
    sides = pool_sides(pool)
    candidates = [s for s in sides if str(s.get("address", "")) not in QUOTE_MINTS]
    if not candidates:
        return None
    return min(candidates, key=lambda s: to_float(s.get("market_cap"), float("inf")))


def meme_mint(pool) -> str:
    side = meme_side(pool)
    return str(side.get("address", "")) if side else ""


def meme_symbol(pool) -> str:
    side = meme_side(pool)
    if side:
        return str(side.get("symbol") or side.get("name") or "")
    return ""


def meme_market_cap(pool) -> float:
    side = meme_side(pool)
    return to_float(side.get("market_cap")) if side else 0.0


def meme_usd_price(pool) -> float | None:
    """meme 代币的 USD 价格（token_* 元数据里 price 即 USD 计价）。"""
    side = meme_side(pool)
    if not side:
        return None
    price = to_float(side.get("price"), 0.0)
    return price if price > 0 else None


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(_HEADERS)
    return session


def fetch_pools(cfg, logger=None) -> list[dict]:
    """按成交量降序分页拉取活跃池子，作为第一层筛选的宇宙。

    失败时返回已拿到的部分，绝不抛异常打断整轮流程。
    """
    session = _session()
    pools: list[dict] = []
    seen: set[str] = set()
    for page in range(1, cfg.pool_max_pages + 1):
        try:
            resp = session.get(
                f"{cfg.data_api_base}/pools",
                params={
                    "page": page,
                    "page_size": cfg.pool_page_size,
                    "sort_by": cfg.pool_sort_by,
                },
                timeout=cfg.request_timeout,
            )
            if resp.status_code != 200:
                _warn(logger, f"拉取池子第 {page} 页响应异常: {resp.status_code}")
                break
            data = resp.json().get("data") or []
            if not data:
                break
            for pool in data:
                address = str((pool or {}).get("address") or "")
                if address and address not in seen:
                    seen.add(address)
                    pools.append(pool)
        except Exception as exc:  # noqa: BLE001 - 数据源异常不应中断整轮
            _warn(logger, f"拉取池子第 {page} 页失败: {exc}")
            break
    _info(logger, f"拉取活跃池子 {len(pools)} 个")
    return pools


def fetch_pool_detail(cfg, pool_address, logger=None) -> dict | None:
    """拉单个池子的最新元数据（含最新 TVL、价格、成交量、风险字段）。"""
    if not pool_address:
        return None
    try:
        resp = _session().get(
            f"{cfg.data_api_base}/pools/{pool_address}",
            timeout=cfg.request_timeout,
        )
        if resp.status_code != 200:
            _warn(logger, f"池子详情 {str(pool_address)[:8]} 响应异常: {resp.status_code}")
            return None
        data = resp.json()
        return data if isinstance(data, dict) and data.get("address") else None
    except Exception as exc:  # noqa: BLE001
        _warn(logger, f"池子详情 {str(pool_address)[:8]} 拉取失败: {exc}")
        return None


def sleep_between_requests(cfg):
    """逐池请求之间的节流。"""
    if getattr(cfg, "request_sleep_seconds", 0) > 0:
        time.sleep(cfg.request_sleep_seconds)


def _info(logger, message):
    if logger:
        logger.info(message)
    else:
        print(message)


def _warn(logger, message):
    if logger:
        logger.warning(message)
    else:
        print(message)

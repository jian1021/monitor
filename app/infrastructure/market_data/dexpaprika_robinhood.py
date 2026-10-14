"""DexPaprika Robinhood Chain Uniswap pool discovery."""

from __future__ import annotations

import os
import time
import uuid
from functools import lru_cache
from typing import Any

import requests

from app.core import config as core_config  # noqa: F401 - loads repository .env

BASE_URL = os.getenv("DEXPAPRIKA_BASE_URL", "https://api.dexpaprika.com").rstrip("/")
DEXES = {
    "uniswap_v2": "Uniswap V2",
    "uniswap_v3": "Uniswap V3",
    "uniswap_v4": "Uniswap V4",
}
GMGN_API_BASE = "https://openapi.gmgn.ai"


def _clean_secret(value: str) -> str:
    """Remove common quote wrappers from manually edited .env values."""
    value = value.strip()
    quote_pairs = (("\"", "\""), ("'", "'"), ("“", "”"), ("‘", "’"))
    for left, right in quote_pairs:
        if value.startswith(left) and value.endswith(right):
            return value[len(left):-len(right)].strip()
    return value


def gmgn_api_key() -> str:
    return _clean_secret(core_config.GMGN_API_KEY or os.getenv("GMGN_API_KEY", ""))


def _as_honeypot_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    normalized = str(value).strip().lower() if value is not None else ""
    if normalized in {"yes", "true", "1"}:
        return True
    if normalized in {"no", "false", "0"}:
        return False
    return None


@lru_cache(maxsize=2048)
def _gmgn_token_security_cached(
    api_key: str, address: str, _cache_minute: int,
) -> dict[str, Any]:
    """Fetch GMGN's read-only Robinhood token security result (short-lived process cache)."""
    params = {
        "chain": "robinhood",
        "address": address,
        "timestamp": int(time.time()),
        "client_id": str(uuid.uuid4()),
    }
    try:
        response = requests.get(
            f"{GMGN_API_BASE}/v1/token/security",
            params=params,
            headers={
                "X-APIKEY": api_key,
                "Content-Type": "application/json",
                "User-Agent": "monitor-robinhood-pool-scanner/1.0",
            },
            timeout=15,
        )
        if response.status_code == 429:
            return {"status": "unknown", "reason": "GMGN 请求频率受限"}
        response.raise_for_status()
        payload = response.json()
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else "未知"
        return {"status": "unknown", "reason": f"GMGN HTTP {status_code}"}
    except (requests.RequestException, ValueError) as exc:
        return {"status": "unknown", "reason": f"GMGN 查询失败（{type(exc).__name__}）"}

    if not isinstance(payload, dict) or payload.get("code") != 0:
        return {"status": "unknown", "reason": "GMGN 返回错误或无权限"}
    data = payload.get("data")
    if not isinstance(data, dict):
        return {"status": "unknown", "reason": "GMGN 未返回安全数据"}
    honeypot = _as_honeypot_value(data.get("is_honeypot"))
    if honeypot is None:
        return {"status": "unknown", "reason": "GMGN 未提供貔貅结论"}
    return {
        "status": "honeypot" if honeypot else "clear",
        "reason": "GMGN 标记为貔貅" if honeypot else "GMGN 未标记为貔貅",
    }


def token_address(token: dict[str, Any]) -> str | None:
    """Read a token contract address from known DexPaprika response fields."""
    for field in ("address", "token_address", "contract_address", "id"):
        value = token.get(field)
        if isinstance(value, str) and value.startswith("0x") and len(value) == 42:
            return value.lower()
    return None


def gmgn_honeypot_check(address: str) -> dict[str, Any]:
    key = gmgn_api_key()
    if not key:
        return {"status": "unknown", "reason": "未配置 GMGN_API_KEY"}
    if not isinstance(address, str) or not address.startswith("0x") or len(address) != 42:
        return {"status": "unknown", "reason": "代币合约地址格式无效"}
    return _gmgn_token_security_cached(key, address.lower(), int(time.time() // 60))


def basic_token_trade_risk(
    pool: dict[str, Any], *, min_sells_per_token: int = 5,
    max_buy_sell_ratio: float = 20,
) -> dict[str, Any]:
    """Flag buy-only or highly one-sided token activity as a basic risk signal.

    DexPaprika's detailed pool result includes per-token 24h buy/sell counts.
    This is a heuristic only; it is not a contract simulation or honeypot proof.
    """
    tokens = pool.get("tokens")
    if not isinstance(tokens, list) or len(tokens) < 2:
        return {"passed": False, "min_sells": None, "max_ratio": None,
                "reason": "代币买卖数据缺失"}

    sell_counts: list[int] = []
    ratios: list[float] = []
    problems: list[str] = []
    for token in tokens:
        daily = token.get("24h") if isinstance(token, dict) else None
        if not isinstance(daily, dict) or daily.get("sells") is None or daily.get("buys") is None:
            problems.append("代币 24h 买卖数据缺失")
            continue
        try:
            sells = int(float(daily["sells"]))
            buys = int(float(daily["buys"]))
        except (TypeError, ValueError, OverflowError):
            problems.append("代币 24h 买卖数据格式异常")
            continue
        if sells < 0 or buys < 0:
            problems.append("代币 24h 买卖数据无效")
            continue
        sell_counts.append(sells)
        ratios.append(float("inf") if sells == 0 and buys > 0 else buys / max(sells, 1))
        if sells < min_sells_per_token:
            symbol = str(token.get("symbol") or token.get("name") or "代币")
            problems.append(f"{symbol} 24h 卖出仅 {sells} 笔（低于 {min_sells_per_token}）")

    if len(sell_counts) != len(tokens):
        problems.append("至少一个代币的卖出数据不可验证")
    max_ratio = max(ratios) if ratios else None
    if max_ratio is not None and max_ratio > max_buy_sell_ratio:
        problems.append(f"买卖笔数比最高 {max_ratio:.1f}（超过 {max_buy_sell_ratio:.1f}）")

    return {
        "passed": not problems,
        "min_sells": min(sell_counts) if len(sell_counts) == len(tokens) else None,
        "max_ratio": max_ratio,
        "reason": "；".join(dict.fromkeys(problems)),
    }


def pool_fee_rate(pool: dict[str, Any]) -> float | None:
    """Read a pool fee tier as a fraction (``0.01`` means 1%).

    DexPaprika reports e.g. ``0.01`` / ``0.05`` on V3 pools and ``None``
    on pools (notably V2) without a reported fee. Returns ``None`` when
    missing or unparsable so callers treat it as unknown instead of 0.
    """
    fee = pool.get("fee") if isinstance(pool, dict) else None
    if fee is None:
        return None
    try:
        if isinstance(fee, str):
            text = fee.strip()
            is_percent = text.endswith("%")
            value = float(text.rstrip("%").strip())
            if is_percent:
                value /= 100.0
        else:
            value = float(fee)
    except (TypeError, ValueError, OverflowError):
        return None
    if value < 0:
        return None
    if value > 1:  # defensive: treat 1-100 style input as percent
        value /= 100.0
    return value


def pool_fdv_stats(pool: dict[str, Any]) -> tuple[float | None, float | None]:
    """Return (min_token_fdv, max_token_fdv) across a pool's tokens.

    ``min`` is ``None`` when any token lacks a usable ``fdv`` so a threshold
    check marks the pool unverifiable instead of guessing.
    """
    tokens = pool.get("tokens") if isinstance(pool, dict) else None
    if not isinstance(tokens, list) or not tokens:
        return None, None
    fdvs: list[float] = []
    for token in tokens:
        if not isinstance(token, dict):
            return None, None
        try:
            value = float(token.get("fdv"))
        except (TypeError, ValueError, OverflowError):
            return None, None
        if value < 0:
            return None, None
        fdvs.append(value)
    return min(fdvs), max(fdvs)


def apply_pool_thresholds(
    pool: dict[str, Any], *, min_token_fdv: float = 0,
    min_fee_rate: float = 0,
) -> dict[str, Any]:
    """Client-side gates on token FDV (market-cap proxy) and pool fee rate.

    DexPaprika's pool search API only filters server-side on volume /
    liquidity / txns, so FDV and fee are enforced here after fetching.
    Missing data fails an enabled gate (it must not silently pass).
    """
    reasons: list[str] = []
    min_fdv, max_fdv = pool_fdv_stats(pool)
    fee_rate = pool_fee_rate(pool)
    if min_token_fdv > 0:
        if min_fdv is None:
            reasons.append("代币市值（FDV）缺失")
        elif min_fdv < min_token_fdv:
            reasons.append(
                f"最小代币市值 ${min_fdv:,.0f} 低于 ${min_token_fdv:,.0f}")
    if min_fee_rate > 0:
        if fee_rate is None:
            reasons.append("池费率缺失")
        elif fee_rate < min_fee_rate:
            reasons.append(
                f"池费率 {fee_rate:.2%} 低于 {min_fee_rate:.2%}")
    return {
        "passed": not reasons,
        "min_fdv": min_fdv,
        "max_fdv": max_fdv,
        "fee_rate": fee_rate,
        "reason": "；".join(reasons),
    }


def search_uniswap_pools(
    *,
    dex_id: str | None = None,
    min_volume_24h: float = 0,
    min_liquidity: float = 0,
    min_transactions_24h: int = 0,
    max_results: int = 300,
) -> dict[str, Any]:
    """Search Robinhood Uniswap pools via DexPaprika's indexed pool API.

    Pagination is capped by ``max_results`` to keep requests and UI memory
    bounded. Each DEX is queried independently because the API accepts one
    ``dex_name`` identifier per request.
    """
    if dex_id is not None and dex_id not in DEXES:
        return {"ok": False, "rows": [], "reason": "不支持的 Uniswap DEX。"}

    max_results = min(600, max(1, int(max_results)))
    selected_dexes = [dex_id] if dex_id else list(DEXES)
    headers = {"User-Agent": "monitor-robinhood-pool-scanner/1.0"}
    api_key = os.getenv("DEXPAPRIKA_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = api_key

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    per_dex_limit = max_results if dex_id else (max_results + len(selected_dexes) - 1) // len(selected_dexes)
    pages_per_dex = max(1, (per_dex_limit + 99) // 100)
    for current_dex in selected_dexes:
        cursor = None
        dex_rows: list[dict[str, Any]] = []
        for _ in range(pages_per_dex):
            params: dict[str, Any] = {
                "dex_name": current_dex,
                "order_by": "volume_usd_24h",
                "sort": "desc",
                "limit": min(100, per_dex_limit - len(dex_rows)),
                "detailed": "true",
            }
            if min_volume_24h > 0:
                params["volume_usd_24h_min"] = min_volume_24h
            if min_liquidity > 0:
                params["liquidity_usd_min"] = min_liquidity
            if min_transactions_24h > 0:
                params["txns_24h_min"] = min_transactions_24h
            if cursor:
                params["cursor"] = cursor

            try:
                response = requests.get(
                    f"{BASE_URL}/networks/robinhood/pools/search",
                    params=params,
                    headers=headers,
                    timeout=25,
                )
                if response.status_code != 200:
                    errors.append(f"{DEXES[current_dex]} HTTP {response.status_code}")
                    break
                payload = response.json()
            except (requests.RequestException, ValueError) as exc:
                errors.append(f"{DEXES[current_dex]} 请求失败：{type(exc).__name__}: {exc}")
                break

            page_rows = payload.get("results") if isinstance(payload, dict) else None
            if not isinstance(page_rows, list):
                errors.append(f"{DEXES[current_dex]} 返回格式异常")
                break
            dex_rows.extend(row for row in page_rows if isinstance(row, dict))
            if len(dex_rows) >= per_dex_limit or not payload.get("has_next_page"):
                break
            cursor = payload.get("next_cursor")
            if not cursor:
                break
        rows.extend(dex_rows[:per_dex_limit])

    rows.sort(
        key=lambda row: float(row.get("volume_usd_24h") or 0), reverse=True,
    )
    return {
        "ok": bool(rows) or not errors,
        "rows": rows[:max_results],
        "reason": "; ".join(errors),
        "dexes": selected_dexes,
    }

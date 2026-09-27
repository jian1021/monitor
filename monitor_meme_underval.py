import time
from datetime import datetime, timezone

import requests

from config import FEISHU_WEBHOOK
from db import filter_unpushed, mark_pushed
from send_feishu_msg import send_feishu_msg

DATAPI_BASE = "https://dlmm.datapi.meteora.ag"

SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMEdh6CkPauqoXNqcy2FWT8MFL5Sdf5N8V6etG5h"
QUOTE_MINTS = frozenset({SOL_MINT, USDC_MINT, USDT_MINT})

DEFAULT_MIN_MARKET_CAP = 1_000_000.0
DEFAULT_MIN_TVL_USD = 100_000.0
DEFAULT_MIN_BIN_STEP = 100
DEFAULT_MIN_BASE_FEE_PCT = 2.0
DEFAULT_RSI_PERIOD = 3
DEFAULT_RSI_MAX = 10.0
DEFAULT_MAX_PAGES = 2
DEFAULT_PAGE_SIZE = 500
DEFAULT_RSI_TOP_N = 30

MODULE_NAME = "meme_underval"


def _to_float(value, default=0.0):
    try:
        result = float(value)
        return result if result == result else default
    except (TypeError, ValueError):
        return default


def _sides(pool):
    return [pool.get("token_x") or {}, pool.get("token_y") or {}]


def meme_market_cap(pool):
    sides = [s for s in _sides(pool) if isinstance(s, dict)]
    if not sides:
        return 0.0
    candidates = [s for s in sides if str(s.get("address", "")) not in QUOTE_MINTS]
    if not candidates:
        candidates = sides
    return min(_to_float(s.get("market_cap")) for s in candidates)


def meme_mint(pool):
    sides = [s for s in _sides(pool) if isinstance(s, dict)]
    if not sides:
        return ""
    candidates = [s for s in sides if str(s.get("address", "")) not in QUOTE_MINTS]
    if not candidates:
        candidates = sides
    best = min(candidates, key=lambda s: _to_float(s.get("market_cap"), float("inf")))
    return str(best.get("address", ""))


def fee_ratio_24h(pool):
    return _to_float((pool.get("fee_tvl_ratio") or {}).get("24h"))


def dedupe_by_meme_mint(pools):
    best = {}
    for pool in (pools or []):
        if not isinstance(pool, dict):
            continue
        key = meme_mint(pool)
        if not key:
            continue
        if key not in best or _to_float(pool.get("tvl")) > _to_float(best[key].get("tvl")):
            best[key] = pool
    return list(best.values())


def pool_bin_step(pool):
    return int(_to_float((pool.get("pool_config") or {}).get("bin_step")))


def base_fee_pct(pool):
    return _to_float((pool.get("pool_config") or {}).get("base_fee_pct"))


def pool_age_hours(pool, now=None):
    created_ms = _to_float(pool.get("created_at"))
    if created_ms <= 0:
        return None
    base = now if now is not None else time.time()
    return (base - created_ms / 1000.0) / 3600.0


def pool_passes_prefilter(pool, min_market_cap=DEFAULT_MIN_MARKET_CAP,
                           min_bin_step=DEFAULT_MIN_BIN_STEP,
                           min_base_fee_pct=DEFAULT_MIN_BASE_FEE_PCT,
                           min_tvl_usd=DEFAULT_MIN_TVL_USD):
    if not isinstance(pool, dict) or pool.get("is_blacklisted"):
        return False
    return (meme_market_cap(pool) >= min_market_cap
            and pool_bin_step(pool) >= min_bin_step
            and base_fee_pct(pool) >= min_base_fee_pct
            and _to_float(pool.get("tvl")) >= min_tvl_usd)


def fetch_top_performers(page_size=DEFAULT_PAGE_SIZE, max_pages=DEFAULT_MAX_PAGES,
                         timeout=20):
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/125.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
    })
    pools = []
    seen = set()
    for page in range(1, max(1, int(max_pages)) + 1):
        try:
            resp = session.get(
                f"{DATAPI_BASE}/pools",
                params={"page": page, "page_size": page_size,
                        "sort_by": "fee_tvl_ratio_24h:desc"},
                timeout=timeout,
            )
            if resp.status_code != 200:
                print(f"Top Performers 第 {page} 页响应异常: {resp.status_code}")
                break
            data = resp.json().get("data") or []
            if not data:
                break
            for p in data:
                addr = (p.get("address") or "") if isinstance(p, dict) else ""
                if addr and addr not in seen:
                    seen.add(addr)
                    pools.append(p)
        except Exception as e:
            print(f"Top Performers 第 {page} 页拉取失败: {e}")
            break
    print(f"Top Performers 共拉取 {len(pools)} 个池子")
    return pools


def scan_undervalued(pools, min_market_cap=DEFAULT_MIN_MARKET_CAP,
                     min_bin_step=DEFAULT_MIN_BIN_STEP,
                     min_base_fee_pct=DEFAULT_MIN_BASE_FEE_PCT,
                     rsi_period=DEFAULT_RSI_PERIOD, rsi_max=DEFAULT_RSI_MAX,
                     rsi_top_n=DEFAULT_RSI_TOP_N, rsi_fetcher=None,
                     min_tvl_usd=DEFAULT_MIN_TVL_USD, on_progress=None):
    unique = dedupe_by_meme_mint(pools)
    prefiltered = [p for p in unique
                   if pool_passes_prefilter(p, min_market_cap, min_bin_step,
                                            min_base_fee_pct, min_tvl_usd)]
    prefiltered.sort(key=fee_ratio_24h, reverse=True)
    candidates = prefiltered[:max(0, int(rsi_top_n))]

    if rsi_fetcher is None:
        from monitor_rsi import get_meteora_rsi

        def _default_fetcher(pool_address, timeframe="hour", aggregate=1, length=3):
            return get_meteora_rsi(pool_address, timeframe, aggregate, length)

        rsi_fetcher = _default_fetcher

    hits = []
    total = len(candidates)
    for index, pool in enumerate(candidates, start=1):
        symbol = str(pool.get("name") or pool.get("address") or "")[:20]
        if on_progress is not None:
            try:
                on_progress(index, total, symbol)
            except Exception:
                pass
        pool_address = str(pool.get("address") or "")
        if not pool_address:
            continue
        try:
            rsi, _price = rsi_fetcher(pool_address, "hour", 1, int(rsi_period))
        except Exception as e:
            print(f"RSI 计算失败 [{pool_address[:10]}...]: {e}")
            continue
        if rsi is None or float(rsi) > float(rsi_max):
            continue
        hits.append({
            "symbol": str(pool.get("name") or pool_address[:10]),
            "meme_mint": meme_mint(pool),
            "pool_address": pool_address,
            "market_cap": meme_market_cap(pool),
            "tvl": _to_float(pool.get("tvl")),
            "fees_24h_usd": _to_float((pool.get("fees") or {}).get("24h")),
            "volume_24h_usd": _to_float((pool.get("volume") or {}).get("24h")),
            "fee_ratio_24h": fee_ratio_24h(pool),
            "base_fee_pct": base_fee_pct(pool),
            "bin_step": pool_bin_step(pool),
            "rsi": round(float(rsi), 2),
            "age_hours": pool_age_hours(pool),
        })
        time.sleep(0.8)
    return hits


def build_push_message(hits):
    lines = [f"📌 低估命中: {len(hits)} 个 (RSI(3,1h)≤{DEFAULT_RSI_MAX})"]
    for hit in hits[:5]:
        age = f"{hit['age_hours']:.1f}h" if hit.get("age_hours") is not None else "未知"
        lines.append(
            f"• {hit['symbol']} | 市值:${hit['market_cap']:,.0f} | "
            f"TVL:${hit['tvl']:,.0f} | 基础费:{hit['base_fee_pct']:.2f}% | "
            f"24h费/TVL:{hit['fee_ratio_24h']:.2f}% | "
            f"RSI:{hit['rsi']:.1f} | 池龄:{age}\n"
            f"  🔗 GMGN: https://gmgn.ai/sol/token/{hit['meme_mint']}\n"
            f"  🌊 Meteora: https://app.meteora.ag/dlmm/{hit['pool_address']}"
        )
    if len(hits) > 5:
        lines.append(f"（消息仅列出前 5 个）")
    return "\n".join(lines)


def run_meme_underval_monitor(params=None):
    cfg = dict(params or {})
    min_market_cap = float(cfg.get("min_market_cap", DEFAULT_MIN_MARKET_CAP))
    min_tvl_usd = float(cfg.get("min_tvl_usd", DEFAULT_MIN_TVL_USD))
    min_bin_step = int(cfg.get("min_bin_step", DEFAULT_MIN_BIN_STEP))
    min_base_fee = float(cfg.get("min_base_fee_pct", DEFAULT_MIN_BASE_FEE_PCT))
    rsi_period = int(cfg.get("rsi_period", DEFAULT_RSI_PERIOD))
    rsi_max = float(cfg.get("rsi_max", DEFAULT_RSI_MAX))

    print("🚀 [Meme 低估监控引擎] 正在拉取 Meteora Top Performers...")
    pools = fetch_top_performers(
        page_size=int(cfg.get("page_size", DEFAULT_PAGE_SIZE)),
        max_pages=int(cfg.get("max_pages", DEFAULT_MAX_PAGES)),
    )
    if not pools:
        print("⚠️ 未获取到 Top Performers 池子数据。")
        return []

    hits = scan_undervalued(
        pools, min_market_cap=min_market_cap, min_tvl_usd=min_tvl_usd,
        min_bin_step=min_bin_step, min_base_fee_pct=min_base_fee,
        rsi_period=rsi_period, rsi_max=rsi_max,
        rsi_top_n=int(cfg.get("rsi_top_n", DEFAULT_RSI_TOP_N)),
    )
    if not hits:
        print("✨ 本轮无满足低估阀值的新标的。")
        return []

    fresh = set(filter_unpushed(MODULE_NAME, [h["meme_mint"] for h in hits]))
    hits = [h for h in hits if h["meme_mint"] in fresh]
    if not hits:
        print("ℹ️ 命中标的均在 24h 内推送过，本轮跳过。")
        return []

    text = "🚀【Meme 低估监控告警】\n========================================\n\n" + build_push_message(hits)
    send_feishu_msg(FEISHU_WEBHOOK, text)
    mark_pushed(MODULE_NAME, [h["meme_mint"] for h in hits])
    print(f"🎉 监控完毕，命中 {len(hits)} 个低估标的并推送。")
    return hits


if __name__ == "__main__":
    run_meme_underval_monitor()

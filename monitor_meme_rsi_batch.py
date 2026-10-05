"""批量 meme 池子超卖超买监控：RSI(3,5m) 越界(>90 或 <10) 推送飞书。"""

import re
import time

import requests

from config import FEISHU_WEBHOOK
from db import (
    get_meme_rsi_params,
    list_meme_watchlist_pools,
    update_meme_pool_rsi,
)
from send_feishu_msg import send_feishu_msg

MODULE_NAME = "meme_rsi_batch"
RSI_PERIOD = 3

_POOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def parse_pool_addresses(text):
    """把粘贴文本解析成 (合法地址列表, 非法片段列表)：按空白/逗号/分号切分并保序去重。"""
    tokens = re.split(r"[\s,;，；]+", str(text or ""))
    valid, invalid, seen = [], [], set()
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        if _POOL_RE.match(token):
            if token not in seen:
                seen.add(token)
                valid.append(token)
        else:
            invalid.append(token)
    return valid, invalid


def fetch_pool_name(address) -> str:
    """尽力从 Meteora datapi 取池子名用于展示，失败返回空串。"""
    try:
        res = requests.get(
            f"https://dlmm.datapi.meteora.ag/pools/{address}",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10,
        ).json()
        return str(res.get("name") or "") if isinstance(res, dict) else ""
    except Exception:
        return ""


def _default_rsi_fetcher(address, length):
    from monitor_rsi import get_meteora_native_rsi

    return get_meteora_native_rsi(address, "5m", 1, length)


def scan_watchlist(pools=None, rsi_low=10.0, rsi_high=90.0, period=RSI_PERIOD,
                   fetcher=None, on_progress=None):
    """逐池计算 RSI，回写 last_rsi/last_price，返回越界命中列表。"""
    if pools is None:
        pools = list_meme_watchlist_pools()
    if fetcher is None:
        fetcher = _default_rsi_fetcher

    enabled = [p for p in pools if p.get("enabled", True)]
    hits = []
    total = len(enabled)
    for index, pool in enumerate(enabled, start=1):
        address = str(pool.get("address") or "")
        symbol = str(pool.get("symbol") or "") or address[:8]
        if on_progress is not None:
            try:
                on_progress(index, total, symbol)
            except Exception:
                pass
        if not address:
            continue
        try:
            rsi, price = fetcher(address, int(period))
        except Exception as e:
            print(f"RSI 计算失败 [{address[:10]}...]: {e}")
            continue
        if rsi is None:
            continue
        rsi_value = float(rsi)
        price_value = None if price is None else float(price)
        update_meme_pool_rsi(address, rsi_value, price_value)
        if rsi_value < float(rsi_low) or rsi_value > float(rsi_high):
            hits.append({
                "address": address,
                "symbol": symbol,
                "rsi": round(rsi_value, 2),
                "price": price_value,
                "side": "超卖" if rsi_value < float(rsi_low) else "超买",
            })
        time.sleep(0.8)
    return hits


def build_alert_message(hits, rsi_low=10.0, rsi_high=90.0, period=RSI_PERIOD):
    lines = [
        f"📡【批量 Meme 超卖超买告警】命中 {len(hits)} 个 "
        f"(RSI({int(period)},5m) >{float(rsi_high):g} 或 <{float(rsi_low):g})"
    ]
    for hit in hits:
        price = "未知" if hit.get("price") is None else f"${hit['price']:.6g}"
        emoji = "🚨" if hit["side"] == "超卖" else "⚠️"
        lines.append(
            f"{emoji} [{hit['side']}] {hit['symbol']} | RSI: {hit['rsi']:.1f} | 现价: {price}\n"
            f"  🌊 Meteora: https://app.meteora.ag/dlmm/{hit['address']}"
        )
    return "\n".join(lines)


def run_batch_meme_rsi_monitor():
    """常驻调度入口：扫描监控列表，越界则合并成一条飞书消息推送。"""
    params = get_meme_rsi_params()
    rsi_low = float(params.get("rsi_low", 10))
    rsi_high = float(params.get("rsi_high", 90))

    pools = list_meme_watchlist_pools()
    if not pools:
        print("ℹ️ [批量 Meme RSI] 监控列表为空，本轮跳过。")
        return []

    hits = scan_watchlist(pools, rsi_low=rsi_low, rsi_high=rsi_high, period=RSI_PERIOD)
    if not hits:
        print("✨ [批量 Meme RSI] 本轮无越界标的。")
        return []

    text = ("🚀【批量 Meme 超卖超买监控】\n" + "=" * 40 + "\n\n"
            + build_alert_message(hits, rsi_low=rsi_low, rsi_high=rsi_high, period=RSI_PERIOD))
    send_feishu_msg(FEISHU_WEBHOOK, text)
    print(f"🎉 [批量 Meme RSI] 推送 {len(hits)} 个越界标的。")
    return hits


if __name__ == "__main__":
    run_batch_meme_rsi_monitor()

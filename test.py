import time
import requests
from datetime import datetime
from config import FEISHU_WEBHOOK
from send_feishu_msg import send_feishu_msg

DEXSCREENER_BASE = "https://api.dexscreener.com"
GECKO_BASE = "https://api.geckoterminal.com/api/v2"
CHAIN = "robinhood"
SCAN_INTERVAL = 60          # 扫描间隔（秒）
MAX_AGE_HOURS = 5.0
MIN_MC = 1_000_000
MIN_TVL = 100_000

# 已推送过的地址，防止重复报警
pushed_addresses = set()


def safe_get(url, timeout=15):
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        else:
            print(f"⚠️ 请求失败 {resp.status_code}: {url}")
            return None
    except Exception as e:
        print(f"⚠️ 请求异常: {e}")
        return None


def parse_created_at(value):
    """统一把各种创建时间转成秒级时间戳"""
    if not value:
        return 0
    try:
        # 数字（毫秒或秒）
        if isinstance(value, (int, float)):
            ts = float(value)
            if ts > 1e12:  # 毫秒
                ts /= 1000
            return ts
        # 字符串 ISO 格式
        if isinstance(value, str):
            # 处理带 Z 或时区的情况
            value = value.replace("Z", "+00:00")
            dt = datetime.fromisoformat(value)
            return dt.timestamp()
    except Exception:
        pass
    return 0


# ================= 1. GeckoTerminal 新池 =================
def fetch_from_gecko():
    print("🌐 [GeckoTerminal] 正在获取新池...")
    all_pairs = []

    for page in range(1, 6):  # 拉 5 页
        url = f"{GECKO_BASE}/networks/robinhood/new_pools?page={page}"
        data = safe_get(url)
        if not data or "data" not in data:
            break

        for pool in data["data"]:
            attrs = pool.get("attributes", {})
            address = attrs.get("base_token_address") or ""
            if not address:
                continue

            pair = {
                "source": "gecko",
                "chainId": "robinhood",
                "pairAddress": attrs.get("address"),
                "baseToken": {
                    "address": address.lower(),
                    "symbol": (attrs.get("name") or "UNKNOWN").split("/")[0].strip(),
                    "name": attrs.get("name") or "UNKNOWN",
                },
                "priceUsd": float(attrs.get("base_token_price_usd") or 0),
                "marketCap": float(attrs.get("market_cap_usd") or attrs.get("fdv_usd") or 0),
                "fdv": float(attrs.get("fdv_usd") or 0),
                "liquidity": {"usd": float(attrs.get("reserve_in_usd") or 0)},
                "volume": {
                    "h24": float(
                        attrs.get("volume_usd", {}).get("h24", 0)
                        if isinstance(attrs.get("volume_usd"), dict)
                        else 0
                    )
                },
                "priceChange": {
                    "h24": float(
                        attrs.get("price_change_percentage", {}).get("h24", 0)
                        if isinstance(attrs.get("price_change_percentage"), dict)
                        else 0
                    )
                },
                "pairCreatedAt": attrs.get("pool_created_at"),
                "url": f"https://www.geckoterminal.com/robinhood/pools/{attrs.get('address')}",
            }
            all_pairs.append(pair)

        time.sleep(0.4)  # 礼貌限速

    print(f"✅ GeckoTerminal 获取到 {len(all_pairs)} 个池")
    return all_pairs


# ================= 2. DexScreener Boost + Profiles =================
def fetch_from_dexscreener():
    print("🌐 [DexScreener] 正在获取 Boost + Profiles...")
    all_addresses = set()

    # Boost
    boosts = safe_get(f"{DEXSCREENER_BASE}/token-boosts/latest/v1")
    if boosts:
        for t in boosts:
            if t.get("chainId") == CHAIN and t.get("tokenAddress"):
                all_addresses.add(t["tokenAddress"].lower())

    # Profiles
    profiles = safe_get(f"{DEXSCREENER_BASE}/token-profiles/latest/v1")
    if profiles:
        for t in profiles:
            if t.get("chainId") == CHAIN and t.get("tokenAddress"):
                all_addresses.add(t["tokenAddress"].lower())

    if not all_addresses:
        print("⚠️ DexScreener 未发现代币")
        return []

    print(f"📋 DexScreener 发现 {len(all_addresses)} 个代币，拉取交易对...")

    all_pairs = []
    addrs = list(all_addresses)

    for i in range(0, len(addrs), 30):
        batch = addrs[i : i + 30]
        data = safe_get(f"{DEXSCREENER_BASE}/latest/dex/tokens/{','.join(batch)}")
        if data and "pairs" in data:
            for p in data["pairs"]:
                if p.get("chainId") == CHAIN:
                    p["source"] = "dexscreener"
                    all_pairs.append(p)

    print(f"✅ DexScreener 获取到 {len(all_pairs)} 个交易对")
    return all_pairs


# ================= 3. 合并数据 =================
def fetch_all_pairs():
    gecko_pairs = fetch_from_gecko()
    dex_pairs = fetch_from_dexscreener()

    # 用 token address 去重（优先保留 Gecko 的数据，因为有更准确的创建时间）
    unique = {}
    for p in gecko_pairs + dex_pairs:
        addr = p.get("baseToken", {}).get("address", "").lower()
        if addr and addr not in unique:
            unique[addr] = p

    result = list(unique.values())
    print(f"🔄 合并去重后共 {len(result)} 个代币\n")
    return result


# ================= 4. 策略过滤 =================
def filter_tokens(raw_pairs):
    now = time.time()
    matched = []

    print(f"🔍 策略过滤 (MC ≥ ${MIN_MC:,} | TVL ≥ ${MIN_TVL:,} | 时长 ≤ {MAX_AGE_HOURS}h)\n")

    for p in raw_pairs:
        base = p.get("baseToken", {})
        address = (base.get("address") or "").lower()
        if not address or address in pushed_addresses:
            continue

        symbol = base.get("symbol", "UNKNOWN")
        name = base.get("name", symbol)

        mc = float(p.get("marketCap") or p.get("fdv") or 0)
        liquidity = p.get("liquidity", {})
        tvl = float(liquidity.get("usd", 0) if isinstance(liquidity, dict) else 0)
        price_usd = float(p.get("priceUsd") or 0)
        volume = p.get("volume", {})
        vol_24h = float(volume.get("h24", 0) if isinstance(volume, dict) else 0)
        change = p.get("priceChange", {})
        change_24h = float(change.get("h24", 0) if isinstance(change, dict) else 0)

        created_at = parse_created_at(p.get("pairCreatedAt"))
        age_hours = (now - created_at) / 3600 if created_at > 0 else 999

        short_addr = address[-6:]
        print(
            f"  [扫描] {symbol:<10} (..{short_addr}) | "
            f"MC: ${mc:>10,.0f} | TVL: ${tvl:>8,.0f} | "
            f"时长: {age_hours:>5.1f}h | 来源: {p.get('source', 'unknown')}"
        )

        if mc >= MIN_MC and tvl >= MIN_TVL and age_hours <= MAX_AGE_HOURS:
            matched.append({
                "address": address,
                "symbol": symbol,
                "name": name,
                "price_usd": price_usd,
                "market_cap": mc,
                "tvl": tvl,
                "vol_24h": vol_24h,
                "change_24h": round(change_24h, 2),
                "age_hours": round(age_hours, 1),
                "dex_url": p.get("url") or f"https://dexscreener.com/robinhood/{address}",
                "source": p.get("source", "unknown"),
            })

    return matched


# ================= 5. 主循环 =================
def run_monitor():
    print("🚀 [Robinhood 全链监控] 启动")
    print(f"   数据源: GeckoTerminal 新池 + DexScreener Boost/Profiles")
    print(f"   条件: MC≥{MIN_MC:,} & TVL≥{MIN_TVL:,} & ≤{MAX_AGE_HOURS}h")
    print(f"   扫描间隔: {SCAN_INTERVAL}s\n")

    while True:
        try:
            raw_pairs = fetch_all_pairs()
            if not raw_pairs:
                print("⚠️ 本次未获取到数据")
            else:
                hits = filter_tokens(raw_pairs)

                if hits:
                    print(f"\n🎯 命中 {len(hits)} 个代币！推送飞书...")
                    msg_lines = []
                    for h in hits:
                        pushed_addresses.add(h["address"])
                        short = h["address"][-6:]
                        msg_lines.append(
                            f"• {h['symbol']} ({h['name']}) | ..{short}\n"
                            f"  价格: ${h['price_usd']:,.8f}\n"
                            f"  MC: ${h['market_cap']:,.0f} | TVL: ${h['tvl']:,.0f}\n"
                            f"  24h: {h['change_24h']:+.1f}% | 开盘: {h['age_hours']}h 前\n"
                            f"  来源: {h['source']}\n"
                            f"  链接: {h['dex_url']}"
                        )

                    final_text = (
                        f"🏹【Robinhood 精选新盘告警】命中 {len(hits)} 个\n"
                        f"{'='*40}\n\n"
                        + "\n\n" + "-"*30 + "\n\n".join(msg_lines)
                    )
                    send_feishu_msg(FEISHU_WEBHOOK, final_text)
                    print("🎉 飞书推送成功！")
                else:
                    print("✨ 本次无满足条件的新币")

        except Exception as e:
            print(f"❌ 主循环异常: {e}")

        print(f"\n⏳ 等待 {SCAN_INTERVAL} 秒后继续扫描...\n")
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    run_monitor()
import time
import requests
from config import FEISHU_WEBHOOK
from send_feishu_msg import send_feishu_msg

DEXSCREENER_BASE = "https://api.dexscreener.com"
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


# ================= 1. 拉取 Robinhood 链代币 =================
def fetch_robinhood_pairs():
    print("🌐 正在从 DexScreener 获取 Robinhood 链数据...")

    all_addresses = set()

    # 1. 最新 Boost
    boosts = safe_get(f"{DEXSCREENER_BASE}/token-boosts/latest/v1")
    if boosts:
        for t in boosts:
            if t.get("chainId") == CHAIN and t.get("tokenAddress"):
                all_addresses.add(t["tokenAddress"].lower())

    # 2. 最新 Token Profiles（补充）
    profiles = safe_get(f"{DEXSCREENER_BASE}/token-profiles/latest/v1")
    if profiles:
        for t in profiles:
            if t.get("chainId") == CHAIN and t.get("tokenAddress"):
                all_addresses.add(t["tokenAddress"].lower())

    if not all_addresses:
        print("⚠️ 未发现 Robinhood 链代币")
        return []

    print(f"📋 发现 {len(all_addresses)} 个 Robinhood 代币，正在拉取交易对数据...")

    all_pairs = []
    addrs = list(all_addresses)

    # 批量查询（每次最多 30 个）
    for i in range(0, len(addrs), 30):
        batch = addrs[i:i+30]
        data = safe_get(f"{DEXSCREENER_BASE}/latest/dex/tokens/{','.join(batch)}")
        if data and "pairs" in data:
            pairs = [p for p in data["pairs"] if p.get("chainId") == CHAIN]
            all_pairs.extend(pairs)

    # 去重
    unique = {}
    for p in all_pairs:
        key = p.get("pairAddress") or p.get("baseToken", {}).get("address")
        if key:
            unique[key] = p

    result = list(unique.values())
    print(f"✅ 成功获取 {len(result)} 个交易对")
    return result


# ================= 2. 策略过滤 =================
def filter_tokens(raw_pairs):
    now = time.time()
    matched = []

    print(f"\n🔍 策略过滤中 (MC ≥ ${MIN_MC:,} | TVL ≥ ${MIN_TVL:,} | 时长 ≤ {MAX_AGE_HOURS}h)\n")

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

        # 处理创建时间（毫秒转秒）
        created_at = float(p.get("pairCreatedAt") or 0)
        if created_at > 1e12:
            created_at /= 1000
        age_hours = (now - created_at) / 3600 if created_at > 0 else 999

        short_addr = address[-6:] if address else "N/A"
        print(f"  [扫描] {symbol:<10} (..{short_addr}) | MC: ${mc:>10,.0f} | TVL: ${tvl:>8,.0f} | 时长: {age_hours:>5.1f}h")

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
            })

    return matched


# ================= 3. 主循环 =================
def run_monitor():
    print("🚀 [Robinhood 策略监控] 启动（DexScreener Boost + Profiles）")
    print(f"   扫描间隔: {SCAN_INTERVAL}s | 条件: MC≥{MIN_MC:,} & TVL≥{MIN_TVL:,} & ≤{MAX_AGE_HOURS}h\n")

    while True:
        try:
            raw_pairs = fetch_robinhood_pairs()
            if not raw_pairs:
                print("⚠️ 本次未获取到数据，等待下次...")
            else:
                hits = filter_tokens(raw_pairs)

                if hits:
                    print(f"\n🎯 命中 {len(hits)} 个代币！准备推送飞书...")
                    msg_lines = []
                    for h in hits:
                        pushed_addresses.add(h["address"])
                        short = h["address"][-6:]
                        msg_lines.append(
                            f"• {h['symbol']} ({h['name']}) | ..{short}\n"
                            f"  价格: ${h['price_usd']:,.8f}\n"
                            f"  MC: ${h['market_cap']:,.0f} | TVL: ${h['tvl']:,.0f}\n"
                            f"  24h: {h['change_24h']:+.1f}% | 开盘: {h['age_hours']}h 前\n"
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

        # print(f"\n⏳ 等待 {SCAN_INTERVAL} 秒后继续扫描...\n")
        # time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    run_monitor()
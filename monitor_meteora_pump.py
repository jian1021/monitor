import json
import os
import time
from datetime import datetime
import requests
from config import FEISHU_WEBHOOK
from db import get_db_client
from send_feishu_msg import send_feishu_msg

# ================= 1. 代理配置（根据需要取消注释或调整端口） =================
# 如果你的 Clash/代理工具运行在 7890 端口，取消下面两行的注释：
# os.environ["http_proxy"] = "http://127.0.0.1:7890"
# os.environ["https_proxy"] = "http://127.0.0.1:7890"


# ================= 2. 数据库加载策略配置 =================
def load_active_strategies():
    """从 sys_lp_config 表中读取所有已激活 (is_active = 1) 的策略配置"""
    client = get_db_client()
    if not client:
        return []

    strategies = []
    try:
        sql = """
        SELECT config_id, config_name, filter_params 
        FROM sys_lp_config 
        WHERE is_active = 1
        """
        rs = client.execute(sql)
        for row in rs.rows:
            raw_param = row[2]
            params = (
                json.loads(raw_param) if isinstance(raw_param, str) else raw_param
            )
            strategies.append({
                "config_id": row[0],
                "config_name": row[1],
                "params": params or {},
            })
    except Exception as e:
        print(f"❌ 读取 sys_lp_config 策略配置失败: {e}")
    finally:
        client.close()

    return strategies


# ================= 3. 全量抓取 100+ 池子（安全抗封锁版） =================
def fetch_meteora_raw_pools():
    session = requests.Session()
    # 模拟真实 Chrome 浏览器 Header
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Origin": "https://app.meteora.ag",
        "Referer": "https://app.meteora.ag/",
    })

    pools = []
    seen_addresses = set()

    # 方案 A: 官方 Meteora DLMM API
    try:
        url_a = "https://dlmm-api.meteora.ag/pair/all_by_groups"
        print("🌐 正在请求 Meteora 官方 DLMM 端点...")
        resp = session.get(url_a, timeout=12)
        if resp.status_code == 200:
            res_json = resp.json()
            groups = (
                res_json.get("groups", [])
                if isinstance(res_json, dict)
                else (res_json if isinstance(res_json, list) else [])
            )
            for g in groups:
                if not isinstance(g, dict):
                    continue
                pairs = g.get("pairs", []) or []
                for p in pairs:
                    if not isinstance(p, dict):
                        continue
                    addr = p.get("address") or p.get("pool_address")
                    if addr and addr not in seen_addresses:
                        seen_addresses.add(addr)
                        pools.append(p)
            if pools:
                print(f"✅ 成功从 Meteora 官方获取到 {len(pools)} 个 DLMM 池子！")
                return pools
        else:
            print(f"⚠️ Meteora 官方端点响应异常 Status: {resp.status_code}")
    except Exception as e:
        print(f"⚠️ Meteora 官方端点请求失败: {e}")

    # 方案 B: DEXScreener 降级备用 API (含 NoneType 安全防护)
    print("🔄 尝试切换至 DEXScreener Solana DLMM 专区获取数据...")
    try:
        url_b = (
            "https://api.dexscreener.com/latest/dex/pairs/solana/meteora_dlmm"
        )
        resp_b = session.get(url_b, timeout=12)
        if resp_b.status_code == 200:
            data_b = resp_b.json()
            # 增加 safe_get，防止 pairs 为 None 导致 TypeError
            pairs_b = data_b.get("pairs") or []
            if isinstance(pairs_b, list):
                for p in pairs_b:
                    if not isinstance(p, dict):
                        continue
                    addr = p.get("pairAddress")
                    if addr and addr not in seen_addresses:
                        seen_addresses.add(addr)
                        pools.append({
                            "name": f"{p.get('baseToken', {}).get('symbol', '')}-{p.get('quoteToken', {}).get('symbol', '')}",
                            "address": addr,
                            "liquidity": float(
                                p.get("liquidity", {}).get("usd", 0) or 0
                            ),
                            "market_cap": float(p.get("fdv", 0) or 0),
                            "fees_24h": float(
                                p.get("volume", {}).get("h24", 0) or 0
                            )
                            * 0.0025,
                            "created_at": p.get("pairCreatedAt", 0),
                            "mint_x": p.get("baseToken", {}).get("address", ""),
                            "mint_y": p.get("quoteToken", {}).get(
                                "address", ""
                            ),
                        })
            if pools:
                print(
                    f"✅ 成功从 DEXScreener 降级源拉取到 {len(pools)} 个 DLMM 池子！"
                )
                return pools
        else:
            print(f"⚠️ DEXScreener 响应异常 Status: {resp_b.status_code}")
    except Exception as e:
        print(f"⚠️ DEXScreener 数据源获取失败: {e}")

    print(
        "❌ 所有数据源均未能获取数据。若在 Mac 终端运行，请确保开启了代理并将终端全局代理打开。"
    )
    return []


def filter_pools_by_strategy(pools: list, strategy: dict, sol_price: float = 150.0):
    """适配 Meteora 字段过滤，修正市值估算与实时扫描展示"""
    p = strategy["params"]

    min_market_cap = p.get("min_market_cap", 0)
    min_liquidity = p.get("min_liquidity", 0)
    min_fee_sol = p.get("min_fee_sol", 0)
    min_age_hours = p.get("min_age_hours", 0)
    max_age_hours = p.get("max_age_hours", 9999)

    now = time.time()
    hit_tokens = []

    for pool in pools:
        pool_name = pool.get("name", "Unknown")
        tvl = float(pool.get("liquidity") or pool.get("tvl") or 0)

        raw_mc = float(pool.get("market_cap") or pool.get("fdv") or 0)
        price = float(pool.get("price", 0) or 0)

        if raw_mc > 0:
            market_cap = raw_mc
        elif price > 0:
            market_cap = price * 1_000_000_000
        else:
            market_cap = tvl * 10.0

        fees_24h_usd = float(pool.get("fees_24h", 0) or 0)
        total_fee_sol = fees_24h_usd / sol_price if sol_price > 0 else 0

        created_at = (
            pool.get("created_at")
            or pool.get("pool_created_at")
            or pool.get("created_timestamp", 0)
        )

        if isinstance(created_at, str) and "T" in created_at:
            try:
                created_ts = datetime.fromisoformat(
                    created_at.replace("Z", "+00:00")
                ).timestamp()
            except Exception:
                created_ts = 0
        else:
            try:
                val = float(created_at or 0)
                created_ts = val / 1000.0 if val > 1e11 else val
            except Exception:
                created_ts = 0

        age_hours = (now - created_ts) / 3600.0 if created_ts > 0 else 0

        # 控制台打印进度
        mc_display = (
            f"${market_cap:>10,.0f}" if raw_mc > 0 else f"~$ {market_cap:>9,.0f}"
        )
        print(
            f"  [扫描中] 池子: {pool_name:<25} | 市值(估): {mc_display} | TVL: ${tvl:>8,.0f} | 创池: {age_hours:>5.1f}h"
        )

        # 阀值判断
        if (
            market_cap >= min_market_cap
            and tvl >= min_liquidity
            and total_fee_sol >= min_fee_sol
            and min_age_hours <= age_hours <= max_age_hours
        ):
            mint_x = pool.get("mint_x", "")
            mint_y = pool.get("mint_y", "")
            sol_mint = "So11111111111111111111111111111111111111112"
            token_address = mint_y if mint_x == sol_mint else mint_x
            pool_address = pool.get("address") or pool.get("pool_address") or ""

            hit_tokens.append({
                "address": token_address,
                "symbol": pool_name,
                "market_cap": market_cap,
                "liquidity": tvl,
                "total_fee_sol": round(total_fee_sol, 2),
                "age_hours": round(age_hours, 1),
                "pool_address": pool_address,
            })

    return hit_tokens


# ================= 4. 主引擎入口 =================
def run_pump_strategy_monitor():
    print("🚀 [Meteora 策略监控引擎] 正在从 Turso 读取激活策略...")
    strategies = load_active_strategies()

    if not strategies:
        print("⚠️ 数据库 sys_lp_config 中没有处于激活状态的策略配置。")
        return

    print(
        f"✅ 成功加载 {len(strategies)} 条激活策略，正在请求 Meteora 数据源..."
    )
    raw_pools = fetch_meteora_raw_pools()

    if not raw_pools:
        print("⚠️ 未获取到 Meteora 池子数据。")
        return

    print(f"\n📡 开始比对 {len(raw_pools)} 个实时池子与策略门槛...\n")

    total_hits = 0
    all_push_messages = []

    for strat in strategies:
        config_name = strat["config_name"]
        config_id = strat["config_id"]

        print(f"🔍 正在执行策略: 【{config_name}】({config_id})...")
        tokens = filter_pools_by_strategy(raw_pools, strat)

        if tokens:
            count = len(tokens)
            total_hits += count
            print(f"\n  🎯 策略【{config_name}】命中 {count} 个标的！")

            msg_lines = [f"📌 匹配策略: 【{config_name}】(符合标的: {count} 个)"]

            for t in tokens[:5]:
                gmgn_url = (
                    f"https://gmgn.ai/sol/token/{t['address']}"
                    if t["address"]
                    else "N/A"
                )
                meteora_url = (
                    f"https://app.meteora.ag/dlmm/{t['pool_address']}"
                    if t["pool_address"]
                    else "N/A"
                )
                msg_lines.append(
                    f"• {t['symbol']} | 估算市值:${t['market_cap']:,.0f} | TVL:${t['liquidity']:,.0f}\n"
                    f"  预估24h手续费:{t['total_fee_sol']} SOL | 开盘:{t['age_hours']}h 前\n"
                    f"  🔗 GMGN: {gmgn_url}\n"
                    f"  🌊 Meteora: {meteora_url}"
                )

            all_push_messages.append("\n".join(msg_lines))
        else:
            print(f"\n  ℹ️ 策略【{config_name}】暂无符合条件的标的。")

    if all_push_messages:
        final_push_text = (
            f"🚀【Meteora DLMM 策略实时监控告警】\n"
            f"========================================\n\n"
            + "\n\n----------------------------------------\n\n".join(
                all_push_messages
            )
        )
        send_feishu_msg(FEISHU_WEBHOOK, final_push_text)
        print(f"\n🎉 监控完毕，已向飞书发送告警，累计命中 {total_hits} 个标的。")
    else:
        print("\n✨ 所有策略轮询完毕，当前没有满足阀值的新标的。")


if __name__ == "__main__":
    run_pump_strategy_monitor()
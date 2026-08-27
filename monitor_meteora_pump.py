import os
import json
import requests
from db import get_db_client

# ================= 1. 数据库交互与策略加载 =================

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
            # 兼容处理：确保解析出 dict 格式
            params = json.loads(row[2]) if isinstance(row[2], str) else row[2]
            strategies.append({
                "config_id": row[0],
                "config_name": row[1],
                "params": params or {}
            })
    except Exception as e:
        print(f"❌ 读取 sys_lp_config 策略配置失败: {e}")
    finally:
        client.close()
    
    return strategies


def fetch_tokens_by_strategy(strategy: dict):
    """
    根据策略的 filter_params 字典，从 token_market_data 中筛选符合条件的标的
    """
    client = get_db_client()
    if not client:
        return []

    p = strategy["params"]
    
    # 严格对齐 JSON 配置字段:
    # 1. min_market_cap
    # 2. min_liquidity
    # 3. min_fee_sol
    # 4. min_age_hours
    # 5. max_age_hours
    min_market_cap = p.get("min_market_cap", 0)
    min_liquidity = p.get("min_liquidity", 0)
    min_fee_sol = p.get("min_fee_sol", 0)
    min_age_hours = p.get("min_age_hours", 0)
    max_age_hours = p.get("max_age_hours", 9999)

    query = """
    SELECT 
        address,
        symbol,
        market_cap,
        liquidity,
        total_fee_sol,
        created_at,
        ROUND((julianday('now') - julianday(created_at)) * 24, 1) AS age_hours
    FROM 
        token_market_data
    WHERE 
        market_cap >= ?
        AND liquidity >= ?
        AND total_fee_sol >= ?
        AND julianday(created_at) <= julianday('now', '-' || ? || ' hours')
        AND julianday(created_at) >= julianday('now', '-' || ? || ' hours')
    ORDER BY 
        created_at DESC;
    """

    hit_tokens = []
    try:
        res = client.execute(query, [
            min_market_cap,
            min_liquidity,
            min_fee_sol,
            min_age_hours,
            max_age_hours
        ])
        
        columns = [col[0] for col in res.columns]
        for row in res.rows:
            hit_tokens.append(dict(zip(columns, row)))
            
    except Exception as e:
        print(f"❌ 执行策略 [{strategy['config_name']}] 检索失败: {e}")
    finally:
        client.close()

    return hit_tokens


# ================= 2. 飞书消息推送 =================

def send_feishu_msg(webhook: str, msg: str):
    """发送飞书文本消息"""
    if not webhook:
        print(f"⚠️ 未配置 FEISHU_WEBHOOK，跳过推送。控制台打印:\n\n{msg}")
        return
    try:
        payload = {
            "msg_type": "text",
            "content": {"text": msg}
        }
        resp = requests.post(webhook, json=payload, timeout=10)
        if resp.status_code == 200:
            print("✅ 飞书消息推送成功！")
        else:
            print(f"⚠️ 飞书推送返回异常: {resp.text}")
    except Exception as e:
        print(f"❌ 飞书推送失败: {e}")


# ================= 3. 主引擎入口 =================

def run_pump_strategy_monitor():
    FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")
    
    print("🚀 [Meteora 策略监控引擎] 正在从 Turso 读取激活策略...")
    strategies = load_active_strategies()

    if not strategies:
        print("⚠️ 数据库 sys_lp_config 中没有处于激活状态 (is_active = 1) 的策略配置。")
        return

    print(f"✅ 成功加载 {len(strategies)} 条激活策略，开始比对 token_market_data 数据...")

    total_hits = 0
    all_push_messages = []

    for strat in strategies:
        config_name = strat["config_name"]
        config_id = strat["config_id"]
        
        print(f"🔍 正在执行策略: 【{config_name}】({config_id})...")
        
        tokens = fetch_tokens_by_strategy(strat)
        if tokens:
            count = len(tokens)
            total_hits += count
            print(f"  🎯 策略【{config_name}】命中 {count} 个标的！")
            
            msg_lines = [f"📌 匹配策略: 【{config_name}】(符合标的: {count} 个)"]
            
            for t in tokens[:5]:  # 推送前 5 个标的
                gmgn_url = f"https://gmgn.ai/sol/token/{t['address']}"
                msg_lines.append(
                    f"• {t['symbol']} | 市值:${t['market_cap']:,.0f} | 池子:${t['liquidity']:,.0f}\n"
                    f"  手续费:{t['total_fee_sol']} SOL | 开盘:{t['age_hours']}h 前\n"
                    f"  🔗 GMGN 盘面: {gmgn_url}"
                )
            
            all_push_messages.append("\n".join(msg_lines))
        else:
            print(f"  ℹ️ 策略【{config_name}】暂无符合条件的标的。")

    if all_push_messages:
        final_push_text = (
            f"🚀【Meme / LP 策略实时监控告警】\n"
            f"========================================\n\n"
            + "\n\n----------------------------------------\n\n".join(all_push_messages)
        )
        send_feishu_msg(FEISHU_WEBHOOK, final_push_text)
        print(f"🎉 监控完毕，累计发现 {total_hits} 个符合策略的标的。")
    else:
        print("✨ 所有策略轮询完毕，当前没有满足阀值的新标的。")



    
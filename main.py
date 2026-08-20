import os
import json
import time
import random
import requests
import pandas as pd
import ta
import akshare as ak

headers = {'User-Agent': 'Mozilla/5.0'}

# ================= 全局默认 RSI 参数配置 =================
DEFAULT_SETTINGS = {
    # 加密货币：使用 1 小时 K 线，RSI 周期 14
    "crypto": {
        "interval": "1H",
        "period": 3,
        "rsi_low": 10,
        "rsi_high": 90
    },
    # 可转债：使用日 K 线 (daily) 或 60 分钟 K 线 (60m)，RSI 周期 14
    "bond": {
        "period": 3,
        "rsi_low": 10,       # RSI < 30 进入超卖区（超跌反弹关注）
        "rsi_high": 90       # RSI > 70 进入超买区
    },
    # ETF：使用日 K 线 (daily)，RSI 周期 14
    "etf": {
        "period": 3,
        "rsi_low": 10,       # RSI < 30 进入超卖区
        "rsi_high": 90       # RSI > 70 进入超买区
    }
}

# ================= 1. 读取配置文件 =================

def load_config(config_file="config.json"):
    """读取 json 配置文件"""
    if not os.path.exists(config_file):
        print(f"❌ 未找到配置文件: {config_file}")
        return None
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"❌ config.json 格式错误，请检查格式或末尾逗号: {e}")
        return None
    except Exception as e:
        print(f"❌ 读取配置文件失败: {e}")
        return None

# ================= 2. 数据获取与 RSI 计算 =================

def get_okx_rsi(symbol, interval="1H", length=14):
    """【加密货币】获取 OKX K线与 RSI"""
    url = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar={interval}&limit=100"
    try:
        res = requests.get(url, headers=headers, timeout=10).json()
        if res.get('code') == '0' and len(res.get('data', [])) > 0:
            df = pd.DataFrame(res['data'])
            df = df.iloc[::-1].reset_index(drop=True)
            df['close'] = df[4].astype(float)
            df['rsi'] = ta.momentum.rsi(df['close'], window=length)
            return df['rsi'].iloc[-1], df['close'].iloc[-1]
    except Exception as e:
        print(f"❌ OKX [{symbol}] 获取失败: {e}")
    return None, None

def get_a_share_rsi(code, length=14, max_retries=3):
    """【A股通用】适用于可转债和 ETF，自带失败重试机制"""
    for attempt in range(max_retries):
        try:
            # stock_zh_a_hist 适配股票、ETF、可转债等所有 A 股标的
            df = ak.stock_zh_a_hist(symbol=str(code), period="daily", adjust="qfq")
            if df is not None and not df.empty and len(df) >= length:
                # 兼容中文列名 '收盘'
                close_col = '收盘' if '收盘' in df.columns else 'close'
                df['close_num'] = df[close_col].astype(float)
                df['rsi'] = ta.momentum.rsi(df['close_num'], window=length)
                return df['rsi'].iloc[-1], df['close_num'].iloc[-1]
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(1.5 * (attempt + 1))  # 失败后等待重试
            else:
                print(f"❌ 标的 [{code}] 请求多次失败，可能被风控拦截。")
    return None, None

def send_feishu_msg(webhook, msg):
    if not webhook:
        print(f"未配置 Webhook，仅日志打印:\n{msg}")
        return
    requests.post(webhook, json={"msg_type": "text", "content": {"text": msg}}, timeout=10)

# ================= 主程序逻辑 =================

if __name__ == "__main__":
    FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")
    config = load_config("config.json")
    
    if not config:
        print("停止运行：未能加载有效的配置文件。")
        exit(1)

    messages = []

    # 1. 加密货币监控
    crypto_list = config.get("crypto_okx", [])
    c_set = DEFAULT_SETTINGS["crypto"]
    for coin in crypto_list:
        symbol = coin.get("symbol")
        if not symbol:
            continue

        rsi, price = get_okx_rsi(symbol, c_set["interval"], c_set["period"])
        if rsi is not None and price is not None:
            print(f"✅ [Crypto] {symbol} 现价: ${price:.2f}, RSI({c_set['period']}): {rsi:.2f}")
            if rsi < c_set["rsi_low"]:
                messages.append(f"🚨 【{symbol} 超卖】现价 ${price:.2f}，1H RSI: {rsi:.2f} (低于 {c_set['rsi_low']})")
            elif rsi > c_set["rsi_high"]:
                messages.append(f"⚠️ 【{symbol} 超买】现价 ${price:.2f}，1H RSI: {rsi:.2f} (高于 {c_set['rsi_high']})")

    # 2. 可转债监控
    cb_list = config.get("convertible_bonds", [])
    b_set = DEFAULT_SETTINGS["bond"]
    for item in cb_list:
        code = str(item.get("code"))
        cfg_name = item.get("name", code)

        rsi, price = get_a_share_rsi(code, length=b_set["period"])
        if rsi is not None and price is not None:
            print(f"✅ [可转债] {cfg_name}({code}) 现价: {price:.2f}, RSI({b_set['period']}): {rsi:.2f}")
            if rsi < b_set["rsi_low"]:
                messages.append(f"🚨 【可转债 RSI 超卖】{cfg_name}({code}) 现价: {price:.2f} 元，RSI: {rsi:.2f} (低于 {b_set['rsi_low']})")
            elif rsi > b_set["rsi_high"]:
                messages.append(f"⚠️ 【可转债 RSI 超买】{cfg_name}({code}) 现价: {price:.2f} 元，RSI: {rsi:.2f} (高于 {b_set['rsi_high']})")
        
        time.sleep(random.uniform(0.8, 1.5))  # 加入随机延迟，防止触发接口频率限制

    # 3. ETF 监控
    etf_list = config.get("etfs", [])
    e_set = DEFAULT_SETTINGS["etf"]
    for item in etf_list:
        code = str(item.get("code"))
        cfg_name = item.get("name", code)

        rsi, price = get_a_share_rsi(code, length=e_set["period"])
        if rsi is not None and price is not None:
            print(f"✅ [ETF] {cfg_name}({code}) 现价: {price:.3f}, RSI({e_set['period']}): {rsi:.2f}")
            if rsi < e_set["rsi_low"]:
                messages.append(f"🚨 【ETF RSI 超卖】{cfg_name}({code}) 现价: {price:.3f} 元，RSI: {rsi:.2f} (低于 {e_set['rsi_low']})")
            elif rsi > e_set["rsi_high"]:
                messages.append(f"⚠️ 【ETF RSI 超买】{cfg_name}({code}) 现价: {price:.3f} 元，RSI: {rsi:.2f} (高于 {e_set['rsi_high']})")

        time.sleep(random.uniform(0.8, 1.5))  # 加入随机延迟，防止触发接口频率限制

    # 4. 发送推送
    if messages:
        full_msg = "\n\n".join(messages)
        send_feishu_msg(FEISHU_WEBHOOK, full_msg)
    else:
        print("所有标的 RSI 均处于正常区间，不触发推送。")

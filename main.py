import os
import json
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
        "rsi_low": 30,
        "rsi_high": 90
    },
    # 可转债：使用日 K 线 (daily) 或 60 分钟 K 线 (60m)，RSI 周期 14
    "bond": {
        "period": 14,
        "rsi_low": 10,       # RSI < 30 进入超卖区（超跌反弹关注）
        "rsi_high": 90       # RSI > 70 进入超买区
    },
    # ETF：使用日 K 线 (daily)，RSI 周期 14
    "etf": {
        "period": 14,
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

def get_cb_rsi(code, length=14):
    """【可转债】通过 AkShare 获取历史 K 线计算 RSI"""
    try:
        # 获取可转债历史日线行情
        df = ak.bond_cb_daily(symbol=code)
        if df is not None and len(df) >= length:
            df['close'] = df['close'].astype(float)
            df['rsi'] = ta.momentum.rsi(df['close'], window=length)
            return df['rsi'].iloc[-1], df['close'].iloc[-1]
    except Exception as e:
        print(f"❌ 可转债 [{code}] RSI 计算失败: {e}")
    return None, None

def get_etf_rsi(code, length=14):
    """【ETF】通过 AkShare 获取历史 K 线计算 RSI"""
    try:
        # 获取 ETF 历史日线行情 (东财数据源)
        df = ak.fund_etf_hist_em(symbol=code, adjust="qfq")
        if df is not None and len(df) >= length:
            df['close'] = df['收盘'].astype(float)
            df['rsi'] = ta.momentum.rsi(df['close'], window=length)
            return df['rsi'].iloc[-1], df['close'].iloc[-1]
    except Exception as e:
        print(f"❌ ETF [{code}] RSI 计算失败: {e}")
    return None, None

def send_feishu_msg(webhook, msg):
    if not webhook:
        print(f"未配置 Webhook，仅日志打印:\n{msg}")
        return
    requests.post(webhook, json={"msg_type": "text", "content": {"text": msg}}, timeout=10)

# ================= 3. 主程序逻辑 =================

if __name__ == "__main__":
    FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")
    config = load_config("config.json")
    
    if not config:
        print("停止运行：未能加载有效的配置文件。")
        exit(1)

    messages = []

    # ---------------- 1. 加密货币 RSI 监控 ----------------
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

    # ---------------- 2. A股可转债 RSI 监控 ----------------
    cb_list = config.get("convertible_bonds", [])
    b_set = DEFAULT_SETTINGS["bond"]
    for item in cb_list:
        code = str(item.get("code"))
        cfg_name = item.get("name", code)

        rsi, price = get_cb_rsi(code, length=b_set["period"])
        if rsi is not None and price is not None:
            print(f"✅ [可转债] {cfg_name}({code}) 现价: {price:.2f}, RSI({b_set['period']}): {rsi:.2f}")
            if rsi < b_set["rsi_low"]:
                messages.append(f"🚨 【可转债 RSI 超卖】{cfg_name}({code}) 现价: {price:.2f} 元，RSI: {rsi:.2f} (低于 {b_set['rsi_low']})")
            elif rsi > b_set["rsi_high"]:
                messages.append(f"⚠️ 【可转债 RSI 超买】{cfg_name}({code}) 现价: {price:.2f} 元，RSI: {rsi:.2f} (高于 {b_set['rsi_high']})")

    # ---------------- 3. A股 ETF RSI 监控 ----------------
    etf_list = config.get("etfs", [])
    e_set = DEFAULT_SETTINGS["etf"]
    for item in etf_list:
        code = str(item.get("code"))
        cfg_name = item.get("name", code)

        rsi, price = get_etf_rsi(code, length=e_set["period"])
        if rsi is not None and price is not None:
            print(f"✅ [ETF] {cfg_name}({code}) 现价: {price:.3f}, RSI({e_set['period']}): {rsi:.2f}")
            if rsi < e_set["rsi_low"]:
                messages.append(f"🚨 【ETF RSI 超卖】{cfg_name}({code}) 现价: {price:.3f} 元，RSI: {rsi:.2f} (低于 {e_set['rsi_low']})")
            elif rsi > e_set["rsi_high"]:
                messages.append(f"⚠️ 【ETF RSI 超买】{cfg_name}({code}) 现价: {price:.3f} 元，RSI: {rsi:.2f} (高于 {e_set['rsi_high']})")

    # ---------------- 4. 统一发送消息 ----------------
    if messages:
        full_msg = "\n\n".join(messages)
        send_feishu_msg(FEISHU_WEBHOOK, full_msg)
    else:
        print("所有标的 RSI 均处于正常区间（30~70），不触发推送。")

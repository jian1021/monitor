import os
import json
import time
import random
import requests
import pandas as pd
import ta
import akshare as ak
import streamlit as st

headers = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# ================= 页面基本配置 =================
st.set_page_config(page_title="多资产 RSI 监控看板", layout="wide", page_icon="📈")
st.title("📈 多资产 RSI 实时监控看板")

# ================= 配置文件加载 =================
def load_config(config_file="config.json"):
    if not os.path.exists(config_file):
        return None
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None

# ================= 数据获取逻辑 =================
def get_okx_rsi(symbol, interval="1H", length=14):
    url = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar={interval}&limit=100"
    try:
        res = requests.get(url, headers=headers, timeout=10).json()
        if res.get('code') == '0' and len(res.get('data', [])) > 0:
            df = pd.DataFrame(res['data'])
            df = df.iloc[::-1].reset_index(drop=True)
            df['close'] = df[4].astype(float)
            df['rsi'] = ta.momentum.rsi(df['close'], window=length)
            return df['rsi'].iloc[-1], df['close'].iloc[-1]
    except Exception:
        pass
    return None, None

@st.cache_data(ttl=60)  # 设置缓存，1分钟内重复查询直接读取缓存，防止过度请求
def get_a_share_rsi(code, length=14):
    try:
        df = ak.stock_zh_a_hist(symbol=str(code), period="daily", adjust="qfq")
        if df is not None and not df.empty and len(df) >= length:
            close_col = '收盘' if '收盘' in df.columns else 'close'
            df['close_num'] = df[close_col].astype(float)
            df['rsi'] = ta.momentum.rsi(df['close_num'], window=length)
            return df['rsi'].iloc[-1], df['close_num'].iloc[-1]
    except Exception:
        pass
    return None, None

def send_feishu_msg(webhook, msg):
    if webhook:
        requests.post(webhook, json={"msg_type": "text", "content": {"text": msg}}, timeout=10)

# ================= 侧边栏参数设置 =================
st.sidebar.header("⚙️ 监控参数设置")
rsi_period = st.sidebar.number_input("RSI 周期", min_value=2, max_value=50, value=14)
rsi_low = st.sidebar.slider("超卖阈值 (低于报警)", 10, 40, 30)
rsi_high = st.sidebar.slider("超买阈值 (高于报警)", 60, 90, 70)
feishu_webhook = st.sidebar.text_input("飞书 Webhook 链接", value=os.getenv("FEISHU_WEBHOOK", ""), type="password")

config = load_config()

if not config:
    st.error("❌ 未找到 `config.json` 配置文件，请检查项目目录！")
else:
    # 点击按钮触发刷新
    if st.button("🔄 立即刷新行情数据", type="primary"):
        st.cache_data.clear()  # 手动刷新时清空缓存

    # 1. 加密货币展示区
    st.subheader("🪙 加密货币 (Crypto)")
    crypto_list = config.get("crypto_okx", [])
    crypto_data = []
    
    with st.spinner("正在获取加密货币数据..."):
        for coin in crypto_list:
            symbol = coin.get("symbol")
            rsi, price = get_okx_rsi(symbol, "1H", rsi_period)
            if rsi is not None:
                status = "🚨 超卖" if rsi < rsi_low else ("⚠️ 超买" if rsi > rsi_high else "🟢 正常")
                crypto_data.append({"标的": symbol, "现价 ($)": f"{price:.2f}", f"RSI({rsi_period})": f"{rsi:.2f}", "状态": status})
    
    if crypto_data:
        st.dataframe(pd.DataFrame(crypto_data), use_container_width=True)

    # 2. A股可转债展示区
    st.subheader("📜 A股可转债")
    cb_list = config.get("convertible_bonds", [])
    cb_data = []
    
    cb_progress = st.progress(0, text="正在获取可转债数据...")
    for idx, item in enumerate(cb_list):
        code = str(item.get("code"))
        name = item.get("name", code)
        rsi, price = get_a_share_rsi(code, length=rsi_period)
        if rsi is not None:
            status = "🚨 超卖" if rsi < rsi_low else ("⚠️ 超买" if rsi > rsi_high else "🟢 正常")
            cb_data.append({"代码": code, "名称": name, "现价 (元)": f"{price:.2f}", f"RSI({rsi_period})": f"{rsi:.2f}", "状态": status})
        cb_progress.progress((idx + 1) / len(cb_list))
        time.sleep(0.2)
    cb_progress.empty()
    
    if cb_data:
        st.dataframe(pd.DataFrame(cb_data), use_container_width=True)

    # 3. ETF 展示区
    st.subheader("📊 ETF 基金")
    etf_list = config.get("etfs", [])
    etf_data = []
    
    etf_progress = st.progress(0, text="正在获取 ETF 数据...")
    for idx, item in enumerate(etf_list):
        code = str(item.get("code"))
        name = item.get("name", code)
        rsi, price = get_a_share_rsi(code, length=rsi_period)
        if rsi is not None:
            status = "🚨 超卖" if rsi < rsi_low else ("⚠️ 超买" if rsi > rsi_high else "🟢 正常")
            etf_data.append({"代码": code, "名称": name, "现价 (元)": f"{price:.3f}", f"RSI({rsi_period})": f"{rsi:.2f}", "状态": status})
        etf_progress.progress((idx + 1) / len(etf_list))
        time.sleep(0.2)
    etf_progress.empty()

    if etf_data:
        st.dataframe(pd.DataFrame(etf_data), use_container_width=True)

    # 4. 触发飞书推送（仅触发异常时）
    alerts = []
    for row in crypto_data + cb_data + etf_data:
        if "超卖" in row["状态"] or "超买" in row["状态"]:
            name = row.get("名称", row.get("标的", ""))
            code = row.get("代码", "")
            rsi_val = row[f"RSI({rsi_period})"]
            alerts.append(f"{row['状态']} | {name} ({code}) | RSI: {rsi_val}")
            
    if alerts and feishu_webhook:
        if st.button("📤 发送异常报警至飞书"):
            send_feishu_msg(feishu_webhook, "\n\n".join(alerts))
            st.success("已成功推送警报！")

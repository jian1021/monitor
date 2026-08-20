import os
import requests
import pandas as pd
import pandas_ta as ta

def get_binance_rsi(symbol="BTCUSDT", interval="1h", length=14):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit=100"
    res = requests.get(url).json()
    
    # 解析收盘价
    df = pd.DataFrame(res, columns=['time', 'open', 'high', 'low', 'close', 'vol', 'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore'])
    df['close'] = df['close'].astype(float)
    
    # 计算 RSI
    df['rsi'] = ta.rsi(df['close'], length=length)
    latest_rsi = df['rsi'].iloc[-1]
    latest_price = df['close'].iloc[-1]
    return latest_rsi, latest_price

def send_feishu_msg(webhook, msg):
    if not webhook:
        print("未配置 Webhook，跳过发送")
        return
    requests.post(webhook, json={"msg_type": "text", "content": {"text": msg}})

if __name__ == "__main__":
    FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")
    rsi, price = get_binance_rsi("BTCUSDT")
    
    print(f"BTC 当前价格: {price}, RSI(14): {rsi:.2f}")
    
    # 设置告警条件：超买或超卖
    if rsi < 10:
        send_feishu_msg(FEISHU_WEBHOOK, f"🚨 【BTC 超卖预警】当前价格 ${price}，RSI 为 {rsi:.2f} (低于 30)")
    elif rsi > 90:
        send_feishu_msg(FEISHU_WEBHOOK, f"⚠️ 【BTC 超买预警】当前价格 ${price}，RSI 为 {rsi:.2f} (高于 70)")

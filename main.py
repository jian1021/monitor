import os
import requests
import pandas as pd
import pandas_ta as ta
import ta

def get_binance_rsi(symbol="bitcoin", interval="1h", length=14):
    """
    使用 CoinGecko 免费 API 获取 K 线并用 ta 库计算 RSI
    :param symbol: CoinGecko 中的币种 ID（如 'bitcoin', 'ethereum', 'solana'）
    """
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        # CoinGecko OHLC 接口：days=1 会自动返回按小时 (1h) 分隔的 K 线数据
        url = f"https://api.coingecko.com/api/v3/coins/{symbol}/ohlc?vs_currency=usd&days=1"
        res = requests.get(url, headers=headers, timeout=10).json()
        
        if isinstance(res, list) and len(res) > 0:
            # CoinGecko 返回格式: [timestamp, open, high, low, close]
            df = pd.DataFrame(res, columns=['time', 'open', 'high', 'low', 'close'])
            df['close'] = df['close'].astype(float)
            
            # 使用 ta 库计算 RSI
            df['rsi'] = ta.momentum.rsi(df['close'], window=length)
            
            latest_rsi = df['rsi'].iloc[-1]
            latest_price = df['close'].iloc[-1]
            return latest_rsi, latest_price
        else:
            print(f"CoinGecko 返回数据异常: {res}")
            
    except Exception as e:
        print(f"CoinGecko API 请求失败: {e}")

    return None, None

def send_feishu_msg(webhook, msg):
    if not webhook:
        print("未配置 Webhook，跳过发送")
        return
    requests.post(webhook, json={"msg_type": "text", "content": {"text": msg}})

if __name__ == "__main__":
    FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")
    rsi, price = get_binance_rsi("BTCUSDT")
    
    print(f"BTC 当前价格: {price}, RSI(3): {rsi:.2f}")
    
    # 设置告警条件：超买或超卖
    if rsi < 10:
        send_feishu_msg(FEISHU_WEBHOOK, f"🚨 【BTC 超卖预警】当前价格 ${price}，RSI 为 {rsi:.2f} (低于 30)")
    elif rsi > 90:
        send_feishu_msg(FEISHU_WEBHOOK, f"⚠️ 【BTC 超买预警】当前价格 ${price}，RSI 为 {rsi:.2f} (高于 70)")

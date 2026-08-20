import os
import requests
import pandas as pd
import ta  # 仅保留 ta 库

def get_coingecko_rsi(symbol="bitcoin", interval="1h", length=14):
    """
    使用 CoinGecko 免费 API 获取 K 线并用 ta 库计算 RSI
    :param symbol: CoinGecko 币种 ID（如 'bitcoin', 'ethereum', 'solana'）
    """
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        # CoinGecko OHLC 接口：days=1 自动按 1 小时级别返回 K 线
        url = f"https://api.coingecko.com/api/v3/coins/{symbol}/ohlc?vs_currency=usd&days=1"
        res = requests.get(url, headers=headers, timeout=10).json()
        
        if isinstance(res, list) and len(res) > 0:
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
    requests.post(webhook, json={"msg_type": "text", "content": {"text": msg}}, timeout=10)

if __name__ == "__main__":
    FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")
    
    # 1. 传入 CoinGecko 的 ID（bitcoin），而不是交易对（BTCUSDT）
    # 2. 如果要计算 RSI(14)，这里使用默认 length=14 即可
    rsi, price = get_coingecko_rsi("bitcoin", length=14)
    
    # 防空保护
    if rsi is None or price is None:
        print("❌ 获取数据失败，放弃本次推送")
    else:
        print(f"✅ BTC 当前价格: ${price:.2f}, RSI(14): {rsi:.2f}")
        
        # 触发告警条件
        if rsi < 10:
            send_feishu_msg(FEISHU_WEBHOOK, f"🚨 【BTC 极端超卖】当前价格 ${price:.2f}，RSI 为 {rsi:.2f} (低于 10)")
        elif rsi > 90:
            send_feishu_msg(FEISHU_WEBHOOK, f"⚠️ 【BTC 极端超买】当前价格 ${price:.2f}，RSI 为 {rsi:.2f} (高于 90)")

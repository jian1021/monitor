import os
import requests
import pandas as pd
import ta

def get_okx_rsi(symbol="BTC-USDT", interval="1H", length=14):
    """
    使用 OKX 公开接口获取 K 线并用 ta 库计算 RSI
    :param symbol: OKX 交易对格式（如 'BTC-USDT', 'ETH-USDT', 'SOL-USDT'）
    :param interval: K 线周期（如 '15m', '1H', '4H', '1D'）
    """
    headers = {'User-Agent': 'Mozilla/5.0'}
    url = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar={interval}&limit=100"
    
    try:
        res = requests.get(url, headers=headers, timeout=10).json()
        
        # 校验 OKX 返回状态
        if res.get('code') == '0' and len(res.get('data', [])) > 0:
            # OKX 返回格式: [ts, open, high, low, close, vol, ...]
            df = pd.DataFrame(res['data'])
            
            # OKX 返回的数据最新的一条在第 0 行，倒序排列为时间正序
            df = df.iloc[::-1].reset_index(drop=True)
            df['close'] = df[4].astype(float)
            
            # 使用 ta 库计算 RSI
            df['rsi'] = ta.momentum.rsi(df['close'], window=length)
            
            latest_rsi = df['rsi'].iloc[-1]
            latest_price = df['close'].iloc[-1]
            return latest_rsi, latest_price
        else:
            print(f"❌ OKX 返回数据异常: {res}")
            
    except Exception as e:
        print(f"❌ OKX API 请求失败: {e}")

    return None, None

def send_feishu_msg(webhook, msg):
    if not webhook:
        print("未配置 Webhook，跳过推送并仅在日志打印")
        return
    requests.post(webhook, json={"msg_type": "text", "content": {"text": msg}}, timeout=10)

if __name__ == "__main__":
    FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")
    
    # 使用 OKX 的标准交易对名称：BTC-USDT
    rsi, price = get_okx_rsi("BTC-USDT", interval="1H", length=14)
    
    # 增加空值安全防护，防止 rsi 为 None 时格式化报错
    if rsi is None or price is None:
        print("⚠️ 数据获取失败，放弃本次推送")
    else:
        print(f"✅ BTC 当前价格: ${price:.2f}, RSI(14): {rsi:.2f}")
        
        # 设置告警条件
        if rsi < 30:
            send_feishu_msg(FEISHU_WEBHOOK, f"🚨 【BTC 超卖预警】当前价格 ${price:.2f}，RSI 为 {rsi:.2f} (低于 30)")
        elif rsi > 70:
            send_feishu_msg(FEISHU_WEBHOOK, f"⚠️ 【BTC 超买预警】当前价格 ${price:.2f}，RSI 为 {rsi:.2f} (高于 70)")

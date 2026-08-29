import os
import json
import time
import random
import requests
import pandas as pd
import ta
import baostock as bs
from db import init_db, load_instruments
headers = {'User-Agent': 'Mozilla/5.0'}
# ================= 全局默认 RSI 参数配置 =================
DEFAULT_SETTINGS = {
    "crypto": {
        "interval": "1H",
        "period": 3,
        "rsi_low": 10,
        "rsi_high": 90
    },
    "meteora": {
        "timeframe": "hour",
        "aggregate": 1,
        "period": 3,
        "rsi_low": 10,
        "rsi_high": 90
    },
    "bond": {
        "period": 3,
        "rsi_low": 10,
        "rsi_high": 90
    },
    "etf": {
        "period": 3,
        "rsi_low": 10,
        "rsi_high": 90
    }
}

# ================= 1. 数据获取与 RSI 计算 =================
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
    except Exception as e:
        import traceback
        print(f"❌ OKX [{symbol}] 获取失败: {e}")
        traceback.print_exc()
    return None, None


def get_meteora_rsi(pool_address, timeframe="hour", aggregate=1, length=14):
    url = f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool_address}/ohlcv/{timeframe}?aggregate={aggregate}&limit=100"
    try:
        res = requests.get(url, headers=headers, timeout=10).json()
        data_list = res.get("data", {}).get("attributes", {}).get("ohlcv_list", [])
        if data_list:
            df = pd.DataFrame(data_list, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df = df.iloc[::-1].reset_index(drop=True)
            df['close'] = df['close'].astype(float)
            df['rsi'] = ta.momentum.rsi(df['close'], window=length)
            return df['rsi'].iloc[-1], df['close'].iloc[-1]
    except Exception as e:
        import traceback
        print(f"❌ Meteora [{pool_address}] 获取失败: {e}")
        traceback.print_exc()
    return None, None


def _to_bs_code(code):
    c = str(code).strip().lower()
    if c.startswith(("sh.", "sz.")):
        return c
    c = c.zfill(6)
    if c[0] in ("5", "6", "9") or c.startswith("11"):
        return f"sh.{c}"
    return f"sz.{c}"


def _get_tencent_rsi(code, length=5):
    try:
        c = str(code).strip().lower().zfill(6)
        mkt = 'sh' if c[0] in ('5', '6', '9') or c.startswith('11') else 'sz'
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={mkt}{c},day,,,320,qfq"
        res = requests.get(url, headers=headers, timeout=10).json()
        days = res.get('data', {}).get(f'{mkt}{c}', {})
        days = days.get('qfqday') or days.get('day') or []
        if len(days) >= length:
            close = pd.Series([float(k[2]) for k in days])
            rsi = ta.momentum.rsi(close, window=length)
            return rsi.iloc[-1], close.iloc[-1]
    except Exception as e:
        import traceback
        print(f"❌ 腾讯降级源 [{code}] 获取失败: {e}")
        traceback.print_exc()
    return None, None


def get_a_share_rsi(code, bs_login_done: bool, length=5, max_retries=3):
    bs_code = _to_bs_code(code)
    start_date = (pd.Timestamp.now() - pd.Timedelta(days=100)).strftime("%Y-%m-%d")

    for attempt in range(max_retries):
        try:
            if not bs_login_done:
                raise RuntimeError("baostock未登录")
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,close",
                start_date=start_date,
                frequency="d",
                adjustflag="2",
            )
            if rs.error_code != '0':
                raise RuntimeError(f"baostock 查询失败: {rs.error_msg}")
            df = rs.get_data()

            if df is not None and not df.empty and 'close' in df.columns:
                df = df[df['close'] != '']
                if len(df) >= length:
                    df['close_num'] = df['close'].astype(float)
                    df['rsi'] = ta.momentum.rsi(df['close_num'], window=length)
                    return df['rsi'].iloc[-1], df['close_num'].iloc[-1]
            return _get_tencent_rsi(code, length)
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1.5 * (attempt + 1))
            else:
                print(f"❌ 标的 [{code}] 请求多次失败: {e}")
    return _get_tencent_rsi(code, length)

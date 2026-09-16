import os
import json
import time
import random
import requests
import pandas as pd
import ta
import baostock as bs
from db import get_db_client
headers = {'User-Agent': 'Mozilla/5.0'}
# ================= 全局默认 RSI 参数配置 =================
# 注意周期写法不通用：OKX 用 interval ("1W")，baostock/腾讯用 frequency ("w")。
DEFAULT_SETTINGS = {
    "crypto": {
        "interval": "1W",
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
        "frequency": "w",
        "period": 3,
        "rsi_low": 10,
        "rsi_high": 90
    },
    "etf": {
        "frequency": "w",
        "period": 3,
        "rsi_low": 10,
        "rsi_high": 90
    },
    "token": {
        "resolution": "1h",
        "period": 3,
        "rsi_low": 10,
        "rsi_high": 90
    }
}

_TIMEFRAME_LABELS = {
    "1W": "周线", "1w": "周线", "w": "周线", "week": "周线",
    "1D": "日线", "1d": "日线", "d": "日线", "day": "日线",
    "1H": "1H", "1h": "1H", "hour": "1H",
}


def timeframe_label(key):
    return _TIMEFRAME_LABELS.get(str(key).strip(), str(key))


def get_token_rsi(chain, address, resolution="1h", length=14):
    """链上代币 RSI：Dexscreener 解析出池子，再取 GeckoTerminal K线算 RSI.

    与其它模块一致的返回约定：成功 (rsi, close)，失败 (None, None)。
    """
    try:
        from dex_client import fetch_ohlcv

        _t, _o, _h, _l, closes, _v = fetch_ohlcv(chain, address, resolution)
        if closes is None or len(closes) < length:
            print(f"⚠️ 链上代币 [{address[:10]}...] K线不足 ({0 if closes is None else len(closes)} 根)")
            return None, None
        close = pd.Series(closes)
        rsi = ta.momentum.rsi(close, window=length)
        return rsi.iloc[-1], close.iloc[-1]
    except (SystemExit, ValueError) as e:
        print(f"❌ 链上代币 [{address[:10]}...] 取数失败: {e}")
    except Exception as e:
        print(f"❌ 链上代币 [{address[:10]}...] 异常: {e}")
    return None, None


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


_TENCENT_KLINE = {
    "d": ("day", ("qfqday", "day")),
    "w": ("week", ("qfqweek", "week")),
}


def _get_tencent_rsi(code, length=5, frequency="d"):
    try:
        c = str(code).strip().lower().zfill(6)
        mkt = 'sh' if c[0] in ('5', '6', '9') or c.startswith('11') else 'sz'
        period, keys = _TENCENT_KLINE.get(str(frequency).lower(), _TENCENT_KLINE["d"])
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={mkt}{c},{period},,,320,qfq"
        res = requests.get(url, headers=headers, timeout=10).json()
        node = res.get('data', {}).get(f'{mkt}{c}', {})
        rows = next((node[k] for k in keys if node.get(k)), [])
        if len(rows) >= length:
            close = pd.Series([float(k[2]) for k in rows])
            rsi = ta.momentum.rsi(close, window=length)
            return rsi.iloc[-1], close.iloc[-1]
    except Exception as e:
        import traceback
        print(f"❌ 腾讯降级源 [{code}] 获取失败: {e}")
        traceback.print_exc()
    return None, None


def get_a_share_rsi(code, bs_login_done: bool, length=5, max_retries=3, frequency="d"):
    bs_code = _to_bs_code(code)
    lookback_days = 100 if str(frequency).lower() == "d" else 730
    start_date = (pd.Timestamp.now() - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    for attempt in range(max_retries):
        try:
            if not bs_login_done:
                raise RuntimeError("baostock未登录")
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,close",
                start_date=start_date,
                frequency=frequency,
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
            return _get_tencent_rsi(code, length, frequency)
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1.5 * (attempt + 1))
            else:
                print(f"❌ 标的 [{code}] 请求多次失败: {e}")
    return _get_tencent_rsi(code, length, frequency)

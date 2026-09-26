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
        "resolution": "1d",
        "days": 15,
        "period": 3,
        "rsi_low": 10,
        "rsi_high": 90
    }
}

_TIMEFRAME_LABELS = {
    "1W": "周线", "1w": "周线", "w": "周线", "week": "周线",
    "1D": "日线", "1d": "日线", "d": "日线", "day": "日线",
    "4H": "4H", "4h": "4H",
    "1H": "1H", "1h": "1H", "hour": "1H",
}

# 链上代币：各 K 线周期的取数窗口（天），与页面 TOKEN_TIMEFRAMES 的 key 对齐
TOKEN_RESOLUTION_DAYS = {"1h": 5, "4h": 7, "1d": 15}

RSI_BAR_OFFSET = 2  # 1=最新未收线 2=倒数第二根已收线，下次切回最新只需改1


def _calc_rsi(close, window: int, offset: int | None = None):
    if offset is None:
        offset = RSI_BAR_OFFSET
    rsi = ta.momentum.rsi(close, window=window)
    idx = -offset
    if len(rsi) >= offset and pd.notna(rsi.iloc[idx]):
        return rsi.iloc[idx], close.iloc[idx]
    return rsi.iloc[-1], close.iloc[-1]


def timeframe_label(key):
    return _TIMEFRAME_LABELS.get(str(key).strip(), str(key))


def get_token_rsi(chain, address, resolution="1h", length=14, days=None):
    """链上代币 RSI：Dexscreener 解析出池子，再取 GeckoTerminal K线算 RSI.

    与其它模块一致的返回约定：成功 (rsi, close)，失败 (None, None)。
    days 为取数窗口天数，直接决定 K 线根数；过小会让 RSI 的 Wilder 平滑未收敛。
    """
    try:
        from dex_client import fetch_ohlcv

        _t, _o, _h, _l, closes, _v = fetch_ohlcv(chain, address, resolution, days)
        if closes is None or len(closes) < length + RSI_BAR_OFFSET - 1:
            print(f"⚠️ 链上代币 [{address[:10]}...] K线不足 ({0 if closes is None else len(closes)} 根)")
            return None, None
        close = pd.Series(closes)
        return _calc_rsi(close, length)
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
            close = df[4].astype(float)
            return _calc_rsi(close, length)
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
            close = df['close'].astype(float)
            return _calc_rsi(close, length)
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
        if len(rows) >= length + RSI_BAR_OFFSET - 1:
            close = pd.Series([float(k[2]) for k in rows])
            return _calc_rsi(close, length)
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
                if len(df) >= length + RSI_BAR_OFFSET - 1:
                    close_num = df['close'].astype(float)
                    return _calc_rsi(close_num, length)
            return _get_tencent_rsi(code, length, frequency)
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1.5 * (attempt + 1))
            else:
                print(f"❌ 标的 [{code}] 请求多次失败: {e}")
    return _get_tencent_rsi(code, length, frequency)

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


METEORA_DATAPI_BASE = "https://dlmm.datapi.meteora.ag"

# Meteora 原生 OHLCV 允许的 timeframe；调用方传 "hour" 等旧写法时映射到这里。
_METEORA_TF_SECONDS = {
    "5m": 300, "30m": 1800, "1h": 3600, "2h": 7200,
    "4h": 14400, "12h": 43200, "24h": 86400,
}
_METEORA_TF_ALIAS = {
    "hour": "1h", "1h": "1h", "1H": "1h", "60m": "1h",
    "30m": "30m", "30min": "30m",
    "5m": "5m",
    "2h": "2h", "4h": "4h", "12h": "12h",
    "day": "24h", "1d": "24h", "24h": "24h",
}

# quote 币 mint（与 monitor_meme_underval.QUOTE_MINTS 同源；为避免循环导入在此复刻，
# 仅用于从池子元数据里挑 meme 边 USD 价格）。
_QUOTE_MINTS = frozenset({
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMEdh6CkPauqoXNqcy2FWT8MFL5Sdf5N8V6etG5h",
})


def _meme_side_usd_price(pool_meta):
    """从 /pools/{address} 元数据取 meme 边 token 的 USD 价格（保持旧 Gecko 口径的 $ 显示）。"""
    sides = [pool_meta.get("token_x") or {}, pool_meta.get("token_y") or {}]
    sides = [s for s in sides if isinstance(s, dict) and s]
    if not sides:
        return None
    candidates = [s for s in sides if str(s.get("address", "")) not in _QUOTE_MINTS]
    if not candidates:
        candidates = sides
    try:
        return float(min(float(s.get("price") or 0) for s in candidates))
    except (TypeError, ValueError):
        return None


def get_meteora_rsi(pool_address, timeframe="hour", aggregate=1, length=14):
    """Meteora 池 RSI（GeckoTerminal 池 K 线口径；除 meme 低估监控外各调用方共用）。"""
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


def get_meteora_native_rsi(pool_address, timeframe="hour", aggregate=1, length=14):
    """Meteora 池 RSI（原生 DLMM datapi 口径，仅 meme 低估监控使用）.

    与其它模块一致的返回约定：成功 (rsi, price_usd)，失败 (None, None)。
    RSI 本身无量纲，直接用原生 base/quote 收盘价序列计算；price 腿为保持
    旧 Gecko 口径（USD 计价展示），取池子元数据里 meme 边的 token USD 价格。
    """
    try:
        tf = _METEORA_TF_ALIAS.get(str(timeframe).strip(), "1h")
        tf_seconds = _METEORA_TF_SECONDS[tf]
        minimum = int(length) + RSI_BAR_OFFSET - 1
        end_ts = int(time.time())
        # 实测原生接口单次窗口上限约 96 根（100h 直接返回空），从大到小逐档尝试。
        counts = sorted({min(max(100, minimum + 5), 96), 48, 24}, reverse=True)
        data_list = []
        for count in counts:
            url = (f"{METEORA_DATAPI_BASE}/pools/{pool_address}/ohlcv"
                   f"?timeframe={tf}&start_time={end_ts - count * tf_seconds}&end_time={end_ts}")
            res = requests.get(url, headers=headers, timeout=10).json()
            data_list = res.get("data", []) if isinstance(res, dict) else []
            if len(data_list) >= minimum:
                break
        if not data_list or len(data_list) < minimum:
            print(f"⚠️ Meteora [{str(pool_address)[:10]}...] K线不足 ({len(data_list)} 根)")
            return None, None
        # 原生接口按时间升序返回；防御性再排一次序（Gecko 侧是降序要反转，这里不要反转）。
        data_list = sorted(data_list, key=lambda c: c.get("timestamp", 0))
        close = pd.Series([float(c["close"]) for c in data_list])
        rsi, _native_close = _calc_rsi(close, length)
        meta = None
        try:
            meta = requests.get(f"{METEORA_DATAPI_BASE}/pools/{pool_address}",
                                headers=headers, timeout=10).json()
        except Exception:
            meta = None
        price = _meme_side_usd_price(meta) if isinstance(meta, dict) else None
        if price is None:
            # 元数据失败时降级用原生收盘价（quote 计价，仅保证告警不断流）。
            price = float(close.iloc[-1])
        return rsi, price
    except Exception as e:
        import traceback
        print(f"❌ Meteora [{str(pool_address)[:10]}...] 获取失败: {e}")
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

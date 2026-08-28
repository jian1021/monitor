import os
import json
import time
import random
import requests
import pandas as pd
import ta
import baostock as bs
from db import init_db, load_instruments
import monitor_meteora_pump

headers = {'User-Agent': 'Mozilla/5.0'}

# ================= 全局默认 RSI 参数配置 =================
DEFAULT_SETTINGS = {
    # 加密货币 (OKX)：使用 1 小时 K 线，RSI 周期 3
    "crypto": {
        "interval": "1H",
        "period": 3,
        "rsi_low": 10,
        "rsi_high": 90
    },
    # Meteora 流动池：使用 1 小时 K 线，RSI 周期 3
    "meteora": {
        "timeframe": "hour",  # GeckoTerminal: minute / hour / day
        "aggregate": 1,       # 1hour
        "period": 3,
        "rsi_low": 10,
        "rsi_high": 90
    },
    # 可转债：使用日 K 线 (daily)，RSI 周期 3
    "bond": {
        "period": 3,
        "rsi_low": 10,       # RSI < 10 进入超卖区（超跌反弹关注）
        "rsi_high": 90       # RSI > 90 进入超买区
    },
    # ETF：使用日 K 线 (daily)，RSI 周期 3
    "etf": {
        "period": 3,
        "rsi_low": 10,       # RSI < 10 进入超卖区
        "rsi_high": 90       # RSI > 90 进入超买区
    }
}

# ================= 1. 数据获取与 RSI 计算 =================
def get_okx_rsi(symbol, interval="1H", length=14):
    """【加密货币】获取 OKX K线与 RSI"""
    url = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar={interval}&limit=100"
    try:
        res = requests.get(url, headers=headers, timeout=10).json()
        if res.get('code') == '0' and len(res.get('data', [])) > 0:
            df = pd.DataFrame(res['data'])
            df = df.iloc[::-1].reset_index(drop=True)
            df['close'] = df[4].astype(float)
[4].astype(float)
            df['rsi'] = ta.momentum.rsi(df['close'], window=length)
            return df['rsi'].iloc[-1], df['close'].iloc[-1]
    except Exception as e:
        import traceback
        print(f"❌ OKX [{symbol}] 获取失败: {e}")
        traceback.print_exc()
    return None, None


def get_meteora_rsi(pool_address, timeframe="hour", aggregate=1, length=14):
    """【Meteora 流动池】通过 GeckoTerminal API 获取 Solana 池子 OHLCV 并计算 RSI"""
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
    """6 位纯数字代码转 baostock 格式：sh.XXXXXX / sz.XXXXXX"""
    c = str(code).strip().lower()
    if c.startswith(("sh.", "sz.")):
        return c
    c = c.zfill(6)
    if c[0] in ("5", "6", "9") or c.startswith("11"):
        return f"sh.{c}"
    return f"sz.{c}"


def _get_tencent_rsi(code, length=5):
    """腾讯 K 线降级源：覆盖可转债等 baostock 未收录品种"""
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
    """【A股通用·baostock主源+腾讯降级】适用于股票、可转债和 ETF，自带失败重试机制
    bs_login_done: 外部统一登录，函数内部不再重复login/logout
    """
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


def send_feishu_msg(webhook, msg):
    if not webhook:
        print(f"未配置 Webhook，仅日志打印:\n{msg}")
        return
    try:
        resp = requests.post(webhook, json={"msg_type": "text", "content": {"text": msg}}, timeout=10)
        print(f"📩飞书推送 status={resp.status_code}")
    except Exception as e:
        import traceback
        print(f"❌飞书消息发送异常 {e}")
        traceback.print_exc()


# ================= 主程序逻辑 =================
if __name__ == "__main__":
    import traceback
    FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")
    init_db()
    config = load_instruments()

    if not config:
        print("停止运行：未能加载有效的配置文件。")
        exit(1)

    messages = []
    bs_login_ok = False
    try:
        lg_ret = bs.login()
        if lg_ret.error_code == '0':
            bs_login_ok = True
            print("✅ baostock login success")
    except Exception as e:
        print(f"⚠️ baostock登录失败，A股源将全部降级到腾讯源 {e}")

    # 1. OKX 加密货币监控
    crypto_list = config.get("crypto_okx", [])
    c_set = DEFAULT_SETTINGS["crypto"]
    for coin in crypto_list:
        if not coin.get("enabled", True):
            continue
        symbol = coin.get("symbol")
        if not symbol:
            continue
        rsi, price = get_okx_rsi(symbol, c_set["interval"], c_set["period"])
        if rsi is not None and price is not None:
            print(f"✅ [OKX] {symbol} 现价: ${price:.4f}, RSI({c_set['period']}): {rsi:.2f}")
            if rsi < c_set["rsi_low"]:
                messages.append(f"🚨 【{symbol} 超卖】现价 ${price:.4f}，1H RSI: {rsi:.2f} (低于 {c_set['rsi_low']})")
            elif rsi > c_set["rsi_high"]:
                messages.append(f"⚠️ 【{symbol} 超买】现价 ${price:.4f}，1H RSI: {rsi:.2f} (高于 {c_set['rsi_high']})")

    # 2. Meteora 流动池监控
    meteora_list = config.get("meteora", [])
    m_set = DEFAULT_SETTINGS["meteora"]
    for pool in meteora_list:
        if not pool.get("enabled", True):
            continue
        address = pool.get("code")
        pool_name = pool.get("name", address)
        if not address:
            continue
        rsi, price = get_meteora_rsi(address, m_set["timeframe"], m_set["aggregate"], m_set["period"])
        if rsi is not None and price is not None:
            print(f"✅ [Meteora] {pool_name} 现价: ${price:.6f}, RSI({m_set['period']}): {rsi:.2f}")
            if rsi < m_set["rsi_low"]:
                messages.append(f"🚨 【Meteora {pool_name} 超卖】现价 ${price:.6f}，1H RSI: {rsi:.2f} (低于 {m_set['rsi_low']})")
            elif rsi > m_set["rsi_high"]:
                messages.append(f"⚠️ 【Meteora {pool_name} 超买】现价 ${price:.6f}，1H RSI: {rsi:.2f} (高于 {m_set['rsi_high']})")
        time.sleep(random.uniform(1.0, 2.0))

    # 3. 可转债监控
    cb_list = config.get("convertible_bonds", [])
    b_set = DEFAULT_SETTINGS["bond"]
    for item in cb_list:
        if not item.get("enabled", True):
            continue
        code = str(item.get("code"))
        cfg_name = item.get("name", code)
        rsi, price = get_a_share_rsi(code, bs_login_ok, length=b_set["period"])
        if rsi is not None and price is not None:
            print(f"✅ [可转债] {cfg_name}({code}) 现价: {price:.2f}, RSI({b_set['period']}): {rsi:.2f}")
            if rsi < b_set["rsi_low"]:
                messages.append(f"🚨 【可转债 RSI 超卖】{cfg_name}({code}) 现价: {price:.2f} 元，RSI: {rsi:.2f} (低于 {b_set['rsi_low']})")
            elif rsi > b_set["rsi_high"]:
                messages.append(f"⚠️ 【可转债 RSI 超买】{cfg_name}({code}) 现价: {price:.2f} 元，RSI: {rsi:.2f} (高于 {b_set['rsi_high']})")
        time.sleep(random.uniform(0.8, 1.5))

    # 4. ETF 监控
    etf_list = config.get("etfs", [])
    e_set = DEFAULT_SETTINGS["etf"]
    for item in etf_list:
        if not item.get("enabled", True):
            continue
        code = str(item.get("code"))
        cfg_name = item.get("name", code)
        rsi, price = get_a_share_rsi(code, bs_login_ok, length=e_set["period"])
        if rsi is not None and price is not None:
            print(f"✅ [ETF] {cfg_name}({code}) 现价: {price:.3f}, RSI({e_set['period']}): {rsi:.2f}")
            if rsi < e_set["rsi_low"]:
                messages.append(f"🚨 【ETF RSI 超卖】{cfg_name}({code}) 现价: {price:.3f} 元，RSI: {rsi:.2f} (低于 {e_set['rsi_low']})")
            elif rsi > e_set["rsi_high"]:
                messages.append(f"⚠️ 【ETF RSI 超买】{cfg_name}({code}) 现价: {price:.3f} 元，RSI: {rsi:.2f} (高于 {e_set['rsi_high']})")
        time.sleep(random.uniform(0.8, 1.5))

    # 释放baostock
    if bs_login_ok:
        bs.logout()

    # 5.发送RSI告警
    if messages:
        full_msg = "\n\n".join(messages)
        send_feishu_msg(FEISHU_WEBHOOK, full_msg)
    else:
        print("所有标的 RSI 均处于正常区间，不触发推送。")

    # ========== 调用 Turso Meteora pump策略监控 ==========
    print("\n====== 开始执行 meteora pump 策略监控 ======")
    try:
        monitor_meteora_pump.run_pump_strategy_monitor()
    except Exception as e:
        print(f"❌ monitor_meteora_pump.run_pump_strategy_monitor() 发生异常：{e}")
        traceback.print_exc()

    print("\n✅ main.py 全部任务执行完毕")

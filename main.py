import os
import json
import time
import random
import requests
import pandas as pd
import ta
#import akshare as ak
import baostock as bs
from db import init_db, load_instruments

headers = {'User-Agent': 'Mozilla/5.0'}

# ================= 全局默认 RSI 参数配置 =================
DEFAULT_SETTINGS = {
    # 加密货币：使用 1 小时 K 线，RSI 周期 3
    "crypto": {
        "interval": "1H",
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
            df['rsi'] = ta.momentum.rsi(df['close'], window=length)
            return df['rsi'].iloc[-1], df['close'].iloc[-1]
    except Exception as e:
        print(f"❌ OKX [{symbol}] 获取失败: {e}")
    return None, None

def _to_bs_code(code):
    """6 位纯数字代码转 baostock 格式：sh.XXXXXX / sz.XXXXXX"""
    c = str(code).strip().lower()
    if c.startswith(("sh.", "sz.")):
        return c
    c = c.zfill(6)
    # 沪市：股票 6xxxxx（含 688）、基金/ETF 5xxxxx、可转债 11xxxx；其余归深市
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
        print(f"❌ 腾讯降级源 [{code}] 获取失败: {e}")
    return None, None

def get_a_share_rsi(code, length=5, max_retries=3):
    """【A股通用·baostock主源+腾讯降级】适用于股票、可转债和 ETF，自带失败重试机制"""
    bs_code = _to_bs_code(code)
    start_date = (pd.Timestamp.now() - pd.Timedelta(days=100)).strftime("%Y-%m-%d")
    for attempt in range(max_retries):
        try:
            lg = bs.login()  # 匿名登录，无需账号密码
            if lg.error_code != '0':
                raise RuntimeError(f"baostock 登录失败: {lg.error_msg}")
            try:
                rs = bs.query_history_k_data_plus(
                    bs_code,
                    "date,close",
                    start_date=start_date,
                    frequency="d",
                    adjustflag="2",  # 前复权，与原 akshare qfq 对齐
                )
                if rs.error_code != '0':
                    raise RuntimeError(f"baostock 查询失败: {rs.error_msg}")
                df = rs.get_data()
            finally:
                bs.logout()
            if df is not None and not df.empty and 'close' in df.columns:
                df = df[df['close'] != '']  # 过滤停牌等空值行
                if len(df) >= length:
                    df['close_num'] = df['close'].astype(float)
                    df['rsi'] = ta.momentum.rsi(df['close_num'], window=length)
                    return df['rsi'].iloc[-1], df['close_num'].iloc[-1]
            # baostock 对可转债返回空数据（不在其覆盖范围）→ 腾讯接口降级
            return _get_tencent_rsi(code, length)
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1.5 * (attempt + 1))  # 失败后等待重试
            else:
                print(f"❌ 标的 [{code}] 请求多次失败: {e}")
    return _get_tencent_rsi(code, length)  # baostock 整体不可用时兜底

def send_feishu_msg(webhook, msg):
    if not webhook:
        print(f"未配置 Webhook，仅日志打印:\n{msg}")
        return
    requests.post(webhook, json={"msg_type": "text", "content": {"text": msg}}, timeout=10)

# ================= 主程序逻辑 =================

if __name__ == "__main__":
    FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")
    init_db()  # 首次运行自动建表，并播种初始数据
    config = load_instruments()
    
    if not config:
        print("停止运行：未能加载有效的配置文件。")
        exit(1)

    messages = []

    # 1. 加密货币监控
    crypto_list = config.get("crypto_okx", [])
    c_set = DEFAULT_SETTINGS["crypto"]
    for coin in crypto_list:
        # 新增 enabled 开关判断，默认为 True，若明确被禁用则跳过
        if not coin.get("enabled", True):
            continue

        symbol = coin.get("symbol")
        if not symbol:
            continue

        rsi, price = get_okx_rsi(symbol, c_set["interval"], c_set["period"])
        if rsi is not None and price is not None:
            print(f"✅ [Crypto] {symbol} 现价: ${price:.2f}, RSI({c_set['period']}): {rsi:.2f}")
            if rsi < c_set["rsi_low"]:
                messages.append(f"🚨 【{symbol} 超卖】现价 ${price:.2f}，1H RSI: {rsi:.2f} (低于 {c_set['rsi_low']})")
            elif rsi > c_set["rsi_high"]:
                messages.append(f"⚠️ 【{symbol} 超买】现价 ${price:.2f}，1H RSI: {rsi:.2f} (高于 {c_set['rsi_high']})")

    # 2. 可转债监控
    cb_list = config.get("convertible_bonds", [])
    b_set = DEFAULT_SETTINGS["bond"]
    for item in cb_list:
        # 新增 enabled 开关判断
        if not item.get("enabled", True):
            continue

        code = str(item.get("code"))
        cfg_name = item.get("name", code)

        rsi, price = get_a_share_rsi(code, length=b_set["period"])
        if rsi is not None and price is not None:
            print(f"✅ [可转债] {cfg_name}({code}) 现价: {price:.2f}, RSI({b_set['period']}): {rsi:.2f}")
            if rsi < b_set["rsi_low"]:
                messages.append(f"🚨 【可转债 RSI 超卖】{cfg_name}({code}) 现价: {price:.2f} 元，RSI: {rsi:.2f} (低于 {b_set['rsi_low']})")
            elif rsi > b_set["rsi_high"]:
                messages.append(f"⚠️ 【可转债 RSI 超买】{cfg_name}({code}) 现价: {price:.2f} 元，RSI: {rsi:.2f} (高于 {b_set['rsi_high']})")
        
        time.sleep(random.uniform(0.8, 1.5))  # 随机延迟

    # 3. ETF 监控
    etf_list = config.get("etfs", [])
    e_set = DEFAULT_SETTINGS["etf"]
    for item in etf_list:
        # 新增 enabled 开关判断
        if not item.get("enabled", True):
            continue

        code = str(item.get("code"))
        cfg_name = item.get("name", code)

        rsi, price = get_a_share_rsi(code, length=e_set["period"])
        if rsi is not None and price is not None:
            print(f"✅ [ETF] {cfg_name}({code}) 现价: {price:.3f}, RSI({e_set['period']}): {rsi:.2f}")
            if rsi < e_set["rsi_low"]:
                messages.append(f"🚨 【ETF RSI 超卖】{cfg_name}({code}) 现价: {price:.3f} 元，RSI: {rsi:.2f} (低于 {e_set['rsi_low']})")
            elif rsi > e_set["rsi_high"]:
                messages.append(f"⚠️ 【ETF RSI 超买】{cfg_name}({code}) 现价: {price:.3f} 元，RSI: {rsi:.2f} (高于 {e_set['rsi_high']})")

        time.sleep(random.uniform(0.8, 1.5))  # 随机延迟

    # 4. 发送推送
    if messages:
        full_msg = "\n\n".join(messages)
        send_feishu_msg(FEISHU_WEBHOOK, full_msg)
    else:
        print("所有标的 RSI 均处于正常区间，不触发推送。")
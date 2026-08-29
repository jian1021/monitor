import os
import json
import time
import random
import requests
import pandas as pd
import ta
import baostock as bs
from db import  get_db_client,load_instruments
import monitor_meteora_pump
from send_feishu_msg import send_feishu_msg
import traceback
import monitor_rsi
from config import FEISHU_WEBHOOK
from monitor_meteora_pump import run_pump_strategy_monitor
headers = {'User-Agent': 'Mozilla/5.0'}





# ================= 主程序逻辑 =================
if __name__ == "__main__":
    import traceback
    
  
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
    c_set = monitor_rsi.DEFAULT_SETTINGS["crypto"]

    for coin in crypto_list:
        if not coin.get("enabled", True):
            continue
        symbol = coin.get("symbol")
        if not symbol:
            continue
        rsi, price = monitor_rsi.mget_okx_rsi(symbol, c_set["interval"], c_set["period"])
        if rsi is not None and price is not None:
            print(f"✅ [OKX] {symbol} 现价: ${price:.4f}, RSI({c_set['period']}): {rsi:.2f}")
            if rsi < c_set["rsi_low"]:
                messages.append(f"🚨 【{symbol} 超卖】现价 ${price:.4f}，1H RSI: {rsi:.2f} (低于 {c_set['rsi_low']})")
            elif rsi > c_set["rsi_high"]:
                messages.append(f"⚠️ 【{symbol} 超买】现价 ${price:.4f}，1H RSI: {rsi:.2f} (高于 {c_set['rsi_high']})")

    # 2. Meteora 流动池监控
    meteora_list = config.get("meteora", [])
    m_set = monitor_rsi.DEFAULT_SETTINGS["meteora"]
    for pool in meteora_list:
        if not pool.get("enabled", True):
            continue
        address = pool.get("code")
        pool_name = pool.get("name", address)
        if not address:
            continue
        rsi, price = monitor_rsi.get_meteora_rsi(address, m_set["timeframe"], m_set["aggregate"], m_set["period"])
        if rsi is not None and price is not None:
            print(f"✅ [Meteora] {pool_name} 现价: ${price:.6f}, RSI({m_set['period']}): {rsi:.2f}")
            if rsi < m_set["rsi_low"]:
                messages.append(f"🚨 【Meteora {pool_name} 超卖】现价 ${price:.6f}，1H RSI: {rsi:.2f} (低于 {m_set['rsi_low']})")
            elif rsi > m_set["rsi_high"]:
                messages.append(f"⚠️ 【Meteora {pool_name} 超买】现价 ${price:.6f}，1H RSI: {rsi:.2f} (高于 {m_set['rsi_high']})")
        time.sleep(random.uniform(1.0, 2.0))

    # 3. 可转债监控
    cb_list = config.get("convertible_bonds", [])
    b_set = monitor_rsi.DEFAULT_SETTINGS["bond"]
    for item in cb_list:
        if not item.get("enabled", True):
            continue
        code = str(item.get("code"))
        cfg_name = item.get("name", code)
        rsi, price = monitor_rsi.get_a_share_rsi(code, bs_login_ok, length=b_set["period"])
        if rsi is not None and price is not None:
            print(f"✅ [可转债] {cfg_name}({code}) 现价: {price:.2f}, RSI({b_set['period']}): {rsi:.2f}")
            if rsi < b_set["rsi_low"]:
                messages.append(f"🚨 【可转债 RSI 超卖】{cfg_name}({code}) 现价: {price:.2f} 元，RSI: {rsi:.2f} (低于 {b_set['rsi_low']})")
            elif rsi > b_set["rsi_high"]:
                messages.append(f"⚠️ 【可转债 RSI 超买】{cfg_name}({code}) 现价: {price:.2f} 元，RSI: {rsi:.2f} (高于 {b_set['rsi_high']})")
        time.sleep(random.uniform(0.8, 1.5))

    # 4. ETF 监控
    etf_list = config.get("etfs", [])
    e_set = monitor_rsi.DEFAULT_SETTINGS["etf"]
    for item in etf_list:
        if not item.get("enabled", True):
            continue
        code = str(item.get("code"))
        cfg_name = item.get("name", code)
        rsi, price = monitor_rsi.get_a_share_rsi(code, bs_login_ok, length=e_set["period"])
        if rsi is not None and price is not None:
            print(f"✅ [ETF] {cfg_name}({code}) 现价: {price:.3f}, RSI({e_set['period']}): {rsi:.2f}")
            if rsi < e_set["rsi_low"]:
                messages.append(f"🚨 【ETF RSI 超卖】{cfg_name}({code}) 现价: {price:.3f} 元，RSI: {rsi:.2f} (低于 {e_set['rsi_low']})")
            elif rsi > e_set["rsi_high"]:
                messages.append(f"⚠️ 【ETF RSI 超买】{cfg_name}({code}) 现价: {price:.3f} 元，RSI: {rsi:.2f} (高于 {e_set['rsi_high']})")
        time.sleep(random.uniform(0.8, 1.5))

    if bs_login_ok:
        bs.logout()

    # 5.发送RSI告警
    if messages:
        full_msg = "\n\n".join(messages)
        send_feishu_msg(FEISHU_WEBHOOK, full_msg)
    else:
        print("所有标的 RSI 均处于正常区间，不触发推送。")


    print("\n====== 开始执行 meteora pump 策略监控 ======")
    try:
        run_pump_strategy_monitor()
    except Exception as e:
        print(f"❌ run_pump_strategy_monitor() 发生异常：{e}")
        traceback.print_exc()

    print("\n✅ main.py 全部任务执行完毕")



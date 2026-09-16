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
import lp_position_alert
from config import FEISHU_WEBHOOK
from monitor_meteora_pump import run_pump_strategy_monitor
from monitor_robinhood_pump import run_monitor
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
    crypto_tf = monitor_rsi.timeframe_label(c_set["interval"])

    for coin in crypto_list:
        if not coin.get("enabled", True):
            continue
        symbol = coin.get("symbol")
        if not symbol:
            continue
        rsi, price = monitor_rsi.get_okx_rsi(symbol, c_set["interval"], c_set["period"])
        if rsi is not None and price is not None:
            print(f"✅ [OKX] {symbol} 现价: ${price:.4f}, {crypto_tf} RSI({c_set['period']}): {rsi:.2f}")
            if rsi < c_set["rsi_low"]:
                messages.append(f"🚨 【{symbol} 超卖】现价 ${price:.4f}，{crypto_tf} RSI: {rsi:.2f} (低于 {c_set['rsi_low']})")
            elif rsi > c_set["rsi_high"]:
                messages.append(f"⚠️ 【{symbol} 超买】现价 ${price:.4f}，{crypto_tf} RSI: {rsi:.2f} (高于 {c_set['rsi_high']})")

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
    bond_tf = monitor_rsi.timeframe_label(b_set["frequency"])
    for item in cb_list:
        if not item.get("enabled", True):
            continue
        code = str(item.get("code"))
        cfg_name = item.get("name", code)
        rsi, price = monitor_rsi.get_a_share_rsi(code, bs_login_ok, length=b_set["period"], frequency=b_set["frequency"])
        if rsi is not None and price is not None:
            print(f"✅ [可转债] {cfg_name}({code}) 现价: {price:.2f}, {bond_tf} RSI({b_set['period']}): {rsi:.2f}")
            if rsi < b_set["rsi_low"]:
                messages.append(f"🚨 【可转债 RSI 超卖】{cfg_name}({code}) 现价: {price:.2f} 元，{bond_tf} RSI: {rsi:.2f} (低于 {b_set['rsi_low']})")
            elif rsi > b_set["rsi_high"]:
                messages.append(f"⚠️ 【可转债 RSI 超买】{cfg_name}({code}) 现价: {price:.2f} 元，{bond_tf} RSI: {rsi:.2f} (高于 {b_set['rsi_high']})")
        time.sleep(random.uniform(0.8, 1.5))

    # 4. ETF 监控
    etf_list = config.get("etfs", [])
    e_set = monitor_rsi.DEFAULT_SETTINGS["etf"]
    etf_tf = monitor_rsi.timeframe_label(e_set["frequency"])
    for item in etf_list:
        if not item.get("enabled", True):
            continue
        code = str(item.get("code"))
        cfg_name = item.get("name", code)
        rsi, price = monitor_rsi.get_a_share_rsi(code, bs_login_ok, length=e_set["period"], frequency=e_set["frequency"])
        if rsi is not None and price is not None:
            print(f"✅ [ETF] {cfg_name}({code}) 现价: {price:.3f}, {etf_tf} RSI({e_set['period']}): {rsi:.2f}")
            if rsi < e_set["rsi_low"]:
                messages.append(f"🚨 【ETF RSI 超卖】{cfg_name}({code}) 现价: {price:.3f} 元，{etf_tf} RSI: {rsi:.2f} (低于 {e_set['rsi_low']})")
            elif rsi > e_set["rsi_high"]:
                messages.append(f"⚠️ 【ETF RSI 超买】{cfg_name}({code}) 现价: {price:.3f} 元，{etf_tf} RSI: {rsi:.2f} (高于 {e_set['rsi_high']})")
        time.sleep(random.uniform(0.8, 1.5))

    if bs_login_ok:
        bs.logout()

    # 5. 链上代币监控
    token_list = config.get("tokens", [])
    t_set = monitor_rsi.DEFAULT_SETTINGS["token"]
    token_tf = monitor_rsi.timeframe_label(t_set["resolution"])
    for item in token_list:
        if not item.get("enabled", True):
            continue
        code = str(item.get("code"))
        chain = item.get("chain") or "sol"
        cfg_name = item.get("name", code)
        rsi, price = monitor_rsi.get_token_rsi(
            chain, code, t_set["resolution"], t_set["period"])
        if rsi is not None and price is not None:
            short = code[:10]
            print(f"✅ [链上代币] {cfg_name}({short}...) {chain} 现价: {price:.8g}, "
                  f"{token_tf} RSI({t_set['period']}): {rsi:.2f}")
            if rsi < t_set["rsi_low"]:
                messages.append(f"🚨 【链上代币 RSI 超卖】{cfg_name}({short}...) 链 {chain} "
                                f"现价: {price:.8g}，{token_tf} RSI: {rsi:.2f} (低于 {t_set['rsi_low']})")
            elif rsi > t_set["rsi_high"]:
                messages.append(f"⚠️ 【链上代币 RSI 超买】{cfg_name}({short}...) 链 {chain} "
                                f"现价: {price:.8g}，{token_tf} RSI: {rsi:.2f} (高于 {t_set['rsi_high']})")
        time.sleep(random.uniform(0.8, 1.5))

    # 6.发送RSI告警
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

    print("\n====== 开始执行 RobinHood pump 策略监控 ======")
    try:
        run_monitor()
    except Exception as e:
        print(f"❌ run_monitor() 发生异常：{e}")
        traceback.print_exc()

    print("\n====== 开始执行 LP 仓位 / 池子价格告警监控 ======")
    try:
        lp_position_alert.ensure_table()
        lp_position_alert.run_once()
    except Exception as e:
        print(f"❌ lp_position_alert 发生异常：{e}")
        traceback.print_exc()

    print("\n✅ main.py 全部任务执行完毕")



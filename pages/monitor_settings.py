# -*- coding: utf-8 -*-
"""监控模块启停与执行间隔设置。"""
import streamlit as st

from db import (
    DEFAULT_INTERVALS,
    get_module_intervals,
    get_module_settings,
    update_module_interval,
    update_module_setting,
)


MODULE_LABELS = {
    "rsi": "📊 RSI 监控",
    "crypto": "🪙 加密货币",
    "onchain_token": "🔗 链上代币",
    "meteora_pump": "☄️ Meteora pump 策略监控",
    "robinhood_pump": "🎰 RobinHood pump 策略监控",
    "lp_alert": "💧 LP 仓位 / 池子价格告警",
}

st.set_page_config(page_title="监控模块设置", page_icon="🎛️", layout="wide")
st.title("🎛️ 监控模块设置")
st.caption("集中管理各监控模块的启停状态与执行间隔，修改会实时写入数据库。")

st.markdown("##### 🎛️ 监控模块启停")
module_settings = get_module_settings()
cols = st.columns(len(MODULE_LABELS))
for idx, (mod_key, mod_label) in enumerate(MODULE_LABELS.items()):
    with cols[idx]:
        current = module_settings.get(mod_key, True)
        enabled = st.toggle(mod_label, value=current, key=f"mod_{mod_key}")
        if enabled != current:
            update_module_setting(mod_key, enabled)
            st.rerun()

st.divider()
st.markdown("##### ⏱️ 监控模块执行间隔（分钟）")
intervals = get_module_intervals(DEFAULT_INTERVALS)
int_cols = st.columns(len(MODULE_LABELS))
for idx, (mod_key, mod_label) in enumerate(MODULE_LABELS.items()):
    with int_cols[idx]:
        current_min = int(intervals.get(mod_key, DEFAULT_INTERVALS.get(mod_key, 5)))
        new_min = st.number_input(
            mod_label,
            min_value=1,
            max_value=10080,
            value=current_min,
            step=5,
            key=f"interval_{mod_key}",
        )
        if new_min != current_min:
            update_module_interval(mod_key, new_min)
            st.rerun()

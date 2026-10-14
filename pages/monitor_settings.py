# -*- coding: utf-8 -*-
"""监控模块启停与执行间隔设置。"""
import streamlit as st

from app.infrastructure.db.intervals import (
    DEFAULT_INTERVALS,
    get_module_intervals,
    update_module_interval,
)
from app.infrastructure.db.module_settings import (
    get_module_settings,
    update_module_setting,
)
from app.infrastructure.db.navigation import (
    NAVIGATION_GROUPS,
    get_navigation_order,
    save_navigation_order,
)


MODULE_LABELS = {
    "rsi": "📊 RSI 监控",
    "crypto": "🪙 加密货币",
    "onchain_token": "🔗 链上代币",
    "meme_underval": "🧲 Meme 低估监控",
    "meme_rsi_batch": "📡 批量 Meme 超买超卖",
    "robinhood_pump": "🎰 RobinHood pump 策略监控",
    "lp_alert": "💧 LP 仓位 / 池子价格告警",
    "rhpools": "🛰️ Robinhood Pools 观测站",
    "lp_apr": "📐 LP 区间 APR",
    "fee_screen": "💰 高手续费池筛选",
}

NAVIGATION_LABELS = {
    "meteora_scanner": "Meteora 全池扫描", "fee_screen": "Robinhood 全池扫描",
    "dashboard": "监控标的管理", "mainstream_crypto": "主流加密货币",
    "discover_lp": "Meme 低估监控", "lp_bands": "LP 区间可视化",
    "price_monitor": "价格监控", "pool_simulator": "池子价格模拟",
    "token_distribution": "代币分布图", "lp_position_alert": "LP 仓位告警",
    "robinhood_pools": "Robinhood Pools 观测站", "lp_apr": "LP 区间 APR",
    "exit_strategy": "退出策略", "onchain_token": "链上代币",
    "meme_rsi_batch": "批量 Meme 超买超卖", "monitor_settings": "监控模块设置",
    "logout": "退出登录",
}

st.set_page_config(page_title="监控模块设置", page_icon="🎛️", layout="wide")
st.title("🎛️ 监控模块设置")
st.caption("管理监控模块的启停、执行间隔和侧边栏顺序。")

if "sidebar_order_draft" not in st.session_state:
    st.session_state.sidebar_order_draft = get_navigation_order()
if st.session_state.pop("sidebar_order_saved", False):
    st.success("侧边栏顺序已保存并生效。")

st.markdown("##### 🧭 侧边栏模块顺序")
st.caption("选择分组和模块后用上移、下移调整；保存后侧边栏会立即按新顺序显示。")
group_col, module_col, up_col, down_col = st.columns([2, 3, 1, 1])
selected_group = group_col.selectbox("导航分组", list(NAVIGATION_GROUPS), key="navigation_group")
group_order = st.session_state.sidebar_order_draft[selected_group]
selected_module = module_col.selectbox(
    "模块", group_order,
    format_func=lambda key: NAVIGATION_LABELS.get(key, key),
    key="navigation_module",
)
module_index = group_order.index(selected_module)
if up_col.button("↑ 上移", disabled=module_index == 0, use_container_width=True):
    group_order[module_index - 1], group_order[module_index] = group_order[module_index], group_order[module_index - 1]
    st.session_state.sidebar_order_draft[selected_group] = group_order
    st.rerun()
if down_col.button("↓ 下移", disabled=module_index == len(group_order) - 1, use_container_width=True):
    group_order[module_index + 1], group_order[module_index] = group_order[module_index], group_order[module_index + 1]
    st.session_state.sidebar_order_draft[selected_group] = group_order
    st.rerun()
if st.button("💾 保存侧边栏顺序", type="primary"):
    if save_navigation_order(st.session_state.sidebar_order_draft):
        st.session_state.sidebar_order_saved = True
        st.rerun()
    st.error("保存失败，请确认数据库连接已配置。")

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

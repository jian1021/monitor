# -*- coding: utf-8 -*-
"""侧边栏模块顺序设置。"""
import streamlit as st

from app.infrastructure.db.navigation import (
    NAVIGATION_GROUPS,
    get_navigation_order,
    save_navigation_order,
)


NAVIGATION_LABELS = {
    "onchain_hub": "链上代币与全池扫描",
    "dashboard": "监控标的管理", "mainstream_crypto": "主流加密货币",
    "discover_lp": "Meme 低估监控", "lp_bands": "LP 区间可视化",
    "price_monitor": "价格监控", "pool_simulator": "池子价格模拟",
    "token_distribution": "代币分布图", "lp_position_alert": "LP 仓位告警",
    "lp_apr": "LP 区间 APR",
    "exit_strategy": "退出策略",
    "monitor_settings": "监控模块设置",
    "logout": "退出登录",
}

st.set_page_config(page_title="监控模块设置", page_icon="🎛️", layout="wide")
st.title("🎛️ 监控模块设置")
st.caption("管理侧边栏模块顺序；各模块的后台启停和执行间隔已移至对应页面。")

if "sidebar_order_draft" not in st.session_state:
    st.session_state.sidebar_order_draft = get_navigation_order()
if st.session_state.pop("sidebar_order_saved", False):
    st.success("侧边栏顺序已保存并生效。")

st.subheader("🧭 侧边栏模块顺序")
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
if up_col.button("↑ 上移", disabled=module_index == 0, width="stretch"):
    group_order[module_index - 1], group_order[module_index] = group_order[module_index], group_order[module_index - 1]
    st.session_state.sidebar_order_draft[selected_group] = group_order
    st.rerun()
if down_col.button("↓ 下移", disabled=module_index == len(group_order) - 1, width="stretch"):
    group_order[module_index + 1], group_order[module_index] = group_order[module_index], group_order[module_index + 1]
    st.session_state.sidebar_order_draft[selected_group] = group_order
    st.rerun()
if st.button("💾 保存侧边栏顺序", type="primary"):
    if save_navigation_order(st.session_state.sidebar_order_draft):
        st.session_state.sidebar_order_saved = True
        st.rerun()
    st.error("保存失败，请确认数据库连接已配置。")

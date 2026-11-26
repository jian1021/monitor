# -*- coding: utf-8 -*-
"""侧边栏模块顺序设置。"""
import pandas as pd
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
    "lp_range": "LP 区间宽度",
    "exit_strategy": "退出策略",
    "monitor_settings": "监控模块设置",
    "logout": "退出登录",
}

st.set_page_config(page_title="监控模块设置", page_icon="🎛️", layout="wide")
st.title("🎛️ 侧边栏顺序")
st.caption("改「顺序」列的数字后保存，侧边栏立即按新顺序显示。各模块的后台启停和间隔在对应页面顶部设置。")

order = get_navigation_order()
edited_groups = {}
cols = st.columns(len(NAVIGATION_GROUPS))
for col, group in zip(cols, NAVIGATION_GROUPS):
    with col:
        st.subheader(group)
        table = pd.DataFrame({"key": order[group],
                              "模块": [NAVIGATION_LABELS.get(k, k) for k in order[group]],
                              "顺序": list(range(1, len(order[group]) + 1))})
        edited_groups[group] = st.data_editor(
            table, key=f"nav_order_{group}", hide_index=True, width="stretch", disabled=["模块"],
            column_config={"key": None,
                           "顺序": st.column_config.NumberColumn(min_value=1, step=1, width="small")},
        )

if st.button("💾 保存侧边栏顺序", type="primary"):
    new_order = {g: df.sort_values(["顺序", "模块"], kind="stable")["key"].tolist()
                 for g, df in edited_groups.items()}
    if save_navigation_order(new_order):
        for g in NAVIGATION_GROUPS:
            st.session_state.pop(f"nav_order_{g}", None)
        st.toast("侧边栏顺序已保存", icon="💾")
        st.rerun()
    st.error("保存失败，请确认数据库连接已配置。")

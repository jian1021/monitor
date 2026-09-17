# -*- coding: utf-8 -*-
"""pages/st_mainstream_crypto.py — 主流加密货币监控标的配置"""
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

from asset_config import ASSET_TYPE_MAP
from asset_config import add_new_asset, batch_update_status_by_type, delete_asset
from asset_config import ensure_asset_schema, fetch_all_assets, update_asset_status

st.set_page_config(
    page_title="主流加密货币 · 监控标的",
    page_icon="🪙",
    layout="wide",
)

FOCUS_TYPES = ["crypto"]

st.title("⚙️ 监控标的配置管理")
st.caption("🔎 当前页面专注 **主流加密货币** 的增删改查与启用/禁用控制；其他资产类别请前往对应页面配置。")


def render_crypto_adder():
    with st.form("add_crypto_form", clear_on_submit=True):
        new_code = st.text_input(
            "标的代码", placeholder="例如: BTC-USDT 或 SOL-USDT",
            help="OKX 交易对格式，例如 BTC-USDT",
        )
        new_name = st.text_input(
            "标的名称 (可选)", placeholder="例如: 比特币",
            help="仅用于展示，留空则取代码",
        )
        submitted = st.form_submit_button("添加主流加密货币", type="primary")
        if submitted and new_code.strip():
            if add_new_asset("crypto", new_code, new_name or new_code):
                st.success(f"✅ 已添加主流加密货币: {new_code.strip()}")
                st.rerun()


render_crypto_adder()
st.divider()

df = fetch_all_assets()
focus_df = df[df["asset_type"].isin(FOCUS_TYPES)] if not df.empty else df

for type_key in FOCUS_TYPES:
    sub_df = focus_df[focus_df["asset_type"] == type_key]
    type_label = ASSET_TYPE_MAP.get(type_key, type_key)

    st.markdown(f"##### {type_label} 列表（{len(sub_df)} 条）")

    if sub_df.empty:
        st.caption(f"该类别 [{type_label}] 下暂无标的资产。")
        continue

    col_all, col_none = st.columns(2)
    if col_all.button("✅ 全选启用", key=f"enable_all_{type_key}"):
        batch_update_status_by_type(type_key, True)
        st.rerun()
    if col_none.button("⏸️ 全部停用", key=f"disable_all_{type_key}"):
        batch_update_status_by_type(type_key, False)
        st.rerun()

    for _, row in sub_df.iterrows():
        c1, c2, c3, c4, c5 = st.columns([3, 2, 2, 2, 1])
        c1.write(f"**{row['name']}** ({row['code']})")
        c2.write(f"ID: {row['id']}")
        c3.write("✅ 启用" if row["enabled"] else "⛔ 停用")
        if c4.button("切换启用/停用", key=f"toggle_{type_key}_{row['id']}"):
            update_asset_status(row["id"], not row["enabled"])
            st.rerun()
        if c5.button("🗑️", key=f"delete_{type_key}_{row['id']}"):
            delete_asset(row["id"])
            st.rerun()
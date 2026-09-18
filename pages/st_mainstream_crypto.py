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
from db import reset_asset_alert
from asset_grid import render_asset_grid
try:
    from dex_client import search_okx_symbols
except ImportError:
    search_okx_symbols = None

CRYPTO_TIMEFRAMES = {"1W": "周线", "1D": "日线", "4H": "4H", "1H": "1H", "15m": "15分钟"}

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
        if search_okx_symbols is not None and st.form_submit_button("🔍 搜索 OKX 交易对", key="crypto_search",
                                   use_container_width=True, type="secondary"):
            with st.spinner("正在搜索 ..."):
                st.session_state["crypto_candidates"] = search_okx_symbols(new_code or "BTC")
        elif search_okx_symbols is None and st.form_submit_button("🔍 搜索 OKX 交易对", key="crypto_search",
                                   use_container_width=True, type="secondary"):
            st.warning("⚠️ 搜索功能暂时不可用，请直接输入交易对名称添加。")

        candidates = st.session_state.get("crypto_candidates") or []
        if candidates:
            picked = st.selectbox("选择要添加的交易对", options=[c["symbol"] for c in candidates],
                                  key="crypto_pick")
            if picked and not new_code.strip():
                new_code = picked

        new_name = st.text_input(
            "标的名称 (可选)", placeholder="例如: 比特币",
            help="仅用于展示，留空则取代码",
        )
        new_timeframe = st.selectbox("时间级别", options=list(CRYPTO_TIMEFRAMES.keys()),
                                     format_func=lambda x: CRYPTO_TIMEFRAMES[x])
        submitted = st.form_submit_button("添加主流加密货币", type="primary")
        if submitted and new_code.strip():
            if add_new_asset("crypto", new_code, new_name or new_code, timeframe=new_timeframe):
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

    table = sub_df[["name", "code", "timeframe", "id", "enabled", "alarm_active", "last_alert_at"]].rename(columns={
        "name": "名称", "code": "交易对", "timeframe": "时间级别", "id": "ID",
        "enabled": "启用", "alarm_active": "报警中", "last_alert_at": "最近报警时间",
    })
    render_asset_grid(table, key=f"crypto_table_{type_key}", reset_asset_alert=reset_asset_alert,
                      delete_asset=delete_asset,
                      update_asset_status=update_asset_status)

# -*- coding: utf-8 -*-
"""pages/st_onchain_token.py — 链上代币监控标的配置"""
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

from asset_config import ASSET_TYPE_MAP
from asset_config import add_new_asset, batch_update_status_by_type, delete_asset
from asset_config import ensure_asset_schema, fetch_all_assets, update_asset_status
from asset_config import stale_module_names, token_candidates, format_candidate
from asset_config import TOKEN_CHAINS, CHAIN_LABELS
from db import get_db_client

TOKEN_TIMEFRAMES = {"1d": "日线", "4h": "4H", "1h": "1H"}
TIMEFRAME_LABELS = {v: k for k, v in TOKEN_TIMEFRAMES.items()}

st.set_page_config(
    page_title="链上代币 · 监控标的",
    page_icon="🔗",
    layout="wide",
)

FOCUS_TYPES = ["token"]

st.title("⚙️ 监控标的配置管理")
st.caption("🔎 当前页面专注 **链上代币** 的添加与管理（按公链搜索或直接粘合约地址）；其他资产类别请前往对应页面配置。")

_missing = stale_module_names()
if _missing:
    st.warning("⚠️ 检测到当前运行的模块是旧版（缺失：" + "、".join(_missing) +
               "），链上代币等新功能不可用。请在 Streamlit Cloud 上重启 / 重新部署后再试。")


def render_token_adder():
    col_chain, col_query = st.columns([1.2, 3])
    tok_chain = col_chain.selectbox(
        "所属公链", options=TOKEN_CHAINS,
        format_func=lambda x: CHAIN_LABELS.get(x, x), key="tok_chain")
    tok_timeframe = col_chain.selectbox(
        "时间级别", options=list(TOKEN_TIMEFRAMES.keys()),
        format_func=lambda x: TOKEN_TIMEFRAMES[x], key="tok_tf")
    tok_query = col_query.text_input(
        "代币名称或合约地址", key="tok_query",
        placeholder="例如 PENGU，或直接粘合约地址")

    if st.button("🔍 解析", key="tok_resolve"):
        if not tok_query.strip():
            st.warning("⚠️ 请先填写名称或地址")
        else:
            with st.spinner("正在搜索 ..."):
                st.session_state["tok_candidates"] = token_candidates(tok_chain, tok_query)

    candidates = st.session_state.get("tok_candidates") or []
    if not candidates:
        return
    if not any(c.get("found", True) for c in candidates):
        st.warning("⚠️ 没查到这个地址的信息，请确认所属公链与合约地址是否正确"
                   "（仍可继续添加，但价格监控可能取不到数据）。")
    labels = {c["address"]: format_candidate(c) for c in candidates}
    picked = st.selectbox("选择要监控的代币", options=list(labels),
                          format_func=lambda a: labels[a], key="tok_pick")
    if st.button("✅ 添加该代币", key="tok_add", type="primary"):
        chosen = next(c for c in candidates if c["address"] == picked)
        final_name = chosen.get("symbol") or chosen.get("name") or picked[:10]
        if add_new_asset("token", chosen["address"], final_name, tok_chain, tok_timeframe):
            st.session_state.pop("tok_candidates", None)
            st.success(f"✅ 已添加: {final_name}（{picked[:10]}...）")
            st.rerun()


render_token_adder()
st.divider()

ensure_asset_schema()
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

    col_tf1, col_tf2 = st.columns(2)
    with col_tf1:
        if st.button("🔄 批量切换为日线", key=f"batch_daily_{type_key}"):
            client = get_db_client()
            if client:
                try:
                    client.execute(
                        "UPDATE asset_config SET timeframe = '1d' WHERE asset_type = 'token' AND timeframe != '1d'"
                    )
                    st.success("✅ 已将所有链上代币时间级别更新为日线")
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ 更新失败: {e}")
                finally:
                    client.close()
    with col_tf2:
        if st.button("🔄 批量切换为1H", key=f"batch_1h_{type_key}"):
            client = get_db_client()
            if client:
                try:
                    client.execute(
                        "UPDATE asset_config SET timeframe = '1h' WHERE asset_type = 'token' AND timeframe != '1h'"
                    )
                    st.success("✅ 已将所有链上代币时间级别更新为1H")
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ 更新失败: {e}")
                finally:
                    client.close()

    for _, row in sub_df.iterrows():
        c1, c2, c3, c4, c5, c6 = st.columns([2.5, 1.5, 1, 1.5, 1, 1])
        with c1: st.caption(f"**{row['name']}** ({row['code']})")
        raw_tf = row.get('timeframe', '—')
        with c2: st.caption(f"⏱ {TIMEFRAME_LABELS.get(raw_tf, raw_tf)}")
        with c3: st.caption(f"ID: {row['id']}")
        chain_label = CHAIN_LABELS.get(row["chain"], row["chain"])
        with c4: st.caption(f"🔗 {chain_label}")
        with c5:
            if st.button("切换启用/停用", key=f"toggle_{type_key}_{row['id']}"):
                update_asset_status(row["id"], not row["enabled"])
                st.rerun()
        with c6:
            if st.button("🗑️", key=f"delete_{type_key}_{row['id']}"):
                delete_asset(row["id"])
                st.rerun()

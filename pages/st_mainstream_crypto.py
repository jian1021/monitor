# -*- coding: utf-8 -*-
"""pages/st_mainstream_crypto.py — 主流加密货币（OKX）RSI 监控标的

一行搜索添加；一张可编辑表格完成启停 / 改时间级别 / 删除，统一保存。
"""
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import streamlit as st

from app.infrastructure.db.assets import set_asset_timeframe
from app.infrastructure.market_data.okx import search_okx_symbols
from app.interfaces.asset_config import (
    add_new_asset, delete_asset, ensure_asset_schema, fetch_all_assets, update_asset_status,
)
from app.interfaces.monitor_controls import render_monitor_controls
from app.interfaces.reload_guard import fresh_timefmt

to_local_display = fresh_timefmt()

CRYPTO_TIMEFRAMES = {"1W": "周线", "1D": "日线", "4H": "4H", "1H": "1H", "15m": "15分钟"}
TF_BY_LABEL = {v: k for k, v in CRYPTO_TIMEFRAMES.items()}

st.set_page_config(page_title="主流加密货币", page_icon="🪙", layout="wide")
st.title("🪙 主流加密货币")
st.caption("OKX 交易对 RSI(3) 越界时推送飞书。")
render_monitor_controls("crypto", "主流加密货币")

# ---------------- 搜索 + 添加 ----------------
with st.form("crypto_search_form", border=False):
    c_q, c_tf, c_go = st.columns([4, 1, 0.8], vertical_alignment="bottom")
    query = c_q.text_input("交易对", placeholder="BTC、SOL，或直接写 BTC-USDT")
    tf = c_tf.selectbox("时间级别", list(CRYPTO_TIMEFRAMES), format_func=CRYPTO_TIMEFRAMES.get)
    go = c_go.form_submit_button("🔍 搜索", type="primary", width="stretch")

if go:
    with st.spinner("搜索 OKX 交易对 …"):
        st.session_state["crypto_candidates"] = {"tf": tf, "items": search_okx_symbols(query.strip() or "BTC")}

found = st.session_state.get("crypto_candidates")
if found is not None:
    items = found["items"] or []
    if not items:
        st.warning("没有搜到匹配的 OKX 交易对，换个关键词试试。")
    else:
        p_l, p_n, p_r = st.columns([3, 2, 0.8], vertical_alignment="bottom")
        picked = p_l.selectbox(f"搜索结果（{len(items)}）", [c["symbol"] for c in items], key="crypto_pick")
        name = p_n.text_input("名称（可选）", placeholder="比特币")
        if p_r.button("➕ 添加", type="primary", width="stretch"):
            if add_new_asset("crypto", picked, name.strip() or picked, timeframe=found["tf"]):
                st.session_state.pop("crypto_candidates", None)
                st.toast(f"已添加 {picked}（{CRYPTO_TIMEFRAMES[found['tf']]}）", icon="✅")
                st.rerun()
            else:
                st.warning("该交易对已在监控列表中。")

# ---------------- 列表 ----------------
ensure_asset_schema()
df = fetch_all_assets()
df = df[df["asset_type"] == "crypto"] if not df.empty else df
if df.empty:
    st.info("还没有监控的交易对，在上方搜索后添加。")
    st.stop()

st.subheader(f"监控列表 {int(df['enabled'].sum())}/{len(df)} 启用 · {int(df['alarm_active'].sum())} 报警中")
view = pd.DataFrame({
    "ID": df["id"].astype(int),
    "启用": df["enabled"].astype(bool),
    "名称": df["name"],
    "交易对": df["code"],
    "时间级别": df["timeframe"].map(lambda v: CRYPTO_TIMEFRAMES.get(v, CRYPTO_TIMEFRAMES["1W"])),
    "报警中": df["alarm_active"].map({True: "🔔", False: ""}),
    "最近报警": df["last_alert_at"].apply(to_local_display),
    "删除": False,
})
edited = st.data_editor(
    view, key="crypto_editor", hide_index=True, width="stretch",
    disabled=["名称", "交易对", "报警中", "最近报警"],
    column_config={
        "ID": None,
        "启用": st.column_config.CheckboxColumn(width="small"),
        "时间级别": st.column_config.SelectboxColumn(options=list(CRYPTO_TIMEFRAMES.values()),
                                                 required=True, width="small"),
        "报警中": st.column_config.TextColumn(width="small"),
        "删除": st.column_config.CheckboxColumn(width="small"),
    },
)

orig, new = view.set_index("ID"), edited.set_index("ID")
changes = []
for rid in new.index:
    a, b = orig.loc[rid], new.loc[rid]
    if b["删除"]:
        changes.append(("delete", rid, None))
        continue
    if bool(a["启用"]) != bool(b["启用"]):
        changes.append(("enabled", rid, bool(b["启用"])))
    if a["时间级别"] != b["时间级别"]:
        changes.append(("tf", rid, TF_BY_LABEL[b["时间级别"]]))

tools_l, tools_r = st.columns([4, 1], vertical_alignment="center")
with tools_l.popover("批量操作"):
    b1, b2 = st.columns(2)
    if b1.button("全部启用", width="stretch"):
        for rid in orig.index:
            update_asset_status(int(rid), True)
        st.rerun()
    if b2.button("全部停用", width="stretch"):
        for rid in orig.index:
            update_asset_status(int(rid), False)
        st.rerun()
    tf_all = st.segmented_control("全部改为", list(CRYPTO_TIMEFRAMES), format_func=CRYPTO_TIMEFRAMES.get)
    if tf_all and st.button(f"应用到全部（{CRYPTO_TIMEFRAMES[tf_all]}）", width="stretch"):
        for rid in orig.index:
            set_asset_timeframe(int(rid), tf_all)
        st.rerun()

if changes:
    n_del = sum(1 for c in changes if c[0] == "delete")
    tools_l.info(f"{len(changes)} 项未保存" + (f"，含删除 {n_del} 条（不可恢复）" if n_del else ""))
    if tools_r.button("💾 保存修改", type="primary", width="stretch"):
        failed = []
        for kind, rid, val in changes:
            rid = int(rid)
            ok = (delete_asset(rid) if kind == "delete" else
                  update_asset_status(rid, val) if kind == "enabled" else
                  set_asset_timeframe(rid, val))
            if not ok:
                failed.append(str(rid))
        st.session_state.pop("crypto_editor", None)
        if failed:
            st.error("以下 ID 保存失败：" + "、".join(failed))
        else:
            st.toast("已保存", icon="💾")
            st.rerun()

# -*- coding: utf-8 -*-
"""pages/st_onchain_token.py — 链上代币 RSI 监控标的

一行搜索添加；一张可编辑表格完成启停 / 改时间级别 / 删除，统一保存。
"""
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import streamlit as st

from app.infrastructure.db.assets import set_asset_timeframe
from app.interfaces.asset_config import (
    CHAIN_LABELS, TOKEN_CHAINS, add_new_asset, delete_asset, ensure_asset_schema,
    fetch_all_assets, format_candidate, token_candidates, update_asset_status,
)
from app.interfaces.monitor_controls import render_monitor_controls
from app.interfaces.reload_guard import fresh_timefmt

to_local_display = fresh_timefmt()

TOKEN_TIMEFRAMES = {"1d": "日线", "4h": "4H", "1h": "1H"}
TF_BY_LABEL = {v: k for k, v in TOKEN_TIMEFRAMES.items()}


def _render_adder() -> None:
    with st.form("tok_search_form", border=False):
        c_chain, c_tf, c_q, c_btn = st.columns([1.3, 0.8, 3.5, 0.8], vertical_alignment="bottom")
        chain = c_chain.selectbox("公链", TOKEN_CHAINS, format_func=lambda x: CHAIN_LABELS.get(x, x),
                                  key="tok_chain")
        tf = c_tf.selectbox("级别", list(TOKEN_TIMEFRAMES), format_func=TOKEN_TIMEFRAMES.get,
                            key="tok_tf")
        query = c_q.text_input("代币名称或合约地址", placeholder="例如 PENGU，或粘贴合约地址",
                               key="tok_query")
        go = c_btn.form_submit_button("🔍 搜索", type="primary", width="stretch")

    if go:
        if not query.strip():
            st.warning("请先填写名称或地址")
        else:
            with st.spinner("搜索中 ..."):
                st.session_state["tok_candidates"] = {
                    "chain": chain, "tf": tf, "items": token_candidates(chain, query)}

    found = st.session_state.get("tok_candidates")
    if not found:
        return
    items = found["items"] or []
    if not items:
        st.warning("没有搜到结果，请换个关键词或核对公链。")
        return
    if not any(c.get("found", True) for c in items):
        st.warning("没查到该地址的行情，请核对公链与地址（仍可添加，但可能取不到数据）。")

    labels = {c["address"]: format_candidate(c) for c in items}
    c_pick, c_add = st.columns([5, 1], vertical_alignment="bottom")
    picked = c_pick.selectbox(f"搜索结果（{len(items)}）", list(labels), format_func=labels.get,
                              key="tok_pick")
    if c_add.button("➕ 添加", type="primary", width="stretch", key="tok_add"):
        chosen = next(c for c in items if c["address"] == picked)
        name = chosen.get("symbol") or chosen.get("name") or picked[:10]
        if add_new_asset("token", chosen["address"], name, found["chain"], found["tf"]):
            st.session_state.pop("tok_candidates", None)
            st.toast(f"已添加 {name}（{CHAIN_LABELS.get(found['chain'])} · "
                     f"{TOKEN_TIMEFRAMES[found['tf']]}）", icon="✅")
            st.rerun()
        else:
            st.warning("该代币已在监控列表中。")


def _render_table() -> None:
    ensure_asset_schema()
    df = fetch_all_assets()
    df = df[df["asset_type"] == "token"] if not df.empty else df
    if df.empty:
        st.info("还没有链上代币，在上方搜索后添加。")
        return

    st.subheader(f"监控列表 {int(df['enabled'].sum())}/{len(df)} 启用 · "
                 f"{int(df['alarm_active'].sum())} 报警中")

    # 链上代币跨多链，必须显示「所属链」，否则分不清同名跨链代币；标签统一取自 CHAIN_LABELS
    view = pd.DataFrame({
        "ID": df["id"].astype(int),
        "启用": df["enabled"].astype(bool),
        "名称": df["name"],
        "所属链": df["chain"].apply(lambda c: CHAIN_LABELS.get(c, c or "sol")),
        "时间级别": df["timeframe"].map(TOKEN_TIMEFRAMES).fillna("日线"),
        "报警中": df["alarm_active"].map({True: "🔔", False: ""}),
        "最近报警": df["last_alert_at"].apply(to_local_display),
        "合约地址": df["code"],
        "删除": False,
    })
    edited = st.data_editor(
        view, key="token_editor", hide_index=True, width="stretch",
        disabled=["名称", "所属链", "报警中", "最近报警", "合约地址"],
        column_config={
            "ID": None,
            "启用": st.column_config.CheckboxColumn(width="small"),
            "时间级别": st.column_config.SelectboxColumn(options=list(TOKEN_TIMEFRAMES.values()),
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
        tf_all = st.segmented_control("全部改为", list(TOKEN_TIMEFRAMES), format_func=TOKEN_TIMEFRAMES.get)
        if tf_all and st.button(f"应用到全部（{TOKEN_TIMEFRAMES[tf_all]}）", width="stretch"):
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
            st.session_state.pop("token_editor", None)
            if failed:
                st.error("以下 ID 保存失败：" + "、".join(failed))
            else:
                st.toast("已保存", icon="💾")
                st.rerun()


def render_page(embedded: bool = False) -> None:
    if not embedded:
        st.title("🔗 链上代币")
    st.caption("RSI(3) 跌破 10 报「超卖」，回升上穿 10 再报一次「反转突破」。")
    render_monitor_controls("onchain_token", "链上代币")
    _render_adder()
    _render_table()


if __name__ == "__main__":
    st.set_page_config(page_title="链上代币", page_icon="🔗", layout="wide")
    render_page()

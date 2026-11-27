"""超卖监控 · 标的管理（Meteora 池 / 可转债 / ETF）。

资产增删改查统一走 asset_config（唯一实现），本页只负责渲染：
一行添加；每个分类一张可编辑表格（启用 / 删除），改完统一保存。
"""

import pandas as pd
import streamlit as st

from app.interfaces.asset_config import (
    CHAIN_LABELS,
    TOKEN_CHAINS,
    add_new_asset,
    delete_asset,
    ensure_asset_schema,
    fetch_all_assets,
    update_asset_status,
)
from app.interfaces.monitor_controls import render_monitor_controls
from app.interfaces.reload_guard import fresh_timefmt

to_local_display = fresh_timefmt()

ASSET_TYPE_MAP = {"meteora": "☄️ Meteora 池", "bond": "📈 可转债", "etf": "📊 ETF"}

st.set_page_config(page_title="超卖监控 · 标的管理", page_icon="⚙️", layout="wide")
st.title("⚙️ 超卖监控")
st.caption("RSI(3) 跌破 10 报「超卖」，回升上穿 10 再报一次「反转突破」。Meteora 池 / 可转债每个交易日收盘后检查，ETF 按周线检查。")
render_monitor_controls("rsi", "RSI 监控（Meteora 池 / 可转债）")
render_monitor_controls("etf_rsi", "ETF 周线 RSI 监控", webhook_editable=True)

ensure_asset_schema()
df = fetch_all_assets()

# ---------------- 添加 ----------------
with st.form("add_asset_form", clear_on_submit=True, border=False):
    c1, c2, c3, c4, c5 = st.columns([1.2, 1.2, 2.6, 1.6, 0.8], vertical_alignment="bottom")
    new_type = c1.selectbox("类别", list(ASSET_TYPE_MAP), format_func=ASSET_TYPE_MAP.get)
    chain = c2.selectbox("所属链（Meteora）", TOKEN_CHAINS, format_func=lambda k: CHAIN_LABELS[k])
    new_code = c3.text_input("代码 / 池子地址", placeholder="可转债 113052 · ETF 159967 · Meteora 池地址")
    new_name = c4.text_input("名称（可选）")
    if c5.form_submit_button("➕ 添加", type="primary", width="stretch"):
        if not new_code.strip():
            st.warning("请输入代码或池子地址。")
        elif add_new_asset(new_type, new_code, new_name, chain=chain):
            st.toast(f"已添加：{new_name.strip() or new_code.strip()}", icon="✅")
            st.rerun()
        else:
            st.warning("该标的已存在（同类型同代码），未重复添加。")

# ---------------- 列表 ----------------
total = len(df)
s_l, s_r = st.columns([3, 1])
search_text = s_l.text_input("搜索", placeholder="名称、代码或地址片段", label_visibility="collapsed")
status_filter = s_r.selectbox("状态", ["全部", "运行中", "已暂停", "报警中"], label_visibility="collapsed")


def _tab_label(key: str) -> str:
    sub = df[df["asset_type"] == key] if total else df
    on = int(sub["enabled"].fillna(False).astype(bool).sum()) if len(sub) else 0
    return f"{ASSET_TYPE_MAP[key]} {on}/{len(sub)}"


tabs = st.tabs([_tab_label(k) for k in ASSET_TYPE_MAP], on_change="rerun", key="dashboard_asset_tabs")
for tab, type_key in zip(tabs, ASSET_TYPE_MAP):
    if not tab.open:
        continue
    with tab:
        cat = df[df["asset_type"] == type_key].copy() if total else df.copy()
        if cat.empty:
            st.info("这个分类还没有标的，在上方添加。")
            continue
        view = cat
        if search_text.strip():
            needle = search_text.strip().casefold()
            view = view[view["name"].fillna("").astype(str).str.casefold().str.contains(needle, regex=False)
                        | view["code"].fillna("").astype(str).str.casefold().str.contains(needle, regex=False)]
        enabled_mask = view["enabled"].fillna(False).astype(bool)
        if status_filter == "运行中":
            view = view[enabled_mask]
        elif status_filter == "已暂停":
            view = view[~enabled_mask]
        elif status_filter == "报警中":
            view = view[view["alarm_active"].fillna(False).astype(bool)]
        if view.empty:
            st.info("没有符合搜索 / 状态条件的标的。")
            continue

        table = pd.DataFrame({
            "ID": view["id"].astype(int),
            "运行": view["enabled"].fillna(False).astype(bool),
            "名称": view["name"],
            "代码 / 地址": view["code"],
            "报警中": view["alarm_active"].fillna(False).map({True: "🔔", False: ""}),
            "最近报警": view["last_alert_at"].apply(to_local_display),   # 库里存 UTC
            "删除": False,
        })
        edited = st.data_editor(
            table, key=f"asset_editor_{type_key}", hide_index=True, width="stretch",
            disabled=["名称", "代码 / 地址", "报警中", "最近报警"],
            column_config={
                "ID": None,
                "运行": st.column_config.CheckboxColumn(width="small",
                                                      help="取消勾选后不再参与后台监控"),
                "报警中": st.column_config.TextColumn(width="small"),
                "删除": st.column_config.CheckboxColumn(width="small"),
            },
        )

        orig, new = table.set_index("ID"), edited.set_index("ID")
        changes = []
        for rid in new.index:
            if new.loc[rid, "删除"]:
                changes.append(("delete", int(rid), None))
            elif bool(orig.loc[rid, "运行"]) != bool(new.loc[rid, "运行"]):
                changes.append(("enabled", int(rid), bool(new.loc[rid, "运行"])))
        if changes:
            n_del = sum(1 for c in changes if c[0] == "delete")
            i_l, i_r = st.columns([4, 1], vertical_alignment="center")
            i_l.info(f"{len(changes)} 项未保存" + (f"，含删除 {n_del} 条（不可恢复）" if n_del else ""))
            if i_r.button("💾 保存修改", type="primary", width="stretch", key=f"save_{type_key}"):
                failed = [rid for kind, rid, val in changes
                          if not (delete_asset(rid) if kind == "delete" else update_asset_status(rid, val))]
                st.session_state.pop(f"asset_editor_{type_key}", None)
                if failed:
                    st.error(f"有 {len(failed)} 条保存失败，请检查数据库连接。")
                else:
                    st.toast("已保存", icon="💾")
                    st.rerun()

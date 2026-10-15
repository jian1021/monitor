"""监控标的配置管理页（超卖监控）。

资产增删改查统一走 asset_config（唯一实现），本页只负责渲染。
"""

import streamlit as st
from app.interfaces.monitor_controls import render_monitor_controls

from app.interfaces.asset_config import (
    CHAIN_LABELS,
    TOKEN_CHAINS,
    add_new_asset,
    delete_asset,
    ensure_asset_schema,
    fetch_all_assets,
    stale_module_names,
    update_asset_status,
)


# =============================================================================
# 4. Streamlit 界面构建
# =============================================================================
st.set_page_config(page_title="超卖监控 · 标的管理", page_icon="⚙️", layout="wide")
st.title("⚙️ 超卖监控 · 标的管理")
st.caption("🔎 当前页面专注 **Meteora 池 / 可转债 / ETF** 的添加与管理；其他资产类别请前往对应页面配置。")
render_monitor_controls("rsi", "RSI 监控")

_missing = stale_module_names()
if _missing:
    st.warning("⚠️ 检测到当前运行的模块是旧版（缺失：" + "、".join(_missing) +
               "），链上代币等新功能不可用。请在 Streamlit Cloud 上重启 / 重新部署后再试。")

# 明确定义资产映射（加密货币和链上代币已移至独立页面）
ASSET_TYPE_MAP = {
    "meteora": "☄️ Meteora 池",
    "bond": "📈 可转债",
    "etf": "📊 ETF",
}

# --- 读取标的 ---
ensure_asset_schema()
df = fetch_all_assets()

with st.container(border=True):
    st.subheader("➕ 添加监控标的")
    with st.form("add_asset_form", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns([1.1, 1.5, 2, 2])
        new_type = c1.selectbox(
            "资产类别", options=list(ASSET_TYPE_MAP),
            format_func=lambda key: ASSET_TYPE_MAP[key],
        )
        chain = c2.selectbox("所属链", TOKEN_CHAINS,
                             format_func=lambda key: CHAIN_LABELS[key])
        code_placeholder = "池子地址" if new_type == "meteora" else "代码或合约地址"
        new_code = c3.text_input("标的代码 / 地址", placeholder=code_placeholder)
        new_name = c4.text_input("显示名称（可选）", placeholder="未填写时使用代码")
        submitted = st.form_submit_button("添加标的", type="primary")
        if submitted:
            if not new_code.strip():
                st.warning("请输入标的代码或地址。")
            elif add_new_asset(new_type, new_code, new_name, chain=chain):
                st.toast(f"已添加：{new_name.strip() or new_code.strip()}", icon="✅")
                st.rerun()

total_count = len(df)
enabled_count = int(df["enabled"].fillna(False).astype(bool).sum()) if total_count else 0
metric_cols = st.columns(4)
metric_cols[0].metric("全部标的", total_count)
metric_cols[1].metric("运行中", enabled_count)
metric_cols[2].metric("已暂停", total_count - enabled_count)
metric_cols[3].metric("报警中", int(df["alarm_active"].fillna(False).astype(bool).sum()) if total_count else 0)

st.divider()
f1, f2 = st.columns([3, 1])
search_text = f1.text_input("搜索标的", placeholder="输入名称、代码或地址片段", label_visibility="collapsed")
status_filter = f2.selectbox("状态", ["全部状态", "运行中", "已暂停"], label_visibility="collapsed")

tabs = st.tabs(
    [ASSET_TYPE_MAP[key] for key in ASSET_TYPE_MAP],
    on_change="rerun",
    key="dashboard_asset_tabs",
)
for tab, (type_key, type_label) in zip(tabs, ASSET_TYPE_MAP.items()):
    if not tab.open:
        continue
    with tab:
        category_df = df[df["asset_type"] == type_key].copy() if total_count else df.copy()
        filtered = category_df
        if search_text.strip():
            needle = search_text.strip().casefold()
            mask = (filtered["name"].fillna("").astype(str).str.casefold().str.contains(needle, regex=False)
                    | filtered["code"].fillna("").astype(str).str.casefold().str.contains(needle, regex=False))
            filtered = filtered[mask]
        if status_filter == "运行中":
            filtered = filtered[filtered["enabled"].fillna(False).astype(bool)]
        elif status_filter == "已暂停":
            filtered = filtered[~filtered["enabled"].fillna(False).astype(bool)]

        active_count = int(category_df["enabled"].fillna(False).astype(bool).sum()) if len(category_df) else 0
        head, count = st.columns([3, 2])
        head.markdown(f"##### {type_label}")
        count.caption(f"{active_count}/{len(category_df)} 运行中 · 当前显示 {len(filtered)} 条")
        if category_df.empty:
            st.info("这个分类还没有标的。可以使用上方「添加监控标的」开始配置。")
            continue
        if filtered.empty:
            st.info("没有符合当前搜索和状态条件的标的。")
            continue

        display_df = filtered[["id", "name", "code", "chain", "timeframe", "enabled", "alarm_active", "last_alert_at"]].copy()
        display_df = display_df.rename(columns={
            "id": "ID", "name": "名称", "code": "池子地址" if type_key == "meteora" else "代码 / 地址",
            "chain": "链", "timeframe": "周期", "enabled": "运行", "alarm_active": "报警中",
            "last_alert_at": "最近报警",
        })
        with st.form(f"save_assets_{type_key}"):
            edited = st.data_editor(
                display_df, hide_index=True, width="stretch", num_rows="fixed",
                disabled=[column for column in display_df.columns if column != "运行"],
                column_config={
                    "ID": st.column_config.NumberColumn(width="small"),
                    "名称": st.column_config.TextColumn(width="medium"),
                    "池子地址": st.column_config.TextColumn(width="large"),
                    "代码 / 地址": st.column_config.TextColumn(width="large"),
                    "链": st.column_config.TextColumn(width="small"),
                    "周期": st.column_config.TextColumn(width="small"),
                    "运行": st.column_config.CheckboxColumn(help="取消勾选后，该标的不再参与后台监控"),
                    "报警中": st.column_config.CheckboxColumn(disabled=True),
                    "最近报警": st.column_config.TextColumn(width="medium"),
                },
                key=f"asset_editor_{type_key}",
            )
            save_status = st.form_submit_button("保存运行状态", type="primary")
        if save_status:
            original = {int(row["id"]): bool(row["enabled"]) for row in filtered.to_dict("records")}
            updates = 0
            failed = 0
            for row in edited.to_dict("records"):
                asset_id = int(row["ID"])
                new_enabled = bool(row["运行"])
                if original.get(asset_id) != new_enabled:
                    if update_asset_status(asset_id, new_enabled):
                        updates += 1
                    else:
                        failed += 1
            if failed:
                st.error(f"有 {failed} 条状态保存失败，请检查数据库连接。")
            else:
                st.toast(f"运行状态已保存（更新 {updates} 条）。", icon="✅")
                st.rerun()

        with st.expander("删除标的", expanded=False):
            delete_options = category_df.to_dict("records")
            selected_id = st.selectbox(
                "选择要删除的标的", options=[int(row["id"]) for row in delete_options],
                format_func=lambda asset_id: next(
                    f"{row.get('name') or row.get('code')} · {row.get('code')}"
                    for row in delete_options if int(row["id"]) == asset_id
                ), key=f"delete_select_{type_key}",
            )
            confirm_delete = st.checkbox("我确认永久删除该标的", key=f"confirm_delete_{type_key}")
            if st.button("永久删除", type="secondary", disabled=not confirm_delete,
                         key=f"delete_asset_{type_key}"):
                if delete_asset(selected_id):
                    st.session_state[f"confirm_delete_{type_key}"] = False
                    st.toast("标的已删除。", icon="🗑️")
                    st.rerun()
                st.error("删除失败，请检查数据库连接。")

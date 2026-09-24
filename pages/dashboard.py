"""监控标的配置管理页（超卖监控）。

资产增删改查统一走 asset_config（唯一实现），本页只负责渲染。
"""

import streamlit as st

from asset_config import (
    add_new_asset,
    batch_update_status_by_type,
    delete_asset,
    ensure_asset_schema,
    fetch_all_assets,
    stale_module_names,
    update_asset_status,
)
from asset_grid import render_asset_grid


# =============================================================================
# 4. Streamlit 界面构建
# =============================================================================
st.title("⚙️ 监控标的配置管理")
st.caption("在此页面配置需监控的资产标的及其启用/禁用状态，支持一键批量修改，变更实时同步至 Turso 数据库。")

st.divider()

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

# --- 侧边栏：添加新标的与登出 ---
with st.sidebar:
    st.write("👤 **当前登录：管理员**")
    if st.button("🚪 退出登录"):
        st.session_state["authenticated"] = False
        st.rerun()
    st.divider()

    st.header("➕ 添加新标的")
    with st.form("add_asset_form", clear_on_submit=True):
        new_type = st.selectbox(
            "资产类别",
            options=list(ASSET_TYPE_MAP.keys()),
            format_func=lambda x: ASSET_TYPE_MAP[x]
        )

        new_chain = "sol"
        if new_type == "meteora":
            code_label = "标的代码 / 池子 Address"
            code_placeholder = "例如: Cgnuirsk5dQ9..."
            name_placeholder = "例如: TROLL-SOL"
        else:
            code_label = "标的代码 / 池子 Address"
            code_placeholder = "例如: BTC-USDT 或 113052"
            name_placeholder = "例如: 兴业转债"

        new_code = st.text_input(code_label, placeholder=code_placeholder)
        new_name = st.text_input("标的名称 (可选)", placeholder=name_placeholder)

        submitted = st.form_submit_button("添加标的", type="primary")
        if submitted:
            if new_type == "token":
                st.warning("⚠️ 链上代币请在「🔗 链上代币」标签页里添加（需先核对确切合约）")
            elif not new_code.strip():
                st.warning("⚠️ 标的代码/池子地址不能为空！")
            else:
                if add_new_asset(new_type, new_code, new_name):
                    st.success(f"✅ 成功添加: {new_code}")
                    st.rerun()


# --- 主界面：按分类展示与编辑配置 ---
ensure_asset_schema()
df = fetch_all_assets()

if not df.empty:
    # 顶部统计信息
    total_count = len(df)
    enabled_count = len(df[df["enabled"] == True])

    col1, col2, col3 = st.columns(3)
    col1.metric("总标的数", total_count)
    col2.metric("已启用标的", enabled_count)
    col3.metric("已停用标的", total_count - enabled_count)

    st.divider()

# 使用 Tab 标签页区分各类资产
tabs = st.tabs([ASSET_TYPE_MAP[key] for key in ASSET_TYPE_MAP.keys()])

for tab, (type_key, type_label) in zip(tabs, ASSET_TYPE_MAP.items()):
    with tab:
        sub_df = df[df["asset_type"] == type_key] if not df.empty else df

        if sub_df.empty:
            st.caption(f"该类别 [{type_label}] 下暂无标的资产。")
            continue

        # 标题与批量操作同一行，避免控件散落
        head_left, head_right = st.columns([3, 2])
        head_left.markdown(f"##### {type_label} 列表（{len(sub_df)} 条）")
        with head_right:
            btn_all, btn_none = st.columns(2)
            if btn_all.button("✅ 全选", key=f"select_all_{type_key}",
                              use_container_width=True):
                if batch_update_status_by_type(type_key, True):
                    st.toast(f"已全部启用所有 {type_label}", icon="🎉")
                    st.rerun()
            if btn_none.button("🚫 全不选", key=f"deselect_all_{type_key}",
                               use_container_width=True):
                if batch_update_status_by_type(type_key, False):
                    st.toast(f"已全部禁用所有 {type_label}", icon="⏸️")
                    st.rerun()

        # 区分不同类别的列表字段头显示
        code_col_title = {"meteora": "池子 Address"}.get(
            type_key, "标的代码")

        grid_df = sub_df[["id", "name", "code", "chain", "enabled", "alarm_active", "last_alert_at"]].rename(columns={
            "id": "ID", "name": "名称", "code": code_col_title, "chain": "链",
            "enabled": "启用", "alarm_active": "报警中", "last_alert_at": "最近报警时间",
        })
        render_asset_grid(grid_df, key=f"editor_{type_key}",
                          delete_asset=delete_asset, update_asset_status=update_asset_status)

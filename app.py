import os
import pandas as pd
import streamlit as st
from libsql_client import create_client_sync

# 设置页面标题
st.set_page_config(page_title="标的资产配置管理", page_icon="⚙️", layout="wide")

# 1. 数据库凭据读取（优先读 st.secrets，兼容 os.getenv）
LIBSQL_URL = (
    st.secrets.get("TURSO_DATABASE_URL")
    or os.getenv("TURSO_DATABASE_URL")
    or os.getenv("LIBSQL_URL", "https://monitor-db-jian1021.aws-ap-northeast-1.turso.io")
)
LIBSQL_TOKEN = st.secrets.get("TURSO_AUTH_TOKEN") or os.getenv("TURSO_AUTH_TOKEN") or os.getenv("LIBSQL_TOKEN")


def get_db_client():
    if not LIBSQL_TOKEN:
        st.error("❌ 未检测到数据库 Token，请配置 TURSO_AUTH_TOKEN / LIBSQL_TOKEN！")
        return None
    return create_client_sync(url=LIBSQL_URL, auth_token=LIBSQL_TOKEN)


# -----------------------------------------------------------------------------
# 2. 数据库 CRUD 操作函数
# -----------------------------------------------------------------------------
def fetch_all_assets():
    """读取所有标的资产"""
    client = get_db_client()
    if not client:
        return pd.DataFrame()
    try:
        rs = client.execute(
            "SELECT id, asset_type, code, name, enabled, created_at FROM asset_config ORDER BY id ASC"
        )
        # 将结果转换为 DataFrame 方便展示和编辑
        data = []
        for row in rs.rows:
            data.append({
                "id": row[0],
                "asset_type": row[1],
                "code": row[2],
                "name": row[3],
                "enabled": bool(row[4]),  # 转为 bool 供 Streamlit Checkbox 编辑
                "created_at": row[5],
            })
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"❌ 读取标的列表失败: {e}")
        return pd.DataFrame()
    finally:
        client.close()


def update_asset_status(asset_id: int, enabled: bool):
    """更新单个标的的启用状态"""
    client = get_db_client()
    if not client:
        return False
    try:
        status_val = 1 if enabled else 0
        client.execute(
            "UPDATE asset_config SET enabled = ? WHERE id = ?",
            [status_val, asset_id]
        )
        return True
    except Exception as e:
        st.error(f"❌ 更新状态失败 (ID: {asset_id}): {e}")
        return False
    finally:
        client.close()


def add_new_asset(asset_type: str, code: str, name: str):
    """新增标的"""
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute(
            "INSERT INTO asset_config (asset_type, code, name, enabled) VALUES (?, ?, ?, 1)",
            [asset_type, code.strip(), name.strip() or code.strip()]
        )
        return True
    except Exception as e:
        st.error(f"❌ 添加标的失败: {e}")
        return False
    finally:
        client.close()


def delete_asset(asset_id: int):
    """删除标的"""
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute("DELETE FROM asset_config WHERE id = ?", [asset_id])
        return True
    except Exception as e:
        st.error(f"❌ 删除标的失败: {e}")
        return False
    finally:
        client.close()


# -----------------------------------------------------------------------------
# 3. Streamlit 界面构建
# -----------------------------------------------------------------------------
st.title("⚙️ 监控标的配置管理")
st.caption("在此页面配置需监控的资产标的及其启用/禁用状态，修改实时同步至 Turso 数据库。")

# 映射分类名称展示
ASSET_TYPE_MAP = {
    "crypto": "🪙 加密货币 (OKX)",
    "bond": "📈 可转债",
    "etf": "📊 ETF 基金",
}

# --- 侧边栏：添加新标的 ---
with st.sidebar:
    st.header("➕ 添加新标的")
    with st.form("add_asset_form", clear_on_submit=True):
        new_type = st.selectbox(
            "资产类别",
            options=list(ASSET_TYPE_MAP.keys()),
            format_func=lambda x: ASSET_TYPE_MAP[x]
        )
        new_code = st.text_input("标的代码 / Symbol", placeholder="例如: BTC-USDT 或 113052")
        new_name = st.text_input("标的名称 (可选)", placeholder="例如: 兴业转债")
        
        submitted = st.form_submit_button("添加标的", type="primary")
        if submitted:
            if not new_code.strip():
                st.warning("⚠️ 标的代码不能为空！")
            else:
                if add_new_asset(new_type, new_code, new_name):
                    st.success(f"✅ 成功添加: {new_code}")
                    st.rerun()

# --- 主界面：按分类展示与编辑配置 ---
df = fetch_all_assets()

if df.empty:
    st.info("ℹ️ 数据库中暂无标的配置或未查到数据。")
else:
    # 顶部统计信息
    total_count = len(df)
    enabled_count = len(df[df["enabled"] == True])
    
    col1, col2, col3 = st.columns(3)
    col1.metric("总标的数", total_count)
    col2.metric("已启用标的", enabled_count)
    col3.metric("已停用标的", total_count - enabled_count)

    st.divider()

    # 使用 Tab 标签页区分三大类资产
    tabs = st.tabs([ASSET_TYPE_MAP["crypto"], ASSET_TYPE_MAP["bond"], ASSET_TYPE_MAP["etf"]])

    for tab, (type_key, type_label) in zip(tabs, ASSET_TYPE_MAP.items()):
        with tab:
            sub_df = df[df["asset_type"] == type_key]
            
            if sub_df.empty:
                st.caption("该类别下暂无标的资产。")
                continue

            st.markdown(f"##### {type_label} 列表")
            
            # 使用 Data Editor 实现表格内直接开关切换 (Streamlit 1.19+)
            # 这里的 disabled 列表定义不可修改的列，enabled 列允许直接打钩
            edited_df = st.data_editor(
                sub_df,
                column_config={
                    "id": st.column_config.NumberColumn("ID", disabled=True, width="small"),
                    "asset_type": None,  # 隐藏字段
                    "code": st.column_config.TextColumn("标的代码", disabled=True),
                    "name": st.column_config.TextColumn("标的名称", disabled=True),
                    "enabled": st.column_config.CheckboxColumn("是否启用 🟢/🔴", default=True),
                    "created_at": st.column_config.DatetimeColumn("添加时间", disabled=True, format="YYYY-MM-DD HH:mm"),
                },
                hide_index=True,
                use_container_width=True,
                key=f"editor_{type_key}"
            )

            # 监测表格中 enabled 状态的变化并批量提交修改
            if st.button("💾 保存状态变更", key=f"save_{type_key}", type="primary"):
                changes_count = 0
                for _, row in edited_df.iterrows():
                    # 对比原始状态是否有改变
                    orig_row = sub_df[sub_df["id"] == row["id"]].iloc[0]
                    if row["enabled"] != orig_row["enabled"]:
                        update_asset_status(row["id"], row["enabled"])
                        changes_count += 1
                
                if changes_count > 0:
                    st.success(f"✅ 成功更新 {changes_count} 条标的状态！")
                    st.rerun()
                else:
                    st.info("ℹ️ 未检测到状态变化。")

            # 下方提供一个快捷删除区
            with st.expander("🗑️ 删除该分类下的标的"):
                del_id = st.selectbox(
                    "选择要删除的标的",
                    options=sub_df["id"].tolist(),
                    format_func=lambda x: f"ID:{x} - {sub_df[sub_df['id']==x]['code'].values[0]} ({sub_df[sub_df['id']==x]['name'].values[0]})",
                    key=f"del_select_{type_key}"
                )
                if st.button("确认彻底删除", key=f"del_btn_{type_key}"):
                    if delete_asset(del_id):
                        st.success("✅ 删除成功！")
                        st.rerun()
import os
import pandas as pd
import streamlit as st
from libsql_client import create_client_sync
from config import FEISHU_WEBHOOK
from db import get_db_client
import db as _db
import dex_client as _dex

# 链上代币：可用名称搜索，也可直接填合约地址
TOKEN_CHAINS = ["sol", "bsc", "base", "eth", "robinhood", "arc", "stable"]
CHAIN_LABELS = {
    "sol": "Solana (SOL)", "bsc": "BNB Chain (BSC)", "base": "Base",
    "eth": "Ethereum (ETH)", "robinhood": "Robinhood (RH)",
    "arc": "ARC", "stable": "Stable",
}


def looks_like_address(text: str) -> bool:
    t = (text or "").strip()
    if t.startswith("0x") and len(t) == 42:
        return True
    return len(t) >= 32


def ensure_asset_schema():
    """调用 db.ensure_asset_schema；旧版 db 模块（Streamlit 会缓存已导入的模块）缺失时跳过."""
    fn = getattr(_db, "ensure_asset_schema", None)
    return fn() if callable(fn) else False


def stale_module_names() -> list:
    """本页依赖但当前模块里缺失的新函数（说明部署未更新）."""
    missing = []
    if not callable(getattr(_db, "ensure_asset_schema", None)):
        missing.append("db.ensure_asset_schema")
    if not callable(getattr(_dex, "search_tokens", None)):
        missing.append("dex_client.search_tokens")
    return missing


def token_candidates(chain: str, text: str) -> list:
    """地址直通为唯一候选（会补查名称/价格/市值）；名称则返回搜索结果列表。"""
    t = (text or "").strip()
    if looks_like_address(t):
        lookup = getattr(_dex, "lookup_token", None)
        if callable(lookup):
            return [lookup(chain, t)]
        return [{"address": t, "symbol": None, "name": None,
                 "price": None, "market_cap": None, "liquidity": None, "found": False}]
    fn = getattr(_dex, "search_tokens", None)
    return fn(chain, t) if callable(fn) else []


def format_candidate(c: dict) -> str:
    """候选展示文案：符号 · 名称 · 价格 · 市值 · 流动性 · 地址前缀."""
    parts = [f"{c.get('symbol') or '?'} · {c.get('name') or '未知'}"]
    if c.get("price") is not None:
        parts.append(f"价 {c['price']:.10g}")
    if c.get("market_cap"):
        parts.append(f"市值 {c['market_cap']:,.0f}")
    if c.get("liquidity"):
        parts.append(f"流动性 {c['liquidity']:,.0f}")
    parts.append(f"{c['address'][:10]}…")
    return " · ".join(parts)

# =============================================================================
# 1. 设置页面属性（全程序仅保留这一个）
# =============================================================================

# =============================================================================
# 3. 数据库连接与 CRUD 操作函数
# =============================================================================



def fetch_all_assets():
    """读取所有标的资产"""
    client = get_db_client()
    if not client:
        return pd.DataFrame()
    try:
        try:
            rs = client.execute(
                "SELECT id, asset_type, code, name, enabled, created_at, chain"
                " FROM asset_config ORDER BY id ASC"
            )
            has_chain = True
        except Exception:
            rs = client.execute(
                "SELECT id, asset_type, code, name, enabled, created_at"
                " FROM asset_config ORDER BY id ASC"
            )
            has_chain = False
        data = []
        for row in rs.rows:
            data.append({
                "id": row[0],
                "asset_type": row[1],
                "code": row[2],
                "name": row[3],
                "enabled": bool(row[4]),
                "created_at": row[5],
                "chain": (row[6] if has_chain and len(row) > 6 else None) or "sol",
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


def batch_update_status_by_type(asset_type: str, enabled: bool):
    """按分类一键批量修改启用/禁用状态"""
    client = get_db_client()
    if not client:
        return False
    try:
        status_val = 1 if enabled else 0
        client.execute(
            "UPDATE asset_config SET enabled = ? WHERE asset_type = ?",
            [status_val, asset_type]
        )
        return True
    except Exception as e:
        st.error(f"❌ 批量更新分类 [{asset_type}] 失败: {e}")
        return False
    finally:
        client.close()


def add_new_asset(asset_type: str, code: str, name: str, chain: str = None):
    """新增标的"""
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute(
            "INSERT INTO asset_config (asset_type, code, name, enabled, chain)"
            " VALUES (?, ?, ?, 1, ?)",
            [asset_type, code.strip(), name.strip() or code.strip(), chain or "sol"]
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


# =============================================================================
# 4. Streamlit 界面构建
# =============================================================================
st.title("⚙️ 监控标的配置管理")
st.caption("在此页面配置需监控的资产标的及其启用/禁用状态，支持一键批量修改，变更实时同步至 Turso 数据库。")

_missing = stale_module_names()
if _missing:
    st.warning("⚠️ 检测到当前运行的模块是旧版（缺失：" + "、".join(_missing) +
               "），链上代币等新功能不可用。请在 Streamlit Cloud 上重启 / 重新部署后再试。")

# 明确定义资产映射，包含对应数据库中的 'meteora'
ASSET_TYPE_MAP = {
    "crypto": "🪙 加密货币",
    "meteora": "☄️ Meteora 池",
    "bond": "📈 可转债",
    "etf": "📊 ETF",
    "token": "🔗 链上代币",
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

def render_token_adder():
    """链上代币的添加表单（放在链上代币标签页内，而不是侧边栏）."""
    st.markdown("##### ➕ 添加链上代币")
    st.caption("填名称会列出候选（同名代币多，请核对后选择）；直接粘合约地址则跳过搜索。")
    col_chain, col_query = st.columns([1.2, 3])
    tok_chain = col_chain.selectbox(
        "所属公链", options=TOKEN_CHAINS,
        format_func=lambda x: CHAIN_LABELS.get(x, x), key="tok_chain")
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
        if add_new_asset("token", chosen["address"], final_name, tok_chain):
            st.session_state.pop("tok_candidates", None)
            st.success(f"✅ 已添加: {final_name}（{picked[:10]}...）")
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

# 使用 Tab 标签页区分各类资产（即使暂无数据也渲染，否则链上代币没有添加入口）
tabs = st.tabs([ASSET_TYPE_MAP[key] for key in ASSET_TYPE_MAP.keys()])

for tab, (type_key, type_label) in zip(tabs, ASSET_TYPE_MAP.items()):
    with tab:
        if type_key == "token":
            render_token_adder()
            st.divider()

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
        code_col_title = {"meteora": "池子 Address", "token": "合约地址"}.get(
            type_key, "标的代码")

        edited_df = st.data_editor(
            sub_df,
            column_config={
                "id": st.column_config.NumberColumn("ID", disabled=True, width="small"),
                "asset_type": None,  # 隐藏字段
                "code": st.column_config.TextColumn(code_col_title, disabled=True),
                "chain": st.column_config.TextColumn("链", disabled=True, width="small"),
                "name": st.column_config.TextColumn("标的/交易对名称", disabled=True),
                "enabled": st.column_config.CheckboxColumn("是否启用 🟢/🔴", default=True),
                "created_at": st.column_config.DatetimeColumn("添加时间", disabled=True, format="YYYY-MM-DD HH:mm"),
            },
            hide_index=True,
            use_container_width=True,
            key=f"editor_{type_key}"
        )

        # 保存按钮只占左侧窄栏，不要横贯整页
        save_col, _ = st.columns([1, 4])
        if save_col.button("💾 保存状态", key=f"save_{type_key}", type="primary",
                           use_container_width=True):
            changes_count = 0
            for _, row in edited_df.iterrows():
                orig_row = sub_df[sub_df["id"] == row["id"]].iloc[0]
                if row["enabled"] != orig_row["enabled"]:
                    update_asset_status(row["id"], row["enabled"])
                    changes_count += 1

            if changes_count > 0:
                st.success(f"✅ 成功更新 {changes_count} 条标的状态！")
                st.rerun()
            else:
                st.info("ℹ️ 未检测到状态变化。")

        # 下方删除工具
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


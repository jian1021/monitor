"""价格监控配置页 (st_monitor_price)

功能:
  - 新增价格监控规则: 输入 token 地址 + 所属公链 + 目标价 + 方向
  - 查看规则列表 / 当前价格 / 最近检查时间 / 是否已触发
  - 启用/停用/删除规则
数据表: price_alert (Turso/libSQL)
"""
import os
import streamlit as st
import pandas as pd

from config import FEISHU_WEBHOOK
from db import get_db_client
from send_feishu_msg import send_feishu_msg

CHAIN_OPTIONS = ["sol", "bsc", "base", "eth", "robinhood", "arc", "stable"]
CHAIN_LABELS = {
    "sol": "Solana (SOL)",
    "bsc": "BNB Chain (BSC)",
    "base": "Base",
    "eth": "Ethereum (ETH)",
    "robinhood": "Robinhood (RH)",
    "arc": "ARC",
    "stable": "Stable",
}

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS price_alert (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    address       TEXT NOT NULL,
    chain         TEXT NOT NULL DEFAULT 'sol',
    symbol        TEXT,
    target_price  REAL NOT NULL,
    direction     TEXT NOT NULL DEFAULT 'gte',
    enabled       INTEGER NOT NULL DEFAULT 1,
    last_price    REAL,
    last_checked_at TEXT,
    alerted       INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_price_alert_enabled ON price_alert(enabled);
"""

# ============================================================
# 通用工具
# ============================================================
def get_client():
    return get_db_client()


def ensure_table():
    client = get_client()
    if not client:
        return False
    try:
        client.batch([CREATE_TABLE_SQL])
        return True
    except Exception as e:
        st.error(f"❌ 初始化 price_alert 表失败: {e}")
        return False
    finally:
        client.close()


# ============================================================
# CRUD
# ============================================================
def fetch_rules():
    """读取全部价格监控规则"""
    client = get_client()
    if not client:
        return pd.DataFrame()
    try:
        res = client.execute(
            "SELECT id, address, chain, symbol, target_price, direction, enabled,"
            " last_price, last_checked_at, alerted, created_at"
            " FROM price_alert ORDER BY id DESC"
        )
        data = []
        for row in res.rows:
            data.append({
                "id": row[0],
                "address": row[1],
                "chain": row[2],
                "symbol": row[3] or "",
                "target_price": row[4],
                "direction": row[5],
                "enabled": bool(row[6]),
                "last_price": row[7],
                "last_checked_at": row[8],
                "alerted": bool(row[9]),
                "created_at": row[10],
            })
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"❌ 读取规则失败: {e}")
        return pd.DataFrame()
    finally:
        client.close()


def add_rule(address: str, chain: str, target_price: float, direction: str, symbol: str):
    client = get_client()
    if not client:
        return False
    try:
        client.execute(
            "INSERT INTO price_alert (address, chain, symbol, target_price, direction, enabled)"
            " VALUES (?, ?, ?, ?, ?, 1)",
            [address.strip(), chain, symbol.strip(), target_price, direction],
        )
        return True
    except Exception as e:
        st.error(f"❌ 新增规则失败: {e}")
        return False
    finally:
        client.close()


def update_rule_status(rule_id: int, enabled: bool):
    client = get_client()
    if not client:
        return False
    try:
        client.execute(
            "UPDATE price_alert SET enabled = ? WHERE id = ?",
            [1 if enabled else 0, rule_id],
        )
        return True
    except Exception as e:
        st.error(f"❌ 更新规则状态失败: {e}")
        return False
    finally:
        client.close()


def delete_rule(rule_id: int):
    client = get_client()
    if not client:
        return False
    try:
        client.execute("DELETE FROM price_alert WHERE id = ?", [rule_id])
        return True
    except Exception as e:
        st.error(f"❌ 删除规则失败: {e}")
        return False
    finally:
        client.close()


def reset_alert(rule_id: int):
    """重置告警标志，允许再次触发推送"""
    client = get_client()
    if not client:
        return False
    try:
        client.execute("UPDATE price_alert SET alerted = 0 WHERE id = ?", [rule_id])
        return True
    except Exception as e:
        st.error(f"❌ 重置告警标志失败: {e}")
        return False
    finally:
        client.close()


# ============================================================
# 界面
# ============================================================
st.set_page_config(page_title="价格监控", page_icon="📈", layout="wide")
st.title("📈 价格监控")
st.caption("按 Token 地址 + 公链监控实时价格，到达目标价时通过飞书推送告警。")

if not ensure_table():
    st.stop()

# ---- 顶部告警通道状态 ----
if FEISHU_WEBHOOK:
    st.success("✅ 飞书告警通道已配置", icon="🔔")
else:
    st.warning("⚠️ 未配置 FEISHU_WEBHOOK，告警仅打印到控制台", icon="🔕")

if os.getenv("GMGN_PROXY"):
    st.caption(f"GMGN 代理: {os.getenv('GMGN_PROXY')}")

st.divider()

# ================= 新增规则表单 =================
with st.expander("➕ 新增价格监控规则", expanded=True):
    with st.form("add_price_rule_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            new_address = st.text_input(
                "Token 合约地址 *",
                placeholder="例如: EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                help="Solana / BSC / Base / ETH 上的 token 合约地址",
            )
            new_chain = st.selectbox(
                "所属公链",
                options=CHAIN_OPTIONS,
                format_func=lambda x: CHAIN_LABELS[x],
            )
        with c2:
            new_target = st.number_input(
                "目标价格 *",
                min_value=0.0,
                step=0.000001,
                format="%.8f",
                value=0.0,
            )
            new_direction = st.radio(
                "触发条件",
                options=["gte", "lte"],
                format_func=lambda x: "📈 价格 ≥ 目标价时报警" if x == "gte" else "📉 价格 ≤ 目标价时报警",
                horizontal=True,
            )
            new_symbol = st.text_input("代币符号 (可选)", placeholder="例如: USDC")

        submitted = st.form_submit_button("✅ 添加规则", type="primary")
        if submitted:
            if not new_address.strip() or len(new_address.strip()) < 20:
                st.warning("⚠️ 请输入有效的 Token 合约地址")
            elif new_target <= 0:
                st.warning("⚠️ 目标价格必须大于 0")
            else:
                if add_rule(new_address, new_chain, new_target, new_direction, new_symbol):
                    st.success(f"✅ 已添加规则: {new_address[:10]}... ({CHAIN_LABELS[new_chain]}) 目标 {new_target}")
                    st.rerun()

st.divider()

# ================= 现有规则列表 =================
df = fetch_rules()
if df.empty:
    st.info("ℹ️ 暂无价格监控规则，请在上方新增。")
    st.stop()

col1, col2, col3, col4 = st.columns(4)
col1.metric("总规则数", len(df))
col2.metric("启用中", len(df[df["enabled"] == True]))
col3.metric("已触发", len(df[df["alerted"] == True]))
col4.metric("待触发", len(df[(df["enabled"] == True) & (df["alerted"] == False)]))

st.divider()

# 展示用副本（隐藏内部字段 id/alerted）
view_df = df.copy()

# 状态列文本化
view_df["状态"] = view_df.apply(
    lambda r: "🟢 监控中" if (r["enabled"] and not r["alerted"])
    else ("🔔 已触发" if (r["enabled"] and r["alerted"]) else "⏸️ 已停用"),
    axis=1,
)
view_df["方向"] = view_df["direction"].map(
    {"gte": "📈 ≥", "lte": "📉 ≤"}
)
view_df["目标价"] = view_df["target_price"]
view_df["当前价"] = view_df["last_price"].fillna("-")
view_df["最近检查"] = view_df["last_checked_at"].fillna("—")

# 列筛选
show_cols = {
    "状态": "状态",
    "chain": "公链",
    "symbol": "符号",
    "address": "地址",
    "方向": "方向",
    "目标价": "目标价",
    "当前价": "当前价",
    "最近检查": "最近检查",
    "created_at": "创建时间",
}
columns_cfg = {
    "id": None,
    "chain": st.column_config.TextColumn("公链", disabled=True, width="small"),
    "symbol": st.column_config.TextColumn("符号", disabled=True),
    "address": st.column_config.TextColumn("地址", disabled=True, width="large"),
    "direction": None,
    "target_price": st.column_config.NumberColumn("目标价", disabled=True, format="%.8f"),
    "enabled": None,
    "alerted": None,
    "last_price": st.column_config.NumberColumn("当前价", disabled=True, format="%.8f"),
    "last_checked_at": st.column_config.TextColumn("最近检查", disabled=True),
    "created_at": st.column_config.DatetimeColumn("创建时间", disabled=True, format="YYYY-MM-DD HH:mm"),
}

st.dataframe(
    view_df[list(show_cols.keys())].rename(columns=show_cols),
    column_config=columns_cfg,
    width="stretch",
    hide_index=True,
)

st.divider()

# ================= 管理操作 =================
st.markdown("#### 🛠️ 规则管理")

# 手动触发一次查价（调 gmgn-cli）
with st.expander("🔍 立即检查选中规则价格"):
    check_sel = st.selectbox(
        "选择要检查的规则",
        options=df["id"].tolist(),
        format_func=lambda i: f"[{i}] {df.loc[df['id']==i,'symbol'].iloc[0] or df.loc[df['id']==i,'address'].iloc[0][:16]}... ({df.loc[df['id']==i,'chain'].iloc[0]})",
    )
    if st.button("检查价格"):
        import subprocess
        import json as _json
        import os as _os

        check_id = int(check_sel)
        rule = df[df["id"] == check_id].iloc[0]
        cmd = ["gmgn-cli", "token", "info", "--chain", rule["chain"], "--address", rule["address"], "--raw"]
        env = dict(_os.environ)
        proxy = _os.getenv("GMGN_PROXY", "http://127.0.0.1:7897")
        if proxy:
            env["HTTPS_PROXY"] = proxy
            env["HTTP_PROXY"] = proxy
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env, shell=os.name == "nt")
            if proc.returncode != 0:
                st.error(f"gmgn-cli 调用失败: {proc.stderr.strip()[:200]}")
            else:
                data = _json.loads(proc.stdout)
                price = None
                pobj = data.get("price") or {}
                raw = pobj.get("price")
                try:
                    price = float(raw)
                except (TypeError, ValueError):
                    pass
                if price is not None:
                    sym = data.get("symbol") or rule["symbol"]
                    st.success(f"✅ {sym} 当前价格: {price}")
                    # 回写
                    client = get_client()
                    if client:
                        try:
                            client.execute(
                                "UPDATE price_alert SET last_price = ?, last_checked_at = datetime('now') WHERE id = ?",
                                [price, check_id],
                            )
                        finally:
                            client.close()
                else:
                    st.error(f"无法解析价格字段: {raw}")
        except Exception as e:
            st.error(f"检查失败: {e}")

with st.expander("⚙️ 启用 / 停用 / 重置 / 删除", expanded=True):
    manage_sel = st.selectbox(
        "选择规则 ID",
        options=df["id"].tolist(),
        key="manage_sel",
        format_func=lambda i: f"[{i}] {df.loc[df['id']==i,'symbol'].iloc[0] or df.loc[df['id']==i,'address'].iloc[0][:16]}...",
    )
    manage_id = int(manage_sel)
    rule_row = df[df["id"] == manage_id].iloc[0]

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        if rule_row["enabled"]:
            if st.button("⏸️ 停用", width=stretch):
                if update_rule_status(manage_id, False):
                    st.toast("已停用", icon="⏸️")
                    st.rerun()
        else:
            if st.button("▶️ 启用", width=stretch):
                if update_rule_status(manage_id, True):
                    st.toast("已启用", icon="▶️")
                    st.rerun()
    with m2:
        if st.button("🔓 重置告警", width=stretch, help="重置后将可再次触发推送"):
            if reset_alert(manage_id):
                st.toast("告警标志已重置", icon="🔓")
                st.rerun()
    with m3:
        st.caption(f"当前: {'已触发' if rule_row['alerted'] else '未触发'} / 目标 {'≥' if rule_row['direction']=='gte' else '≤'} {rule_row['target_price']}")
    with m4:
        with st.popover("🗑️ 删除规则", width=stretch):
            st.write("⚠️ 删除后不可恢复，确认删除该规则？")
            if st.button("确认删除", type="primary"):
                if delete_rule(manage_id):
                    st.toast("已删除", icon="🗑️")
                    st.rerun()

# ================= 运行说明 =================
st.divider()
with st.expander("📖 运行说明"):
    st.markdown(
        """
**后台监控脚本 (独立运行):**

```bash
# GitHub Actions 定时任务 (每小时, 见 .github/workflows) 或手动单轮:
python monitor_price.py

# 本地常驻轮询 (每 60 秒):
python monitor_price.py --loop
python monitor_price.py --loop --interval 300   # 自定义间隔(秒)
```

**告警去重**：同一规则首次触发后标记为「已触发」，避免重复轰炸；
需再次提醒可点「重置告警」。

**网络要求**：gmgn-cli 需走代理连通 GMGN（HTTPS_PROXY），
本地默认 `http://127.0.0.1:7897`，可用 `GMGN_PROXY` 环境变量覆盖。
"""
    )
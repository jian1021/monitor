"""人工选池的退出策略配置。"""

import pandas as pd
import streamlit as st

from app.application.meteora_scanner import config as scanner_config
from app.application.meteora_scanner.db import Database
from app.application.meteora_scanner.filters import apply_filters
from app.infrastructure.db.exit_strategy import load_exit_strategy, save_exit_strategy

st.set_page_config(page_title="退出策略", page_icon="🚨", layout="wide")
st.title("🚨 退出策略")
st.caption("从扫描候选中人工勾选池子，或手动录入 Robinhood Chain 池地址。任一告警条件命中时由主监控循环推送飞书。")

@st.cache_resource(show_spinner=False)
def scanner_db():
    return Database(scanner_config.DB_PATH)

saved = load_exit_strategy()
pool_rows = scanner_db().load_pools(scanner_config.SUPPORTED_POOL_TYPES)
filters = {**scanner_config.DEFAULT_FILTERS,
           **(scanner_db().get_setting("filters") or {})}
eligible = [r for r in apply_filters(pool_rows, filters)
            if r.get("pool_address") and r.get("passed")]
pool_by_address = {r["pool_address"]: r for r in eligible}
pool_labels = {
    r["pool_address"]: f"{r.get('token_x_symbol') or '?'} / {r.get('token_y_symbol') or '?'} · {r.get('pool_address')}"
    for r in eligible
}

st.subheader("选择扫描池")
selected_sol = st.multiselect(
    "Solana（Meteora）候选池",
    options=list(pool_labels),
    default=[p["address"] for p in saved.get("pools", [])
             if p.get("chain") == "sol" and p.get("address") in pool_labels],
    format_func=lambda addr: pool_labels.get(addr, addr),
    help="仅列出当前 Meteora 扫描筛选通过的池子。",
)
with st.expander("手动添加 Robinhood Chain 池"):
    rh_address = st.text_input("Robinhood 池地址", placeholder="0x…")
    rh_label = st.text_input("池名称（可选）", placeholder="例如 TOKEN / ETH")

st.subheader("退出告警条件")
current_by_key = {(p.get("chain"), p.get("address")): p for p in saved.get("pools", [])}
selected_pools = []
for address in selected_sol:
    prev = current_by_key.get(("sol", address), {})
    left, mid, right = st.columns([3, 1.5, 1])
    left.write(pool_labels[address])
    floor = mid.number_input("价格下限", min_value=0.0, value=float(prev.get("floor_price") or 0.0),
                             format="%.10g", key=f"floor_sol_{address}")
    enabled = right.checkbox("监控", value=bool(prev.get("enabled", True)), key=f"enabled_sol_{address}")
    selected_pools.append({"chain": "sol", "address": address,
                           "pool_type": str(pool_by_address[address].get("pool_type") or "DLMM"),
                           "label": pool_labels[address], "floor_price": floor,
                           "enabled": enabled})

rh_pools = [p for p in saved.get("pools", [])
            if p.get("chain") == "robinhood" and p.get("address")]
if rh_address.strip() and all(p.get("address") != rh_address.strip() for p in rh_pools):
    rh_pools.append({"chain": "robinhood", "address": rh_address.strip(),
                     "label": rh_label.strip() or rh_address.strip(),
                     "floor_price": 0.0, "enabled": True})
for rh_pool in rh_pools:
    address = str(rh_pool["address"])
    prev = current_by_key.get(("robinhood", address), rh_pool)
    left, mid, enabled_col, remove_col = st.columns([3, 1.5, 1, 1])
    left.write(rh_label.strip() if address == rh_address.strip() and rh_label.strip()
               else (prev.get("label") or address))
    floor = mid.number_input("价格下限", min_value=0.0, value=float(prev.get("floor_price") or 0.0),
                             format="%.10g", key=f"floor_rh_{address}")
    enabled = enabled_col.checkbox("监控", value=bool(prev.get("enabled", True)), key=f"enabled_rh_{address}")
    remove = remove_col.checkbox("移除", key=f"remove_rh_{address}")
    if not remove:
        selected_pools.append({"chain": "robinhood", "address": address,
                               "pool_id": prev.get("pool_id"),
                               "label": (rh_label.strip() if address == rh_address.strip() and rh_label.strip()
                                         else prev.get("label") or address),
                               "floor_price": floor, "enabled": enabled})

c1, c2, c3 = st.columns(3)
with c1:
    rsi_high = st.number_input("RSI(3) 告警阈值", min_value=1.0, max_value=100.0,
                               value=float(saved.get("rsi_high", 90.0)), step=1.0)
with c2:
    timeframe = st.selectbox("指标周期", ["5m", "1h"],
                             index=0 if saved.get("timeframe", "5m") == "5m" else 1)
with c3:
    enabled = st.checkbox("启用自动监控", value=bool(saved.get("enabled", False)))

st.caption("布林带使用 20 根收盘价、2 倍标准差；价格下穿人工填写的池子下限、RSI(3) 达到阈值、价格上穿中轨或上轨，任一项触发飞书。")
if st.button("💾 保存退出策略", type="primary"):
    config = {"enabled": enabled, "pools": selected_pools,
              "rsi_high": float(rsi_high), "timeframe": timeframe}
    if save_exit_strategy(config):
        st.success("退出策略已保存。启用后由运行中的 main.py 监控循环按配置间隔检查并推送飞书。")
        st.rerun()
    st.error("保存失败，请确认数据库连接已配置。")

st.divider()
st.subheader("已发现池子")
if eligible:
    st.dataframe(pd.DataFrame(eligible)[["pool_type", "pool_address", "token_x_symbol", "token_y_symbol", "price", "tvl"]],
                 use_container_width=True, hide_index=True)
else:
    st.info("扫描器数据库暂无池子。先运行 Solana 或 Robinhood 全池扫描，再回到这里人工勾选。")

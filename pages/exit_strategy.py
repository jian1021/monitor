"""退出策略：人工选池 + 告警条件。

池子放在一张可编辑表格里（价格下限 / 监控 / 移除），条件一行，统一保存。
保存的配置结构不变：{"enabled", "pools", "rsi_high", "timeframe", "rsi_period", "rsi_confirm_bars"}。
"""

import pandas as pd
import streamlit as st

from app.application.meteora_scanner import config as scanner_config
from app.application.meteora_scanner.db import Database
from app.application.meteora_scanner.filters import apply_filters
from app.infrastructure.db.exit_strategy import load_exit_strategy, save_exit_strategy
from app.interfaces.monitor_controls import render_monitor_controls

CHAIN_NAMES = {"sol": "Solana", "robinhood": "Robinhood"}

st.set_page_config(page_title="退出策略", page_icon="🚨", layout="wide")
st.title("🚨 退出策略")
st.caption("价格跌到下限、RSI 过高、上穿布林中轨或上轨，任一命中即推送飞书。")
render_monitor_controls("exit_strategy", "退出策略")


@st.cache_resource(show_spinner=False)
def scanner_db():
    return Database(scanner_config.DB_PATH)


saved = load_exit_strategy()
if "exit_draft" not in st.session_state:
    st.session_state["exit_draft"] = [dict(p) for p in saved.get("pools", []) if p.get("address")]
draft: list[dict] = st.session_state["exit_draft"]
in_draft = {(p.get("chain"), p.get("address")) for p in draft}

pool_rows = scanner_db().load_pools(scanner_config.SUPPORTED_POOL_TYPES)
filters = {**scanner_config.DEFAULT_FILTERS, **(scanner_db().get_setting("filters") or {})}
eligible = [r for r in apply_filters(pool_rows, filters) if r.get("pool_address") and r.get("passed")]
pool_by_address = {r["pool_address"]: r for r in eligible}


def _label(r: dict) -> str:
    return f"{r.get('token_x_symbol') or '?'} / {r.get('token_y_symbol') or '?'}"


# ---------------- 添加池子 ----------------
add_sol, add_rh = st.columns(2)
with add_sol.container(border=True):
    options = [a for a in pool_by_address if ("sol", a) not in in_draft]
    s_l, s_r = st.columns([4, 1], vertical_alignment="bottom")
    picked = s_l.multiselect(f"添加 Meteora 候选池（{len(options)} 个通过扫描筛选）", options,
                             format_func=lambda a: f"{_label(pool_by_address[a])} · {a[:8]}…",
                             placeholder="先在「全链扫描 → Meteora」扫描" if not options else "选择池子")
    if s_r.button("➕ 添加", key="add_sol", width="stretch", disabled=not picked):
        for a in picked:
            r = pool_by_address[a]
            draft.append({"chain": "sol", "address": a, "pool_type": str(r.get("pool_type") or "DLMM"),
                          "label": f"{_label(r)} · {a}", "floor_price": 0.0, "enabled": True})
        st.rerun()

with add_rh.container(border=True):
    r1, r2, r3 = st.columns([3, 2, 1], vertical_alignment="bottom")
    rh_address = r1.text_input("添加 Robinhood Chain 池", placeholder="0x…")
    rh_label = r2.text_input("名称（可选）", placeholder="TOKEN / ETH")
    if r3.button("➕ 添加", key="add_rh", width="stretch", disabled=not rh_address.strip()):
        addr = rh_address.strip()
        if ("robinhood", addr) in in_draft:
            st.warning("该池子已在列表中")
        else:
            draft.append({"chain": "robinhood", "address": addr, "label": rh_label.strip() or addr,
                          "floor_price": 0.0, "enabled": True})
            st.rerun()

# ---------------- 池子列表 ----------------
if draft:
    table = pd.DataFrame([{
        "监控": bool(p.get("enabled", True)),
        "池子": (p.get("label") or p["address"]).split(" · ")[0],
        "链": CHAIN_NAMES.get(p.get("chain"), p.get("chain")),
        "价格下限": float(p.get("floor_price") or 0.0),
        "地址": p["address"],
        "移除": False,
    } for p in draft])
    edited = st.data_editor(
        table, key="exit_pools_editor", hide_index=True, width="stretch",
        disabled=["池子", "链", "地址"],
        column_config={
            "监控": st.column_config.CheckboxColumn(width="small"),
            "价格下限": st.column_config.NumberColumn(min_value=0.0, format="%.10g",
                                                   help="0 = 不检查下限；与该池 K 线同一计价口径"),
            "移除": st.column_config.CheckboxColumn(width="small"),
        },
    )
else:
    edited = None
    st.info("还没有选择池子：从上方添加 Meteora 候选池或 Robinhood 池。")

# ---------------- 条件 + 保存 ----------------
with st.container(border=True):
    c0, c1, c2, c3, c4, c5 = st.columns([1.2, 1, 1, 1, 1.1, 1.1], vertical_alignment="bottom")
    enabled = c0.toggle("启用自动监控", value=bool(saved.get("enabled", False)))
    timeframe = c1.selectbox("K 线周期", ["5m", "1h"], index=0 if saved.get("timeframe", "5m") == "5m" else 1)
    rsi_period = c2.number_input("RSI 周期", min_value=2, max_value=50,
                                 value=int(saved.get("rsi_period", 6)), step=1,
                                 help="5m 周期上建议 6~14，周期太短噪声大")
    rsi_high = c3.number_input("RSI ≥", min_value=1.0, max_value=100.0,
                               value=float(saved.get("rsi_high", 90.0)), step=1.0)
    rsi_confirm_bars = c4.number_input("连续确认根数", min_value=1, max_value=10,
                                       value=int(saved.get("rsi_confirm_bars", 2)), step=1,
                                       help="最近 N 根 K 线 RSI 都达到阈值才告警")
    save = c5.button("💾 保存", type="primary", width="stretch")
    st.caption("布林带：20 根收盘价、2 倍标准差。")

if save:
    pools = []
    if edited is not None:
        for p, (_, row) in zip(draft, edited.iterrows()):
            if row["移除"]:
                continue
            pools.append({**p, "enabled": bool(row["监控"]), "floor_price": float(row["价格下限"] or 0.0)})
    config = {"enabled": enabled, "pools": pools, "rsi_high": float(rsi_high), "timeframe": timeframe,
              "rsi_period": int(rsi_period), "rsi_confirm_bars": int(rsi_confirm_bars)}
    if save_exit_strategy(config):
        st.session_state["exit_draft"] = [dict(p) for p in pools]
        st.session_state.pop("exit_pools_editor", None)
        st.toast("退出策略已保存，后台按配置间隔检查。", icon="✅")
        st.rerun()
    else:
        st.error("保存失败，请确认数据库连接已配置。")
elif edited is not None and (edited["移除"].any() or
                             (edited[["监控", "价格下限"]].values != table[["监控", "价格下限"]].values).any()):
    st.caption("⚠️ 有未保存的修改")

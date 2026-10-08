# -*- coding: utf-8 -*-
"""pages/robinhood_pools.py — Robinhood Pools 观测站面板（rhpools 服务数据）.

数据来自本地 rhpools 服务（modules/robinhoodpools 子模块）的 /api/lp/* API。
面板只读：不签交易、不改服务配置。索引未追平时数据不完整——看 status 指标。
"""
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import streamlit as st

from app.infrastructure.rhpools import client

st.set_page_config(page_title="Robinhood Pools", page_icon="🛰️", layout="wide")
st.title("🛰️ Robinhood Pools 观测站")
st.caption("Robinhood Chain（4663）LP 池索引服务的只读面板。所有数值为服务的已索引证据，"
           "缺失字段不补零；请先看索引状态再解读下面的表。")

# ------------------------- 侧边栏：服务与参数 -------------------------
with st.sidebar:
    st.header("服务")
    st.code(client.base_url(), language=None)
    c1, c2 = st.columns(2)
    if c1.button("🔄 探活", use_container_width=True):
        st.session_state.pop("rhp_status", None)
    if c2.button("▶️ 启动服务", use_container_width=True):
        with st.spinner("等待服务就绪（首次启动会初始化索引）..."):
            st.session_state["rhp_status"] = client.ensure_running(wait_s=90)
        st.rerun()
    if client.available():
        st.caption("子模块：modules/robinhoodpools ✅")
        st.caption("日志：logs/rhpools.log")
    else:
        st.error("子模块未拉取（Streamlit Cloud 不支持 submodule）")
        if st.button("📦 一键拉取子模块", use_container_width=True):
            with st.spinner("git clone 中（约 10~30 秒）..."):
                ok = client.ensure_repo()
            if ok:
                st.success("✅ 已拉取，点「启动服务」")
            else:
                st.error("❌ 拉取失败，看终端日志")
            st.rerun()
    st.markdown(
        f"[打开观测站终端 UI]({client.base_url()}/)（服务已启动时可用）"
    )
    st.divider()
    st.header("价差参数")
    min_bps = st.number_input("价差阈值 (bps)", 0.0, 10000.0, 50.0, 10.0)
    min_depth = st.number_input("浅边深度下限 ($)", 0.0, 1e6, 300.0, 100.0)

status = client.health()
if status is None:
    st.warning(
        "服务未运行。点左侧「▶️ 启动服务」即可——子模块缺失会**自动拉取**，"
        "首次启动要初始化索引，等状态变成 live 后再看下面的表。"
    )
    with st.expander("手动启动（本机终端）"):
        st.code(
            "git submodule update --init\n"
            "PYTHONPATH=modules/robinhoodpools/src python -m rhpools.lp_server",
            language="bash",
        )
    st.stop()

# ------------------------- 索引状态 -------------------------
lag_s = status.get("lag_s")
st.markdown("##### 📡 索引状态")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("state", str(status.get("state") or "?"))
c2.metric("head 块", f"{status.get('head') or '?':,}" if isinstance(status.get("head"), int) else "?")
c3.metric("已索引块",
          f"{status.get('indexed_head') or '?':,}" if isinstance(status.get("indexed_head"), int) else "?")
c4.metric("落后", f"{lag_s:.0f}s" if isinstance(lag_s, (int, float)) else "?")
c5.metric("落后块数", str(status.get("lag_blocks")) if status.get("lag_blocks") is not None else "?")

hist_from, hist_to = status.get("history_from"), status.get("history_to")
if isinstance(hist_from, (int, float)) and isinstance(hist_to, (int, float)):
    from datetime import datetime
    st.caption(f"历史覆盖：{datetime.fromtimestamp(hist_from):%Y-%m-%d %H:%M}"
               f" → {datetime.fromtimestamp(hist_to):%Y-%m-%d %H:%M}"
               f"（coverage 未完成时窗口数据不完整）")

providers = status.get("providers") or {}
problems = []
for name, p in providers.items():
    if isinstance(p, dict) and p.get("error"):
        problems.append(f"{name}: {p['error']}")
if problems:
    st.error("数据源异常：\n" + "\n".join(problems))

st.divider()

# ------------------------- 池子列表 -------------------------
st.markdown("##### 🏊 池子")
f1, f2, f3, f4 = st.columns([1, 1, 1, 2])
window = f1.selectbox("窗口", ["1h", "24h", "7d", "30d", "all"], index=1)
sort = f2.selectbox("排序", ["fees", "volume", "tvl", "flow", "activity", "swaps",
                             "lps", "price", "change", "created"], index=1)
protocol = f3.selectbox("协议", ["", "v2", "v3", "v4"], index=0,
                        format_func=lambda x: {"": "全部", "v2": "V2", "v3": "V3", "v4": "V4"}[x])
query = f4.text_input("搜索（池子/代币）", placeholder="WETH")

params = {"window": window, "sort": sort, "limit": 100}
if protocol:
    params["protocol"] = protocol
if query.strip():
    params["q"] = query.strip()

data = client.get("/api/lp/pools", params, timeout=30)
rows = (data or {}).get("rows") or []
if not rows:
    st.info("该窗口下没有已索引的池子（可能正在预热 / 回填）。")
else:
    df = pd.DataFrame(rows)
    show_cols = [c for c in ["pair", "protocol", "price", "price_change_pct", "tvl_usd",
                             "volume_usd", "fees_usd", "swaps", "adds", "removes",
                             "net_deposits_usd", "lp_count", "fee_ppm", "created_at"]
                 if c in df.columns]
    st.dataframe(df[show_cols], width="stretch", hide_index=True)
    cov = (data or {}).get("coverage") or {}
    st.caption(f"共 {(data or {}).get('total')} 个池；窗口 {cov.get('window', window)}"
               f"，coverage.complete={cov.get('complete')}"
               f"（false 表示历史回填未覆盖整个窗口）")

st.divider()

# ------------------------- 池间价差 -------------------------
st.markdown("##### ⚡ 池间价差（dislocations）")
st.caption("同一交易对在不同池的定价偏离；net_bps 已扣两边手续费，未计 gas 与滑点，"
           "价格为索引 mid price 而非可执行报价。")
dis = client.get("/api/lp/dislocations", {
    "min_bps": min_bps, "min_depth_usd": min_depth, "max_age_s": 3600, "limit": 50,
}, timeout=30)
drows = (dis or {}).get("rows") or []
if not drows:
    st.info(f"最近 1 小时内没有 ≥ {min_bps:.0f} bps 的价差（或数据尚未索引）。")
else:
    ddf = pd.DataFrame([{
        "pair": r.get("pair"), "pools": r.get("pool_count"),
        "spread_bps": round(r.get("spread_bps") or 0, 1),
        "net_bps": round(r["net_bps"], 1) if isinstance(r.get("net_bps"), (int, float)) else None,
        "depth_usd": r.get("depth_usd"),
        "buy_pool": (r.get("buy") or {}).get("protocol"),
        "buy_price": (r.get("buy") or {}).get("price"),
        "sell_pool": (r.get("sell") or {}).get("protocol"),
        "sell_price": (r.get("sell") or {}).get("price"),
    } for r in drows])
    st.dataframe(ddf, width="stretch", hide_index=True)

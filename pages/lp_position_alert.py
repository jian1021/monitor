# -*- coding: utf-8 -*-
"""LP 仓位 / 池子价格告警配置页"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import streamlit as st

from config import FEISHU_WEBHOOK
import lp_position_alert as lpa

st.set_page_config(page_title="LP 仓位告警", page_icon="📌", layout="wide")
st.title("📌 LP 仓位 / 池子价格告警")

if FEISHU_WEBHOOK:
    st.success("✅ 飞书告警通道已配置")
else:
    st.warning("⚠️ 未配置 FEISHU_WEBHOOK，告警仅打印到控制台")

if not lpa.ensure_table():
    st.error("❌ 初始化 lp_position_alert 表失败，请检查 Turso 凭据。")
    st.stop()

st.caption("Solana / Meteora DLMM 读链上仓位真实区间；Robinhood 等链只做池子价格。")

tab_lp, tab_price = st.tabs(["Solana LP 仓位", "池子价格（Robinhood 等）"])

with tab_lp:
    c1, c2, c3 = st.columns(3)
    lp_pool = c1.text_input("池子地址 *", key="lp_pool",
                            placeholder="Meteora DLMM 池子地址（LbPair）")
    lp_wallet = c2.text_input("钱包地址 *", key="lp_wallet", placeholder="持有该仓位的钱包")
    c3.selectbox("链", lpa.CHAIN_OPTIONS, key="lp_chain")

    if st.button("🔌 连接", type="primary", key="lp_connect"):
        if not lp_pool.strip() or not lp_wallet.strip():
            st.warning("⚠️ 请填写池子地址与钱包地址")
        else:
            with st.spinner("正在连接 Meteora 并读取仓位 ..."):
                st.session_state["lp_preview"] = lpa.preview_dlmm(
                    lp_pool.strip(), lp_wallet.strip())

    preview = st.session_state.get("lp_preview")
    if preview:
        if preview["error"]:
            st.error(preview["error"])
        if preview["pool"]:
            p = preview["pool"]
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("池子", p.get("name") or "-")
            m2.metric("现价", f"{p.get('current_price'):.10g}" if p.get("current_price") else "-")
            m3.metric("TVL", f"{p.get('tvl'):,.0f}" if p.get("tvl") else "-")
            m4.metric("黑名单", "是" if p.get("is_blacklisted") else "否")
        if preview["ok"]:
            st.success("✅ 连接成功")
            opts = {pos["position_address"]: pos for pos in preview["positions"]}
            rows = [{
                "仓位地址": pos["position_address"],
                "区间下界": pos["min_price"],
                "区间上界": pos["max_price"],
                "bin": f"{pos['lower_bin_id']} ~ {pos['upper_bin_id']}",
                "当前 PnL%": pos["pnl_pct"],
                "已超区间": pos["is_out_of_range"],
            } for pos in preview["positions"]]
            st.dataframe(pd.DataFrame(rows), use_container_width=True)

            with st.form("lp_add_form"):
                picked = st.selectbox("选择仓位 *", options=list(opts.keys()))
                f1, f2 = st.columns(2)
                tgt_pct = f1.number_input("盈利目标 %（真实持仓盈亏）", value=10.0,
                                          step=1.0, key="lp_tgt")
                default_floor = opts[picked]["min_price"] or 0.0
                floor = f2.number_input("跌穿阈值（默认 = 该仓位区间下界）",
                                        value=float(default_floor),
                                        format="%.10f", step=0.0, key="lp_floor")

                if st.form_submit_button("✅ 添加规则", type="primary"):
                    pos = opts[picked]
                    if floor <= 0:
                        st.warning("⚠️ 跌穿阈值必须大于 0")
                    else:
                        created = lpa.add_rule({
                            "kind": "dlmm", "chain": "sol",
                            "pool_address": lp_pool.strip(),
                            "wallet": lp_wallet.strip(),
                            "position_address": picked,
                            "pool_name": (preview["pool"] or {}).get("name"),
                            "token_x_symbol": (preview["pool"] or {}).get("token_x_symbol"),
                            "token_y_symbol": (preview["pool"] or {}).get("token_y_symbol"),
                            "lower_bin_id": pos["lower_bin_id"],
                            "upper_bin_id": pos["upper_bin_id"],
                            "min_price": pos["min_price"], "max_price": pos["max_price"],
                            "floor_price": float(floor),
                            "target_mode": "pnl_pct", "target_pct": float(tgt_pct),
                            "entry_price": None,
                        })
                        if created:
                            st.success("✅ 规则已添加")
                            st.rerun()
                        else:
                            st.error("❌ 规则写入失败")

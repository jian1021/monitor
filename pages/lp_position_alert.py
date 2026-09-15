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

with st.expander("🔍 配置诊断（部署排查用，只显示键名与来源，不显示任何值）"):
    import config as _cfg
    for _name in ("FEISHU_WEBHOOK", "LIBSQL_URL", "LIBSQL_TOKEN", "ADMIN_USER"):
        st.write(f"`{_name}` → {_cfg.setting_source(_name)}")
    st.write("st.secrets 中的键名：", _cfg.streamlit_secret_keys())

if not lpa.ensure_table():
    st.error("❌ 初始化 lp_position_alert 表失败，请检查 Turso 凭据。")
    st.stop()


@st.cache_data(ttl=120, show_spinner=False)
def cached_evm_preview(wallet):
    return lpa.preview_evm_wallet(wallet)

st.caption("Solana / Meteora DLMM 读链上仓位真实区间；Robinhood 只列未平仓的 Uniswap v4 仓位。"
           f"　构建 {lpa.VERSION}")

tab_lp, tab_price = st.tabs(["Solana LP 仓位", "池子价格（Robinhood 等）"])

with tab_lp:
    lp_wallet = st.text_input("钱包地址 *", key="lp_wallet",
                              placeholder="只需钱包地址，池子会自动列出")

    if st.button("🔌 连接钱包", type="primary", key="lp_connect"):
        if not lp_wallet.strip():
            st.warning("⚠️ 请填写钱包地址")
        else:
            with st.spinner("正在读取该钱包的 LP 仓位 ..."):
                st.session_state["wallet_preview"] = lpa.preview_wallet(lp_wallet.strip())

    wp = st.session_state.get("wallet_preview")
    preview = None
    if wp:
        if wp["error"]:
            st.error(wp["error"])
        if wp["ok"]:
            st.success(f"✅ 连接成功：找到 {len(wp['pools'])} 个有开放仓位的池子")
            st.dataframe(pd.DataFrame([{
                "池子": p["pool_name"],
                "池子地址": p["pool_address"],
                "仓位数": p["open_positions"],
                "池子价": p["pool_price"],
                "盈亏%": p["pnl_pct"],
            } for p in wp["pools"]]), use_container_width=True)

            labels = {p["pool_address"]: f"{p['pool_name']} · {p['pool_address'][:8]}…"
                      for p in wp["pools"]}
            picked_pool = st.selectbox("选择池子 *", options=list(labels),
                                       format_func=lambda a: labels[a],
                                       key="lp_picked_pool")

            with st.expander("🔧 高级：改用手动输入池子地址"):
                manual = st.text_input("池子地址", key="lp_manual_pool",
                                       placeholder="留空则用上面选中的池子")
                if manual.strip():
                    picked_pool = manual.strip()

            with st.spinner("读取该池仓位 ..."):
                preview = lpa.preview_dlmm(picked_pool, lp_wallet.strip())
            st.session_state["lp_active_pool"] = picked_pool

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
                            "pool_address": st.session_state["lp_active_pool"],
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


with tab_price:
    px_wallet = st.text_input("Robinhood 钱包地址 *", key="px_wallet",
                              placeholder="0x 开头的 EVM 钱包，Uniswap v4 仓位会自动列出")

    if st.button("🔌 连接钱包", type="primary", key="px_connect_wallet"):
        if not px_wallet.strip():
            st.warning("⚠️ 请填写钱包地址")
        else:
            with st.spinner("正在读取链上 Uniswap v4 仓位（首次约 30-40 秒，之后 2 分钟内走缓存）..."):
                st.session_state["evm_preview"] = cached_evm_preview(px_wallet.strip())

    ev = st.session_state.get("evm_preview")
    if ev:
        if ev["error"]:
            st.error(ev["error"])
        if ev["ok"]:
            basis = ev["positions"][0].get("price_basis") or "token1"
            st.success(f"✅ 连接成功：找到 {len(ev['positions'])} 个有流动性的 Uniswap v4 仓位"
                       f"（未平仓；价格以 {basis} 计价）")
            st.dataframe(pd.DataFrame([{
                "tokenId": p["token_id"],
                "交易对": p["pool_name"],
                "区间下界": p["lower_price"],
                "区间上界": p["upper_price"],
                "当前价": p["current_price"],
                "状态": "区间内" if p["in_range"] else "已超区间",
            } for p in ev["positions"]]), use_container_width=True)

            ev_labels = {str(p["token_id"]): f"{p['pool_name']} · #{p['token_id']}"
                         for p in ev["positions"]}
            picked_id = st.selectbox("选择仓位 *", options=list(ev_labels),
                                     format_func=lambda x: ev_labels[x],
                                     key="px_picked_position")
            picked = next(p for p in ev["positions"] if str(p["token_id"]) == picked_id)
            unit = picked.get("price_basis") or "token1"

            c1, c2, c3 = st.columns(3)
            c1.metric(f"区间下界（{unit}）", f"{picked['lower_price']:.10g}")
            c2.metric(f"当前价（{unit}）",
                      f"{picked['current_price']:.10g}" if picked.get("current_price") else "-")
            c3.metric("状态", "区间内" if picked["in_range"] else "已超区间")

            if picked.get("active_tick") is None:
                st.warning("⚠️ 读不到该池当前 tick，暂时无法建立规则，请稍后重试。")
            else:
                with st.form("evm_add_form"):
                    e1, e2 = st.columns(2)
                    e_tgt = e1.number_input("盈利目标 %（相对建立规则时现价）", value=10.0,
                                            step=1.0, key=f"evm_tgt_{picked_id}")
                    e_floor = e2.number_input(f"跌穿阈值价（{unit}，默认 = 该仓位区间下界）",
                                              value=float(picked["lower_price"]),
                                              format="%.10g", step=0.0,
                                              key=f"evm_floor_{picked_id}")
                    st.caption(f"链上 tick {picked['tick_lower']} ~ {picked['tick_upper']}，"
                               f"当前 {picked['active_tick']}；判定按 {unit} 价格比较")
                    if st.form_submit_button("✅ 添加规则", type="primary"):
                        created = lpa.add_rule({
                            "kind": "evm_v4", "chain": "robinhood",
                            "pool_address": picked["pool_id"],
                            "wallet": px_wallet.strip(),
                            "position_address": None,
                            "pool_name": picked["pool_name"],
                            "token_x_symbol": picked["token_x_symbol"],
                            "token_y_symbol": picked["token_y_symbol"],
                            "price_basis": picked.get("price_basis"),
                            "lower_bin_id": picked["tick_min"],
                            "upper_bin_id": picked["tick_max"],
                            "min_price": picked["lower_price"],
                            "max_price": picked["upper_price"],
                            "floor_price": float(e_floor),
                            "target_mode": "price_pct",
                            "target_pct": float(e_tgt),
                            "entry_price": picked["current_price"],
                            "token_id": picked["token_id"],
                            "entry_tick": picked["active_tick"],
                        })
                        if created:
                            st.success("✅ 规则已添加")
                            st.rerun()
                        else:
                            st.error("❌ 规则写入失败")

    st.divider()
    st.caption("或者：直接按池子地址监控价格（支持多链，不依赖仓位）")

    p1, p2 = st.columns(2)
    px_pool = p1.text_input("池子地址 *", key="px_pool", placeholder="Uniswap 池子 / pair 地址")
    px_chain = p2.selectbox("链", lpa.POOL_PRICE_CHAINS, key="px_chain")

    if st.button("🔌 连接", type="primary", key="px_connect"):
        if not px_pool.strip():
            st.warning("⚠️ 请填写池子地址")
        else:
            with st.spinner("正在连接 ..."):
                st.session_state["px_preview"] = lpa.preview_pool_price(
                    px_chain, px_pool.strip())

    px = st.session_state.get("px_preview")
    if px:
        if px["error"]:
            st.error(px["error"])
        if px["ok"]:
            st.success("✅ 连接成功")
            pair = px["pair"]
            q1, q2, q3, q4 = st.columns(4)
            q1.metric("交易对", f"{pair.get('base_symbol') or '-'} / {pair.get('quote_symbol') or '-'}")
            q2.metric("现价", f"{pair['price']:.10g}")
            q3.metric("流动性", f"{pair.get('liquidity_usd'):,.0f}"
                      if pair.get("liquidity_usd") else "-")
            q4.metric("建池时间",
                      pd.to_datetime(pair["pair_created_at"], unit="ms").strftime("%Y-%m-%d")
                      if pair.get("pair_created_at") else "-")

            if st.button("⬇️ 计算建池以来最低价", key="px_floor_btn"):
                with st.spinner("读取历史 K 线 ..."):
                    st.session_state["px_floor"] = lpa.fetch_pool_floor(
                        px_chain, px_pool.strip())
                if st.session_state["px_floor"] is None:
                    st.warning("⚠️ 取不到历史 K 线，请手动填写跌穿阈值。")

            entry_price = float(pair["price"])
            with st.form("px_add_form"):
                g1, g2 = st.columns(2)
                px_tgt = g1.number_input("盈利目标 %（相对登记时现价）", value=10.0,
                                         step=1.0, key="px_tgt")
                suggested = st.session_state.get("px_floor")
                px_floor = g2.number_input(
                    "跌穿阈值",
                    value=float(suggested) if suggested else 0.0,
                    format="%.10f", step=0.0, key="px_floor_val")
                st.caption(f"登记时现价 {entry_price:.10g}，盈利目标价 "
                           f"{entry_price * (100.0 + px_tgt) / 100.0:.10g}")

                if st.form_submit_button("✅ 添加规则", type="primary"):
                    if px_floor <= 0:
                        st.warning("⚠️ 跌穿阈值必须大于 0（可点上方按钮自动填入）")
                    else:
                        created = lpa.add_rule({
                            "kind": "pool_price", "chain": px_chain,
                            "pool_address": px_pool.strip(),
                            "wallet": None, "position_address": None,
                            "pool_name": f"{pair.get('base_symbol')} / {pair.get('quote_symbol')}",
                            "token_x_symbol": pair.get("base_symbol"),
                            "token_y_symbol": pair.get("quote_symbol"),
                            "floor_price": float(px_floor),
                            "target_mode": "price_pct", "target_pct": float(px_tgt),
                            "entry_price": entry_price,
                        })
                        if created:
                            st.success("✅ 规则已添加")
                            st.rerun()
                        else:
                            st.error("❌ 规则写入失败")

st.divider()
head_left, head_right = st.columns([3, 1])
head_left.subheader("现有规则")
if head_right.button("🔄 立即检查一次", key="run_now", use_container_width=True):
    with st.spinner("正在检查全部启用规则（触发的规则会发飞书）..."):
        lpa.run_once()
    st.success("检查完成，已刷新下方「当前值 / 盈亏% / 状态」")
    st.rerun()

rules = lpa.load_rules(enabled_only=False)
if not rules:
    st.info("ℹ️ 暂无规则，请在上方 Tab 中新增。")
    st.stop()

view = pd.DataFrame([{
    "id": r["id"],
    "类型": r["kind"],
    "池子": r["pool_name"] or r["pool_address"][:10] + "...",
    "链": r["chain"],
    "仓位": (f"#{r['token_id']}" if r["kind"] == "evm_v4" and r.get("token_id")
             else ((r["position_address"][:10] + "...") if r["position_address"] else "-")),
    "触发": "".join([
        f"{'盈利' if r['enable_target_alert'] else ''}"
        f"{'/' if r['enable_target_alert'] and r['enable_floor_alert'] else ''}"
        f"{'跌穿' if r['enable_floor_alert'] else ''}"
    ]),
    "盈利目标%": r["target_pct"],
    "跌穿阈值": r["floor_price"],
    "当前值": (
        f"PnL {r['last_pnl_pct']:.2f}%"
        if r["kind"] == "dlmm" and r["last_pnl_pct"] is not None
        else (f"{r['last_active_price']:.10g} {r['price_basis'] or ''}".strip()
              if r["kind"] == "evm_v4" and r["last_active_price"] is not None
              else (f"{r['last_active_price']:.10g}"
                    if r["last_active_price"] is not None else "-"))),
    "盈亏%": (
        f"{(r['last_active_price'] / r['entry_price'] - 1) * 100:+.2f}%"
        if r["kind"] == "evm_v4" and r.get("last_active_price") and r.get("entry_price")
        else "-"),
    "区间(下界~上界)": (
        f"{r['min_price']:.6g} ~ {r['max_price']:.6g}"
        if r["kind"] == "evm_v4" and r.get("min_price") and r.get("max_price")
        else "-"),
    "状态": {"open": "🟢 监控中", "closed": "⚫ 已关闭", "error": "🔴 取数失败"}.get(r["status"], r["status"]),
    "启用": r["enabled"],
    "已告警": "".join([
        "盈利" if r["target_alerted"] else "",
        "跌穿" if r["floor_alerted"] else "",
    ]) or "-",
} for r in rules])
st.dataframe(view, use_container_width=True)
 
st.caption("操作")
for r in rules:
    o1, o2, o3, o4 = st.columns([1, 1, 1, 3])
    o1.write(f"#{r['id']}")
    if o2.button("暂停" if r["enabled"] else "启用", key=f"tg_{r['id']}"):
        lpa.set_enabled(r["id"], not r["enabled"])
        st.rerun()
    if o3.button("重置告警", key=f"rs_{r['id']}"):
        lpa.reset_alerts(r["id"])
        st.rerun()
    if o4.button("删除", key=f"dl_{r['id']}"):
        lpa.delete_rule(r["id"])
        st.rerun()

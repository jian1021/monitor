# -*- coding: utf-8 -*-
"""LP 仓位 / 池子价格告警。

三种来源各一个标签页：连接 → 选仓位 → 一行设置目标与跌穿阈值 → 添加；
下方一张可编辑表格管理全部规则（启用 / 删除），统一保存。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import streamlit as st

from app.interfaces.monitor_controls import render_monitor_controls
from app.interfaces.reload_guard import fresh_timefmt
from scripts import lp_position_alert as lpa

to_local_display = fresh_timefmt()

st.set_page_config(page_title="LP 仓位告警", page_icon="📌", layout="wide")
st.title("📌 LP 仓位告警")
st.caption("盈利达标或价格跌穿阈值时推送飞书。Solana 读 Meteora DLMM 真实仓位；Robinhood 读未平仓的 Uniswap v4 仓位。")
render_monitor_controls("lp_alert", "LP 仓位 / 池子价格告警")

if not lpa.ensure_table():
    st.error("❌ 初始化 lp_position_alert 表失败，请检查 Turso 凭据。")
    st.stop()


@st.cache_data(ttl=120, show_spinner=False)
def cached_evm_preview(wallet, token_ids_text):
    return lpa.preview_evm_wallet(wallet, token_ids_text)


def _num(v, fmt=".6g") -> str:
    return "-" if v is None else format(v, fmt)


def _added(created: bool) -> None:
    if created:
        st.toast("规则已添加", icon="✅")
        st.rerun()
    st.error("❌ 规则写入失败")


tab_lp, tab_evm, tab_price = st.tabs(["Solana DLMM 仓位", "Robinhood v4 仓位", "按池子价格"])

# ---------------- Solana DLMM ----------------
with tab_lp:
    with st.form("lp_wallet_form", border=False):
        w1, w2 = st.columns([5, 1], vertical_alignment="bottom")
        lp_wallet = w1.text_input("钱包地址", key="lp_wallet", placeholder="Solana 钱包地址，池子会自动列出")
        if w2.form_submit_button("🔌 连接", type="primary", width="stretch"):
            if not lp_wallet.strip():
                st.warning("请填写钱包地址")
            else:
                with st.spinner("读取该钱包的 LP 仓位 …"):
                    st.session_state["wallet_preview"] = lpa.preview_wallet(lp_wallet.strip())

    wp = st.session_state.get("wallet_preview")
    if wp and wp["error"]:
        st.error(wp["error"])
    if wp and wp["ok"]:
        labels = {p["pool_address"]: f"{p['pool_name']} · {p['open_positions']} 个仓位 · "
                                     f"PnL {_num(p['pnl_pct'], '.2f')}%" for p in wp["pools"]}
        s1, s2 = st.columns([3, 2])
        picked_pool = s1.selectbox(f"池子（{len(labels)}）", list(labels), format_func=labels.get,
                                   key="lp_picked_pool")
        manual = s2.text_input("或手动输入池子地址", key="lp_manual_pool", placeholder="留空用左边选中的池子")
        picked_pool = manual.strip() or picked_pool

        with st.spinner("读取该池仓位 …"):
            preview = lpa.preview_dlmm(picked_pool, lp_wallet.strip())
        if preview["error"]:
            st.error(preview["error"])
        pool_info = preview["pool"] or {}
        if pool_info:
            st.caption(f"{pool_info.get('name') or '-'} · 现价 {_num(pool_info.get('current_price'), '.10g')} · "
                       f"TVL {_num(pool_info.get('tvl'), ',.0f')}"
                       + (" · ⚠️ 黑名单" if pool_info.get("is_blacklisted") else ""))
        if preview["ok"]:
            opts = {pos["position_address"]: pos for pos in preview["positions"]}
            a1, a2, a3, a4 = st.columns([3, 1, 1.4, 0.8], vertical_alignment="bottom")
            picked = a1.selectbox(
                "仓位", list(opts), key="lp_pos",
                format_func=lambda k: (f"{k[:8]}… · {_num(opts[k]['min_price'])} ~ {_num(opts[k]['max_price'])}"
                                       f" · PnL {_num(opts[k]['pnl_pct'], '.2f')}%"
                                       + (" · 已出区间" if opts[k]["is_out_of_range"] else "")))
            pos = opts[picked]
            tgt_pct = a2.number_input("盈利目标 %", value=10.0, step=1.0, key=f"lp_tgt_{picked}")
            floor = a3.number_input("跌穿阈值（默认区间下界）", value=float(pos["min_price"] or 0.0),
                                    format="%.10g", step=0.0, key=f"lp_floor_{picked}")
            if a4.button("➕ 添加", type="primary", width="stretch", key="lp_add"):
                if floor <= 0:
                    st.warning("跌穿阈值必须大于 0")
                else:
                    _added(lpa.add_rule({
                        "kind": "dlmm", "chain": "sol", "pool_address": picked_pool,
                        "wallet": lp_wallet.strip(), "position_address": picked,
                        "pool_name": pool_info.get("name"),
                        "token_x_symbol": pool_info.get("token_x_symbol"),
                        "token_y_symbol": pool_info.get("token_y_symbol"),
                        "lower_bin_id": pos["lower_bin_id"], "upper_bin_id": pos["upper_bin_id"],
                        "min_price": pos["min_price"], "max_price": pos["max_price"],
                        "floor_price": float(floor), "target_mode": "pnl_pct",
                        "target_pct": float(tgt_pct), "entry_price": None,
                    }))
            st.caption("盈利目标按真实持仓盈亏（PnL%）判断。")

# ---------------- Robinhood Uniswap v4 ----------------
with tab_evm:
    with st.form("evm_wallet_form", border=False):
        e1, e2, e3 = st.columns([3.5, 2, 1], vertical_alignment="bottom")
        px_wallet = e1.text_input("钱包地址", key="px_wallet", placeholder="0x 开头的 EVM 钱包")
        px_token_ids = e2.text_input("tokenId（可选，逗号分隔）", key="px_token_ids",
                                     help="公共 RPC 不支持全历史扫描，填 tokenId 可精确读取")
        if e3.form_submit_button("🔌 连接", type="primary", width="stretch"):
            if not px_wallet.strip():
                st.warning("请填写钱包地址")
            else:
                with st.spinner("读取链上 Uniswap v4 仓位 …"):
                    st.session_state["evm_preview"] = cached_evm_preview(px_wallet.strip(), px_token_ids.strip())

    ev = st.session_state.get("evm_preview")
    if ev and ev["error"]:
        st.error(ev["error"])
    if ev and ev["ok"]:
        by_id = {str(p["token_id"]): p for p in ev["positions"]}
        b1, b2, b3, b4 = st.columns([3, 1, 1.4, 0.8], vertical_alignment="bottom")
        picked_id = b1.selectbox(
            f"仓位（{len(by_id)}）", list(by_id), key="px_picked_position",
            format_func=lambda k: (f"{by_id[k]['pool_name']} #{k} · {_num(by_id[k]['lower_price'])} ~ "
                                   f"{_num(by_id[k]['upper_price'])} · "
                                   + ("区间内" if by_id[k]["in_range"] else "已出区间")))
        picked = by_id[picked_id]
        unit = picked.get("price_basis") or "token1"
        e_tgt = b2.number_input("盈利目标 %", value=10.0, step=1.0, key=f"evm_tgt_{picked_id}")
        e_floor = b3.number_input(f"跌穿阈值（{unit}）", value=float(picked["lower_price"]),
                                  format="%.10g", step=0.0, key=f"evm_floor_{picked_id}")
        no_tick = picked.get("active_tick") is None
        if b4.button("➕ 添加", type="primary", width="stretch", key="evm_add", disabled=no_tick):
            _added(lpa.add_rule({
                "kind": "evm_v4", "chain": "robinhood", "pool_address": picked["pool_id"],
                "wallet": px_wallet.strip(), "position_address": None,
                "pool_name": picked["pool_name"], "token_x_symbol": picked["token_x_symbol"],
                "token_y_symbol": picked["token_y_symbol"], "price_basis": picked.get("price_basis"),
                "lower_bin_id": picked["tick_min"], "upper_bin_id": picked["tick_max"],
                "min_price": picked["lower_price"], "max_price": picked["upper_price"],
                "floor_price": float(e_floor), "target_mode": "price_pct", "target_pct": float(e_tgt),
                "entry_price": picked["current_price"], "token_id": picked["token_id"],
                "entry_tick": picked["active_tick"],
            }))
        st.caption("读不到当前 tick，暂时无法建立规则，请稍后重试。" if no_tick else
                   f"现价 {_num(picked.get('current_price'), '.10g')} {unit} · tick {picked['tick_lower']} ~ "
                   f"{picked['tick_upper']}（当前 {picked['active_tick']}）· 盈利目标相对建立规则时的现价")

# ---------------- 按池子价格 ----------------
with tab_price:
    with st.form("px_pool_form", border=False):
        q1, q2, q3 = st.columns([4, 1.3, 1], vertical_alignment="bottom")
        px_pool = q1.text_input("池子地址", key="px_pool", placeholder="Uniswap 池子 / pair 地址（不依赖仓位）")
        px_chain = q2.selectbox("链", lpa.POOL_PRICE_CHAINS, key="px_chain")
        if q3.form_submit_button("🔌 连接", type="primary", width="stretch"):
            if not px_pool.strip():
                st.warning("请填写池子地址")
            else:
                with st.spinner("连接中 …"):
                    st.session_state["px_preview"] = lpa.preview_pool_price(px_chain, px_pool.strip())
                    st.session_state.pop("px_floor", None)

    px = st.session_state.get("px_preview")
    if px and px["error"]:
        st.error(px["error"])
    if px and px["ok"]:
        pair = px["pair"]
        entry_price = float(pair["price"])
        created_at = (pd.to_datetime(pair["pair_created_at"], unit="ms").strftime("%Y-%m-%d")
                      if pair.get("pair_created_at") else "-")
        st.caption(f"{pair.get('base_symbol') or '-'} / {pair.get('quote_symbol') or '-'} · 现价 {entry_price:.10g} · "
                   f"流动性 {_num(pair.get('liquidity_usd'), ',.0f')} · 建池 {created_at}")
        c1, c2, c3, c4 = st.columns([1, 1.6, 1.2, 0.8], vertical_alignment="bottom")
        px_tgt = c1.number_input("盈利目标 %", value=10.0, step=1.0, key="px_tgt")
        suggested = st.session_state.get("px_floor")
        px_floor = c2.number_input("跌穿阈值", value=float(suggested) if suggested else 0.0,
                                   format="%.10g", step=0.0, key=f"px_floor_val_{suggested}")
        if c3.button("⬇️ 填入建池以来最低价", width="stretch"):
            with st.spinner("读取历史 K 线 …"):
                st.session_state["px_floor"] = lpa.fetch_pool_floor(px_chain, px_pool.strip())
            if st.session_state.get("px_floor") is None:
                st.warning("取不到历史 K 线，请手动填写跌穿阈值。")
            else:
                st.rerun()
        if c4.button("➕ 添加", type="primary", width="stretch", key="px_add"):
            if px_floor <= 0:
                st.warning("跌穿阈值必须大于 0")
            else:
                _added(lpa.add_rule({
                    "kind": "pool_price", "chain": px_chain, "pool_address": px_pool.strip(),
                    "wallet": None, "position_address": None,
                    "pool_name": f"{pair.get('base_symbol')} / {pair.get('quote_symbol')}",
                    "token_x_symbol": pair.get("base_symbol"), "token_y_symbol": pair.get("quote_symbol"),
                    "floor_price": float(px_floor), "target_mode": "price_pct",
                    "target_pct": float(px_tgt), "entry_price": entry_price,
                }))
        st.caption(f"盈利目标价 {entry_price * (100.0 + px_tgt) / 100.0:.10g}（相对登记时现价）")

# ---------------- 规则列表 ----------------
rules = lpa.load_rules(enabled_only=False)
head_l, head_r = st.columns([4, 1], vertical_alignment="bottom")
n_on = sum(1 for r in rules if r["enabled"])
n_alarm = sum(1 for r in rules if r["alarm_active"])
head_l.subheader(f"规则 {n_on}/{len(rules)} 启用 · {n_alarm} 报警中")
if head_r.button("🔄 立即检查一次", width="stretch", disabled=not rules,
                 help="检查全部启用规则，触发的会发飞书"):
    with st.spinner("检查中 …"):
        lpa.run_once()
    st.toast("检查完成", icon="🔄")
    st.rerun()

if not rules:
    st.info("暂无规则，在上方任一标签页里添加。")
else:
    def _current(r) -> str:
        if r["kind"] == "dlmm" and r["last_pnl_pct"] is not None:
            return f"PnL {r['last_pnl_pct']:.2f}%"
        if r["last_active_price"] is None:
            return "-"
        s = f"{r['last_active_price']:.6g}"
        if r["kind"] == "evm_v4" and r.get("entry_price"):
            s += f"（{(r['last_active_price'] / r['entry_price'] - 1) * 100:+.2f}%）"
        return s

    view = pd.DataFrame([{
        "id": r["id"],
        "启用": bool(r["enabled"]),
        "池子": r["pool_name"] or r["pool_address"][:10] + "…",
        "类型": {"dlmm": "DLMM 仓位", "evm_v4": "v4 仓位", "pool_price": "池子价格"}.get(r["kind"], r["kind"]),
        "链": r["chain"],
        "盈利目标%": r["target_pct"],
        "跌穿阈值": r["floor_price"],
        "当前": _current(r),
        "状态": ("🔔 报警中" if r["alarm_active"] else
                 {"open": "🟢 监控中", "closed": "⚫ 已关闭", "error": "🔴 取数失败"}.get(r["status"], r["status"])),
        "已告警": "、".join(x for x, f in (("盈利", r["target_alerted"]), ("跌穿", r["floor_alerted"])) if f) or "-",
        "最近报警": to_local_display(r["last_alert_at"]),
        "删除": False,
    } for r in rules])
    edited = st.data_editor(
        view, key="lp_rules_editor", hide_index=True, width="stretch",
        disabled=[c for c in view.columns if c not in ("启用", "删除")],
        column_config={
            "id": None,
            "启用": st.column_config.CheckboxColumn(width="small"),
            "盈利目标%": st.column_config.NumberColumn(format="%.2f", width="small"),
            "跌穿阈值": st.column_config.NumberColumn(format="%.6g"),
            "删除": st.column_config.CheckboxColumn(width="small"),
        },
    )
    orig, new = view.set_index("id"), edited.set_index("id")
    changes = [(rid, "rm" if new.loc[rid, "删除"] else "on") for rid in new.index
               if new.loc[rid, "删除"] or bool(orig.loc[rid, "启用"]) != bool(new.loc[rid, "启用"])]
    if changes:
        n_del = sum(1 for _, k in changes if k == "rm")
        i_l, i_r = st.columns([4, 1], vertical_alignment="center")
        i_l.info(f"{len(changes)} 项未保存" + (f"，含删除 {n_del} 条（不可恢复）" if n_del else ""))
        if i_r.button("💾 保存修改", type="primary", width="stretch"):
            for rid, kind in changes:
                if kind == "rm":
                    lpa.delete_rule(int(rid))
                else:
                    lpa.set_enabled(int(rid), bool(new.loc[rid, "启用"]))
            st.session_state.pop("lp_rules_editor", None)
            st.toast("已保存", icon="💾")
            st.rerun()

with st.expander("🔍 配置诊断（只显示键名与来源，不显示值）"):
    import app.core.config as _cfg
    for _name in ("FEISHU_WEBHOOK", "LIBSQL_URL", "LIBSQL_TOKEN", "ADMIN_USER", "ROBINHOOD_RPC"):
        st.write(f"`{_name}` → {_cfg.setting_source(_name)}")
    st.write("st.secrets 中的键名：", _cfg.streamlit_secret_keys())
    st.caption(f"当前 RPC：{lpa.EVM_RPC}　构建 {lpa.VERSION}")

# -*- coding: utf-8 -*-
"""Robinhood Chain Uniswap 池：筛选参数 / 流动池列表 / 单池详情 / 监控报警。

同一模块的功能合并到一个页面：
- 顶部完成 Robinhood Chain 链验证；
- 四个 Tab 分别对应需求书的页面一~四；
- 筛选结果 300s 缓存全页共享，参数保存后立即按新参数重算。
"""
import json
import time
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.application.fee_screen.monitor import (
    load_monitor_config,
    run_fee_screen_monitor,
    save_monitor_config,
)
from app.application.fee_screen.service import load_screening_config
from app.application.fee_screen.ui_data import (
    pool_window_series,
    reevaluate_with_candles,
    run_screening_cached,
    screening_cfg_json,
)
from app.domain.fee_screen.models import RiskLevel, ScreenConfig
from app.domain.fee_screen.screening import (
    estimate_fee_income,
    estimate_net_return,
)
from app.infrastructure.db.fee_screen_store import (
    list_alerts,
    set_screening_config_json,
)
from app.infrastructure.market_data import rh_fee_sources as src
from app.interfaces.robinhood_observer import render_robinhood_observer
from app.interfaces.monitor_controls import render_monitor_controls

def render_page() -> None:
    st.title("🦅 Robinhood Chain 全池扫描")
    st.caption("扫描 Robinhood Chain（4663）上的 Uniswap V2/V3/V4/Slipstream 流动池，并按条件筛选。"
               "Solana 的 Meteora DLMM 池请前往「Meteora 全池扫描」页面。")

    # ================= Robinhood Chain 验证（session 缓存） =================
    if "fee_screen_chain" not in st.session_state:
        st.session_state.fee_screen_chain = src.verify_chain()
    chain = st.session_state.fee_screen_chain

    banner = st.columns([3, 1])
    with banner[0]:
        if chain.get("ok"):
            st.success(f"✅ {chain['reason']}（RPC: {chain['rpc_url']}）")
        else:
            st.error(f"❌ 链验证失败，停止筛选：{chain.get('reason')}——"
                     "不会展示任何池子数据，避免混入其他链。")
    with banner[1]:
        if st.button("🔄 重新验证", width="stretch"):
            st.session_state.pop("fee_screen_chain", None)
            st.rerun()

    st.divider()

    tab_config, tab_pools, tab_detail, tab_monitor, tab_observer = st.tabs(
        ["🎚️ 筛选参数（页面一）", "📋 流动池列表（页面二）",
         "🔎 单池详情（页面三）", "🔔 监控与报警（页面四）",
         "📡 池发现与索引观测"],
        on_change="rerun", key="fee_screen_tabs")


    # ================================================================ 页面一
    def render_config_tab() -> None:
        with tab_config:
            st.markdown("##### 🎚️ 初筛参数（保存后对本页所有 Tab 及主循环监控生效）")
            cfg = load_screening_config() or ScreenConfig()

            c1, c2, c3 = st.columns(3)
            min_volume_24h = c1.number_input("最低 24 小时成交量（$）", min_value=0.0,
                                             value=float(cfg.min_volume_24h), step=10000.0)
            min_fees_24h = c2.number_input("最低 24 小时手续费（$）", min_value=0.0,
                                           value=float(cfg.min_fees_24h), step=500.0)
            min_fee_rate = c3.number_input("最低实际平均手续费率", min_value=0.0, max_value=1.0,
                                           value=float(cfg.min_fee_rate), step=0.001,
                                           help="实际平均手续费率 = 24h 手续费 ÷ 24h 成交量（真实成交费率），"
                                                "不是界面显示的基础费率。当前 Robinhood Chain 池以低费率池为主。")

            c4, c5, c6 = st.columns(3)
            min_fees_active_tvl = c4.number_input("最低 24h Fees/Active TVL", min_value=0.0,
                                                  max_value=10.0,
                                                  value=float(cfg.min_fees_active_tvl), step=0.01,
                                                  help="Active TVL 取 rhpools 观测的区间内 TVL；"
                                                       "缺失时该指标标记数据不可用，不用总 TVL 冒充。")
            min_persistence_ratio = c5.number_input("最低 7 天成交量持续性比例", min_value=0.0,
                                                    max_value=10.0,
                                                    value=float(cfg.min_persistence_ratio), step=0.05,
                                                    help="数据源无每日成交量序列，该比例为 24h 成交量 ÷ 7 天日均的近似值。")
            min_history_days = c6.number_input("最低池子历史数据天数", min_value=0, max_value=90,
                                               value=int(cfg.min_history_days), step=1,
                                               help="要求超过 7 天时：数据源未提供池子历史长度，会标记数据不可用并扣分。")

            st.markdown("##### 📉 价格趋势与下跌风险过滤")
            c7, c8, c9 = st.columns(3)
            max_drop_1h = c7.number_input("最大允许 1 小时跌幅（%）", min_value=0.0, max_value=100.0,
                                          value=float(cfg.max_drop_1h), step=1.0)
            max_drop_4h = c8.number_input("最大允许 4 小时跌幅（%）", min_value=0.0, max_value=100.0,
                                          value=float(cfg.max_drop_4h), step=1.0,
                                          help="数据源无 4h 涨跌幅字段，实际使用 6h 涨跌幅代替（已在结果中标注）。")
            big_candle_drop_pct = c9.number_input("连续大阴线收盘跌幅阈值（%）", min_value=0.1,
                                                  max_value=50.0,
                                                  value=float(cfg.big_candle_drop_pct), step=0.1,
                                                  help="5 分钟线收盘价较前一根跌幅达到该阈值记为一根大阴线。")

            c10, c11 = st.columns(2)
            big_candle_count = c10.number_input("连续大阴线根数", min_value=1, max_value=12,
                                                value=int(cfg.big_candle_count), step=1)
            min_candles_for_trend = c11.number_input("判断趋势所需最少 5 分钟线根数", min_value=6,
                                                     max_value=200,
                                                     value=int(cfg.min_candles_for_trend), step=1)

            st.markdown("##### ⚖️ 排名权重")
            c12, c13, c14, c15, c16 = st.columns(5)
            w_fees = c12.number_input("24h 手续费", 0.0, 1.0, float(cfg.w_fees), 0.05)
            w_fees_active_tvl = c13.number_input("Fees/Active TVL", 0.0, 1.0,
                                                 float(cfg.w_fees_active_tvl), 0.05)
            w_volume = c14.number_input("24h 成交量", 0.0, 1.0, float(cfg.w_volume), 0.05)
            w_persistence = c15.number_input("7 天成交量持续性", 0.0, 1.0,
                                             float(cfg.w_persistence), 0.05)
            w_trend = c16.number_input("价格趋势与下跌风险", 0.0, 1.0, float(cfg.w_trend), 0.05)
            weight_sum = w_fees + w_fees_active_tvl + w_volume + w_persistence + w_trend
            if abs(weight_sum - 1.0) > 1e-9:
                st.warning(f"权重之和为 {weight_sum:.2f}，建议调整为 1.00（当前按原值加权）。")

            if st.button("💾 保存筛选参数", type="primary", width="stretch"):
                new_cfg = ScreenConfig(
                    min_volume_24h=min_volume_24h, min_fees_24h=min_fees_24h,
                    min_fee_rate=min_fee_rate, min_fees_active_tvl=min_fees_active_tvl,
                    min_history_days=int(min_history_days),
                    min_persistence_ratio=min_persistence_ratio,
                    max_drop_1h=max_drop_1h, max_drop_4h=max_drop_4h,
                    big_candle_drop_pct=big_candle_drop_pct,
                    big_candle_count=int(big_candle_count),
                    min_candles_for_trend=int(min_candles_for_trend),
                    w_fees=w_fees, w_fees_active_tvl=w_fees_active_tvl, w_volume=w_volume,
                    w_persistence=w_persistence, w_trend=w_trend,
                )
                ok = set_screening_config_json(json.dumps(
                    {k: getattr(new_cfg, k) for k in vars(new_cfg)}, ensure_ascii=False))
                if ok:
                    st.success("✅ 已保存（数据库），主循环 fee_screen 监控将使用同一套参数")
                    st.cache_data.clear()   # 立即按新参数重算（下方列表/详情 Tab）
                else:
                    st.warning("⚠️ 写入数据库失败，参数仅在本次会话生效")

            st.divider()
            st.markdown("##### 📡 数据源可用性（已实测）")
            st.markdown(
                "| 数据源 | 用途 | 状态 |\n"
                "|---|---|---|\n"
                "| Robinhood Chain RPC | 链验证（Chain ID 4663） | ✅ 官方 RPC |\n"
                "| rhpools 索引（本地 8196 / rhpools.lol） | 池子、成交量、手续费、TVL、Active TVL | ✅ 链上事件索引 |\n"
                "| DexScreener（chainId=robinhood） | 涨跌幅(1h/6h/24h)、创建时间、流动性 | ✅ 无 4h 字段（用 6h） |\n"
                "| rhpools tape | 5 分钟 K 线重建（逐事件价格） | ⚠️ 样本有限 |\n"
                "| 每日成交量序列 / 5m 官方 K 线 / 奖励数据 | — | ❌ 不可用（界面标记，不编造） |\n"
            )

            st.divider()
            st.markdown("##### ⚙️ 后台监控设置")
            st.caption("仅控制常驻后台进程 `python main.py` 中的全池扫描监控（开关 + 轮询间隔）；"
                       "页面上的手动查询或扫描不受影响，此处修改实时生效。")
            render_monitor_controls("fee_screen", "Robinhood 全池扫描")


    # ================================================================ 页面二
    def render_pools_tab() -> None:
        with tab_pools:
            data = run_screening_cached(screening_cfg_json())
            if not data["ok"] or not data["chain"].get("ok"):
                st.error(f"❌ {data['reason'] or '链验证失败'}")
                return

            st.success(f"✅ {data['reason']} ｜ 数据时间："
                       f"{datetime.fromtimestamp(data['fetched_at']).strftime('%m-%d %H:%M:%S')}")
            for warning in data["warnings"]:
                st.warning(f"⚠️ {warning}")

            def _serialize(ev) -> dict:
                pool = ev.pool
                return {
                    "pool_id": pool.pool_id,
                    "pair": pool.pair,
                    "protocol": pool.protocol,
                    "pool_address": pool.pool_address_display,
                    "token0_symbol": pool.token0.symbol,
                    "token0_address": pool.token0.address,
                    "token1_symbol": pool.token1.symbol,
                    "token1_address": pool.token1.address,
                    "volume_usd": pool.volume_usd,
                    "fees_usd": pool.fees_usd,
                    "fee_rate": ev.actual_fee_rate,
                    "fee_ppm": pool.fee_ppm,
                    "tvl_usd": pool.tvl_usd,
                    "active_tvl_usd": pool.active_tvl_usd,
                    "fees_active_tvl": ev.fees_active_tvl,
                    "volume_7d": pool.volume_7d,
                    "persistence": ev.persistence_ratio,
                    "chg_1h": (pool.ds_price_change or {}).get("h1"),
                    "chg_4h": (pool.ds_price_change or {}).get("h6"),  # 数据源无 4h，用 6h
                    "chg_24h": (pool.ds_price_change or {}).get("h24"),
                    "risk": ev.risk_level.value,
                    "score": round(ev.score, 1),
                    "failures": ev.failures,
                    "deductions": ev.deductions,
                    "window_complete": pool.window_complete,
                    "trend_reasons": ev.trend.reasons,
                }

            rows = [_serialize(ev) for ev in data["evaluations"]]
            failed_rows = [
                {"池子": ev.pool.pair, "协议": ev.pool.protocol,
                 "24h成交量($)": ev.pool.volume_usd, "24h手续费($)": ev.pool.fees_usd,
                 "未通过原因": "; ".join(ev.failures) or "评分/数据不足"}
                for ev in data["all_evaluations"] if not ev.passed
            ]

            c1, c2, c3, c4 = st.columns([2, 1, 1, 2])
            query = c1.text_input("🔍 搜索（池子名 / 代币符号 / 地址）", "").strip().lower()
            protocol = c2.selectbox("协议", ["全部"] + sorted({r["protocol"] for r in rows}))
            risk = c3.selectbox("风险等级", ["全部"] + [r.value for r in RiskLevel])
            sort_by = c4.selectbox(
                "排序", ["综合评分", "24h手续费", "Fees/Active TVL", "24h成交量",
                         "实际手续费率", "24h涨跌幅"], index=0)

            filtered = rows
            if query:
                filtered = [r for r in filtered if query in r["pair"].lower()
                            or query in r["pool_address"].lower()
                            or query in r["token0_symbol"].lower()
                            or query in r["token1_symbol"].lower()
                            or query in r["token0_address"].lower()
                            or query in r["token1_address"].lower()]
            if protocol != "全部":
                filtered = [r for r in filtered if r["protocol"] == protocol]
            if risk != "全部":
                filtered = [r for r in filtered if r["risk"] == risk]
            sort_keys = {
                "综合评分": lambda r: r["score"],
                "24h手续费": lambda r: r["fees_usd"] or 0,
                "Fees/Active TVL": lambda r: r["fees_active_tvl"] or 0,
                "24h成交量": lambda r: r["volume_usd"] or 0,
                "实际手续费率": lambda r: r["fee_rate"] or 0,
                "24h涨跌幅": lambda r: (r["chg_24h"] or 0),
            }
            filtered = sorted(filtered, key=sort_keys[sort_by], reverse=True)

            def _na(value, fmt: str) -> str:
                return "数据不可用" if value is None else fmt.format(value)

            table = [
                {
                    "排名": i + 1,
                    "池子名称": r["pair"],
                    "协议": r["protocol"].upper(),
                    "池子地址": r["pool_address"],
                    "代币": f"{r['token0_symbol']}({r['token0_address'][:8]}…)/"
                           f"{r['token1_symbol']}({r['token1_address'][:8]}…)",
                    "24h成交量($)": r["volume_usd"],
                    "24h手续费($)": r["fees_usd"],
                    "实际手续费率": _na(r["fee_rate"], "{:.3%}"),
                    "基础费率(ppm)": r["fee_ppm"],
                    "TVL($)": r["tvl_usd"],
                    "Active TVL($)": r["active_tvl_usd"],
                    "Fees/ActiveTVL": _na(r["fees_active_tvl"], "{:.2%}"),
                    "7d成交量($)": r["volume_7d"],
                    "持续性(24h/7d日均)": _na(r["persistence"], "{:.2f}"),
                    "1h涨跌": _na(r["chg_1h"], "{:+.2f}%"),
                    "4h涨跌(用6h)": _na(r["chg_4h"], "{:+.2f}%"),
                    "24h涨跌": _na(r["chg_24h"], "{:+.2f}%"),
                    "风险等级": r["risk"],
                    "综合评分": r["score"],
                    "窗口完整": "✅" if r["window_complete"] else "⚠️ 部分",
                }
                for i, r in enumerate(filtered)
            ]
            st.dataframe(
                table, width="stretch", hide_index=True,
                column_config={
                    "24h成交量($)": st.column_config.NumberColumn(format="$%.0f"),
                    "24h手续费($)": st.column_config.NumberColumn(format="$%.0f"),
                    "TVL($)": st.column_config.NumberColumn(format="$%.0f"),
                    "Active TVL($)": st.column_config.NumberColumn(format="$%.0f"),
                    "7d成交量($)": st.column_config.NumberColumn(format="$%.0f"),
                    "综合评分": st.column_config.ProgressColumn(min_value=0.0, max_value=100.0),
                },
            )

            rh_labels = {
                f"{r['pair']} · {r['protocol'].upper()} · {r['pool_address']}": r
                for r in filtered
            }
            selected_rh = st.multiselect(
                "人工勾选 Robinhood 池，添加到退出策略",
                options=list(rh_labels), key="rh_exit_strategy_pool_selection",
            )
            if st.button("➕ 添加所选池到退出策略", disabled=not selected_rh):
                from app.infrastructure.db.exit_strategy import load_exit_strategy, save_exit_strategy

                strategy = load_exit_strategy()
                existing = {(p.get("chain"), p.get("address")) for p in strategy.get("pools", [])}
                for label in selected_rh:
                    row = rh_labels[label]
                    address = str(row.get("pool_address") or row.get("pool_id") or "")
                    if address and ("robinhood", address) not in existing:
                        strategy.setdefault("pools", []).append({
                            "chain": "robinhood", "address": address,
                            "pool_id": row.get("pool_id"),
                            "label": row.get("pair") or label, "floor_price": 0.0,
                            "enabled": True,
                        })
                        existing.add(("robinhood", address))
                if save_exit_strategy(strategy):
                    st.success("已加入退出策略池清单；请前往「退出策略」填写价格下限并启用监控。")
                else:
                    st.error("写入失败，请确认数据库连接。")

            st.markdown("##### 🔎 进入单池详情")
            if not table:
                st.info("当前筛选条件下没有通过的池子。可放宽参数（参数 Tab）或查看下方未通过原因。")
            else:
                # 池子 id 跟随当前排序/过滤后的行，逐行对应，避免选错池子
                row_pool_ids = [r["pool_id"] for r in filtered]
                options = {f"{i + 1}. {r['池子名称']} ({r['协议']}) 评分 {r['综合评分']}": row_pool_ids[i]
                           for i, r in enumerate(table)}
                pick = st.selectbox("选择池子", list(options.keys()))
                if st.button("在「单池详情」Tab 中打开 →", type="primary"):
                    st.session_state.fee_screen_detail_pool = options[pick]
                    st.rerun()

            with st.expander(f"❌ 未通过初筛的池子（{len(failed_rows)} 个）及原因"):
                if failed_rows:
                    st.dataframe(failed_rows, width="stretch", hide_index=True)
                else:
                    st.caption("无（全部池子通过，或没有池子数据）")

            with st.expander("📖 原始数据与扣分原因（透明度）"):
                shown = 0
                for i, r in enumerate(filtered):
                    lines = ([f"未通过：{f}" for f in r["failures"]]
                             + [f"扣分：{d}" for d in r["deductions"]]
                             + [f"价格风险：{t}" for t in r["trend_reasons"]])
                    if lines:
                        shown += 1
                        st.markdown(f"**{i + 1}. {r['pair']}**（评分 {r['score']}）\n\n"
                                    + "\n\n".join(f"- {line}" for line in lines))
                if not shown:
                    st.caption("无扣分项")

            st.caption("手续费口径为毛交易费估算（rhpools fees_basis），非 LP 净收益；"
                       "4h 涨跌用 DexScreener 6h 字段代替（该源无 4h）；池子创建时间不可用时显示为空。")


    # ================================================================ 页面三
    def render_detail_tab() -> None:
        with tab_detail:
            data = run_screening_cached(screening_cfg_json())
            if not data["ok"] or not data["chain"].get("ok"):
                st.error(f"❌ {data['reason'] or '链验证失败'}")
                return
            evaluations = data["all_evaluations"]
            if not evaluations:
                st.info("暂无池子数据")
                return

            by_id = {ev.pool.pool_id: ev for ev in evaluations}
            labels = [f"{ev.pool.pair} ({ev.pool.protocol.upper()}) {ev.pool.pool_id[:14]}…"
                      for ev in evaluations]
            default_id = st.session_state.get("fee_screen_detail_pool")
            default_index = list(by_id).index(default_id) if default_id in by_id else 0
            pick = st.selectbox("选择池子", labels, index=default_index)
            pool_id = list(by_id)[labels.index(pick)]

            # 详情页：重建 K 线后重新评估（列表评估不含 K 线趋势）
            bundle = reevaluate_with_candles(pool_id)
            if bundle is None:
                st.error("池子数据不可用（可能已超出筛选窗口，请稍后重试）")
                return
            selected = bundle["evaluation"]
            market = bundle["market"]
            pool = selected.pool

            st.markdown(f"##### {pool.pair} · {pool.protocol.upper()} · 链 4663 (Robinhood Chain)")
            c1, c2, c3, c4, c5, c6 = st.columns(6)
            c1.metric("24h 成交量", "数据不可用" if pool.volume_usd is None else f"${pool.volume_usd:,.0f}")
            c2.metric("24h 手续费", "数据不可用" if pool.fees_usd is None else f"${pool.fees_usd:,.0f}")
            c3.metric("实际手续费率", "数据不可用" if selected.actual_fee_rate is None
                      else f"{selected.actual_fee_rate:.3%}",
                      f"基础费率 {pool.fee_ppm}ppm" if pool.fee_ppm is not None else None)
            c4.metric("TVL", "数据不可用" if pool.tvl_usd is None else f"${pool.tvl_usd:,.0f}")
            c5.metric("Active TVL", "数据不可用" if pool.active_tvl_usd is None
                      else f"${pool.active_tvl_usd:,.0f}")
            c6.metric("Fees/Active TVL", "数据不可用" if selected.fees_active_tvl is None
                      else f"{selected.fees_active_tvl:.2%}")

            st.caption(f"数据更新时间：{datetime.fromtimestamp(data['fetched_at']).strftime('%m-%d %H:%M:%S')}；"
                       f"窗口完整性：{'✅ 完整' if pool.window_complete else '⚠️ 部分历史'}"
                       f"（覆盖 {pool.window_observed_s or '?'}s）；"
                       f"池子地址：{pool.pool_address_display}；"
                       f"代币：{pool.token0.symbol}({pool.token0.address}) / "
                       f"{pool.token1.symbol}({pool.token1.address})"
                       + (f"｜[DexScreener]({pool.ds_url})" if pool.ds_url else ""))

            windows = pool_window_series(pool.pool_id)

            left, right = st.columns(2)
            with left:
                st.markdown("##### 📈 价格 5 分钟线（链上事件重建）")
                st.caption(market["source"])
                candles = market["candles"]
                if len(candles) >= 2:
                    fig = go.Figure()
                    fig.add_trace(go.Candlestick(
                        x=[datetime.fromtimestamp(c["ts"]) for c in candles],
                        open=[c["open"] for c in candles], high=[c["high"] for c in candles],
                        low=[c["low"] for c in candles], close=[c["close"] for c in candles],
                        name="价格"))
                    fig.add_hline(y=max(c["high"] for c in candles), line_dash="dot",
                                  annotation_text="区间最高")
                    fig.add_hline(y=min(c["low"] for c in candles), line_dash="dot",
                                  annotation_text="区间最低")
                    fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10),
                                      xaxis_rangeslider_visible=False)
                    st.plotly_chart(fig, width="stretch")
                else:
                    st.info("K 线数据不可用：tape 事件不足（该池近期无交易或索引未覆盖）。"
                            "可用参考：DexScreener 聚合涨跌幅 "
                            + " / ".join(f"{k}:{v:+.2f}%"
                                         for k, v in (pool.ds_price_change or {}).items()
                                         if v is not None))

            with right:
                st.markdown("##### 💵 5 分钟成交量 / 手续费（swap 事件重建）")
                flow = market["flow"]
                if flow:
                    df = pd.DataFrame(flow)
                    df["时间"] = [datetime.fromtimestamp(t) for t in df["ts"]]
                    fig = go.Figure()
                    fig.add_bar(x=df["时间"], y=df["volume"], name="成交量($)",
                                marker_color="rgba(70,130,180,0.6)")
                    fig.add_scatter(x=df["时间"], y=df["fees"].cumsum(), name="累计手续费($)",
                                    yaxis="y2", line=dict(color="orange"))
                    fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10),
                                      y2=dict(overlaying="y", side="right", showgrid=False))
                    st.plotly_chart(fig, width="stretch")
                else:
                    st.info("成交量/手续费 5 分钟序列数据不可用（tape 无该池 swap 事件）")

            st.markdown("##### 🗓️ 窗口聚合对比（1h / 24h / 7d / 30d，非逐日曲线）")
            st.caption("rhpools 公开 API 暴露窗口聚合而非每日序列，因此展示窗口对比图；"
                       "Fees/Active TVL 按天归一化（窗口手续费 ÷ 天数 ÷ Active TVL）。")
            wdf = pd.DataFrame({
                "窗口": windows["windows"],
                "成交量($)": windows["volume"],
                "手续费($)": windows["fees"],
                "Active TVL($)": windows["active_tvl"],
                "日均手续费($)": windows["fees_daily"],
                "日均 Fees/ActiveTVL": windows["fat_daily"],
            })
            st.dataframe(wdf, width="stretch", hide_index=True)

            st.markdown("##### 🚨 风险事件与扣分记录")
            risk_items = [
                f"风险等级：{selected.risk_level.value}",
                *[f"价格风险：{r}" for r in selected.trend.reasons],
                *[f"数据风险：{r}" for r in pool.risks],
                *[f"扣分：{d}" for d in selected.deductions],
                *[f"未通过：{f}" for f in selected.failures],
            ]
            if risk_items:
                for item in risk_items:
                    st.markdown(f"- {item}")
            else:
                st.success("未发现风险事件")

            # ---------------- 手续费收入估算 ----------------
            st.divider()
            st.markdown("##### 💰 手续费收入估算")
            f1, f2, f3 = st.columns(3)
            capital = f1.number_input("投入资金（$）", min_value=0.0, value=1000.0, step=100.0)
            in_range_ratio = f2.slider("预计处于有效交易区间的时间比例", 0.0, 1.0, 0.8, 0.05)
            horizon_days = f3.number_input("预计持仓天数", min_value=1, max_value=365, value=30)

            fee_est = estimate_fee_income(pool, capital, in_range_ratio, horizon_days)
            if fee_est.computable:
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("池子每日总手续费", f"${fee_est.pool_daily_fees:,.0f}")
                m2.metric("有效流动性占比（估算）", f"{fee_est.effective_share:.2%}")
                m3.metric("预计每日手续费收入", f"${fee_est.daily_income:,.2f}")
                m4.metric(f"预计 {horizon_days:.0f} 天手续费收入",
                          f"${fee_est.daily_income * horizon_days:,.2f}")
                st.caption(f"7 天约 ${fee_est.income_7d:,.2f}；30 天约 ${fee_est.income_30d:,.2f}")
            else:
                st.error("⚠️ 手续费收入无法估算：")
                for a in fee_est.assumptions[:2]:
                    st.markdown(f"- {a}")
            with st.expander("估算假设（必读）"):
                for a in fee_est.assumptions:
                    st.markdown(f"- {a}")

            # ---------------- 净收益分析 ----------------
            st.markdown("##### 📊 LP 相对净收益分析（手续费收入 ≠ 净利润）")
            n1, n2, n3, n4 = st.columns(4)
            price_change_default = (pool.ds_price_change or {}).get("h24")
            expected_change = n1.number_input(
                "预期价格变化（%，作为持有期代理）",
                min_value=-95.0, max_value=500.0,
                value=float(price_change_default) if price_change_default is not None else 0.0,
                step=1.0, help="默认取 24h 观测涨跌幅，仅作估算代理，非预测。")
            entry_cost = n2.number_input("进入成本（gas 等，$）", min_value=0.0, value=0.0, step=0.1,
                                         help="Robinhood L2 gas 低但无可靠报价，默认 0 并计入假设。")
            exit_cost = n3.number_input("退出成本（$）", min_value=0.0, value=0.0, step=0.1)
            rebalance_cost = n4.number_input("预计再平衡成本（$）", min_value=0.0, value=0.0, step=0.5)

            net_est = estimate_net_return(
                pool, capital, fee_est, horizon_days,
                price_change_pct=expected_change,
                entry_cost=entry_cost, exit_cost=exit_cost, rebalance_cost=rebalance_cost,
            )
            if net_est.computable:
                t1, t2, t3, t4 = st.columns(4)
                t1.metric("LP 最终资产价值（估算）", f"${net_est.lp_final_value:,.2f}")
                t2.metric("基准（50/50 持有）最终价值", f"${net_est.baseline_final_value:,.2f}")
                t3.metric("手续费收入（毛）", f"${net_est.fee_income:,.2f}")
                t4.metric("LP 相对净收益",
                          f"${net_est.relative_net_return:,.2f}",
                          delta=f"{'+' if net_est.relative_net_return >= 0 else ''}"
                                f"{net_est.relative_net_return / capital:.2%}",
                          delta_color="normal")
                st.caption(
                    f"无常损失（常积近似，相对持有）：{net_est.il_pct:.2f}% —— 已体现在"
                    "「LP 最终资产价值 − 基准最终资产价值」中，未重复扣除；"
                    "奖励收入：数据不可用，按 0 计。")
            else:
                st.error(f"⚠️ 净收益无法准确计算：{net_est.unavailable_reason}")
            with st.expander("净收益计算假设"):
                for a in net_est.assumptions:
                    st.markdown(f"- {a}")
                st.markdown(
                    "- 公式：LP 相对净收益 = LP 最终资产价值 − 基准策略最终资产价值"
                    " + 已收取手续费 + 奖励收入 − 额外交易成本")

            st.caption("⚠️ 全部为估算值：rhpools 手续费为毛交易费口径；集中流动性区间收益可用"
                       "「LP 区间 APR」页面的 uni-range-replay 逐笔回放验证。本页面不构成投资建议。")


    # ================================================================ 页面四
    def render_monitor_tab() -> None:
        with tab_monitor:
            st.caption("模块名 fee_screen；实际轮询由主循环（main.py）按 "
                       "module_intervals 执行，此处修改实时生效。")

            st.markdown("##### ⚙️ 报警参数")
            mon = load_monitor_config()
            w1, w2, w3, w4 = st.columns(4)
            volume_drop_pct = w1.number_input("成交量下降阈值（%）", 0.0, 100.0,
                                              float(mon.get("volume_drop_pct", 50.0)), 5.0)
            fees_drop_pct = w2.number_input("手续费下降阈值（%）", 0.0, 100.0,
                                            float(mon.get("fees_drop_pct", 50.0)), 5.0)
            fat_drop_pct = w3.number_input("Fees/Active TVL 下降阈值（%）", 0.0, 100.0,
                                           float(mon.get("fat_drop_pct", 40.0)), 5.0)
            top_n = w4.number_input("关注名单前 N 个", 1, 100, int(mon.get("top_n", 20)), 5)

            if st.button("💾 保存报警设置", type="primary"):
                ok = save_monitor_config({
                    "volume_drop_pct": volume_drop_pct, "fees_drop_pct": fees_drop_pct,
                    "fat_drop_pct": fat_drop_pct, "top_n": int(top_n),
                })
                st.success("✅ 已保存" if ok else "⚠️ 保存失败（数据库不可用）")

            st.markdown("##### ⚡ 立即执行一轮监控")
            if st.button("▶️ 立即执行", width="stretch"):
                with st.spinner("执行链验证 → 筛选 → 对比 → 推送..."):
                    started = time.time()
                    try:
                        summary = run_fee_screen_monitor()
                    except Exception as e:
                        summary = {"alerts_sent": 0, "reason": f"执行异常：{e}"}
                st.info(
                    f"结果：{summary.get('reason')}；名单内池子 {summary.get('pools_in_list', 0)} 个；"
                    f"发送告警 {summary.get('alerts_sent', 0)} 条；耗时 {time.time() - started:.1f}s")

            st.markdown("##### 📡 数据源状态")
            status = src.fetch_status()
            if chain.get("ok"):
                st.markdown(f"- 链验证：✅ {chain['reason']}")
            else:
                st.markdown(f"- 链验证：❌ {chain.get('reason')}")
            if status:
                lag = status.get("lag_s")
                lag_text = f"{lag / 3600:.1f} 小时" if isinstance(lag, (int, float)) else "未知"
                st.markdown(
                    f"- rhpools 索引：state={status.get('state')}，chain_id={status.get('chain_id')}，"
                    f"head={status.get('head')}，落后 {lag_text}"
                    + (" ⚠️（回填中，窗口数据为部分历史）"
                       if isinstance(lag, (int, float)) and lag > src.INDEX_BACKFILL_LAG_S else ""))
            else:
                st.markdown("- rhpools 索引：❌ 不可用（服务未启动或拉起失败）")

            st.markdown("##### 🧾 最近告警记录")
            alerts = list_alerts(50)
            if alerts:
                st.dataframe(
                    [{"时间": a["created_at"], "类型": a["kind"], "池子": a["pool_id"],
                      "内容": a["message"].replace("\n", " ｜ ")} for a in alerts],
                    width="stretch", hide_index=True)
            else:
                st.caption("暂无告警记录（监控尚未触发或数据库不可用）")

            st.caption("报警内容包含池子地址、链、成交量、手续费率、Fees/Active TVL、价格变化、"
                       "风险原因与数据时间；去重后同一风险不会反复推送。")


    def render_observer_tab() -> None:
        with tab_observer:
            st.markdown("##### 📡 池发现、索引服务与链上观测")
            st.caption(
                "外部池发现、GMGN 风险检查、本地 rhpools 索引状态、原始池列表和池间价差都集中在这里。"
            )
            render_robinhood_observer()


    if tab_config.open:
        render_config_tab()
    if tab_pools.open:
        render_pools_tab()
    if tab_detail.open:
        render_detail_tab()
    if tab_monitor.open:
        render_monitor_tab()
    if tab_observer.open:
        render_observer_tab()

if __name__ == "__main__":
    st.set_page_config(page_title="Robinhood 全池扫描", page_icon="🦅", layout="wide")
    render_page()

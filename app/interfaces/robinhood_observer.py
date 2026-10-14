"""Robinhood index observer and DexPaprika discovery widgets for fee_screen."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.infrastructure.market_data import dexpaprika_robinhood
from app.infrastructure.rhpools import client


def render_robinhood_observer() -> None:
    """Render the former Robinhood Pools observer inside the unified scanner page."""
    status = client.health()
    with st.container(border=True):
        st.subheader("🖥️ 后台运行与开始扫描")
        st.caption(
            "启用页面底部的 rhpools 后台监控后，运行中的 `python main.py` 会按设定间隔检查并自动拉起索引服务。"
            "点击下方按钮可立即启动服务；索引器启动后会在后台持续扫描，关闭网页不会中断。"
        )
        address_col, state_col, action_col = st.columns([2, 1, 2])
        with address_col:
            st.code(client.base_url(), language=None)
            st.caption("子模块：modules/robinhoodpools " + ("✅" if client.available() else "❌ 未拉取"))
            st.caption("日志：logs/rhpools.log")
        with state_col:
            st.metric("服务状态", "运行中" if status is not None else "未连接")
        with action_col:
            probe_col, start_col = st.columns(2)
            if probe_col.button("🔄 探活", width="stretch"):
                st.session_state.pop("rhp_last_error", None)
            if start_col.button(
                                "▶️ 拉取并开始扫描" if status is None and not client.available()
                                else "▶️ 启动后台扫描" if status is None
                                else "🔄 刷新扫描状态",
                                type="primary", width="stretch"):
                with st.spinner("准备服务并检查索引状态（首次启动可能需要一些时间）..."):
                    got = client.ensure_running(wait_s=90)
                st.session_state["rhp_last_error"] = None if got else client.last_error()
                st.rerun()
        if status is not None:
            state = str(status.get("state") or "unknown")
            lag_s = status.get("lag_s")
            if state == "degraded":
                st.error("索引服务已运行，但数据源异常；开始扫描按钮无法修复 RPC / 数据源故障，请检查下方来源错误及服务日志。")
            elif isinstance(lag_s, (int, float)) and lag_s > 600:
                st.warning(f"索引正在运行但明显落后（约 {lag_s / 60:.1f} 分钟）；扫描会在后台继续追赶。")
            elif state in {"live", "catching_up", "warming"}:
                st.success(f"后台索引服务运行中（{state}）；扫描持续进行中。")
            else:
                st.info(f"索引服务状态：{state}。可刷新状态或查看下方数据源诊断。")
        st.markdown(f"[打开 rhpools 服务界面]({client.base_url()}/)（服务运行时可用）")

    with st.container(border=True):
        st.subheader("⚙️ 观测参数")
        c1, c2 = st.columns(2)
        min_bps = c1.number_input("价差阈值 (bps)", 0.0, 10000.0, 50.0, 10.0)
        min_depth = c2.number_input("浅边深度下限 ($)", 0.0, 1e6, 300.0, 100.0)

    with st.container(border=True):
        st.subheader("🔎 DexPaprika Robinhood Uniswap 池扫描")
        st.caption(
            "直接从 DexPaprika 的 Robinhood Chain 池搜索 API 获取 Uniswap V2/V3/V4 候选池，"
            "按成交量、流动性和交易数筛选；此扫描不依赖本地 rhpools 区块索引进度。"
        )
        with st.form("dexpaprika_robinhood_scan_form"):
            dex_labels = {
                "全部 Uniswap（V2/V3/V4）": None,
                "Uniswap V2": "uniswap_v2",
                "Uniswap V3": "uniswap_v3",
                "Uniswap V4": "uniswap_v4",
            }
            f1, f2, f3, f4 = st.columns([1.4, 1.2, 1.2, 1])
            selected_dex = f1.selectbox("协议", list(dex_labels))
            scan_min_volume = f2.number_input("最低 24h 成交量 ($)", min_value=0.0,
                                              value=100_000.0, step=25_000.0)
            scan_min_liquidity = f3.number_input("最低流动性 ($)", min_value=0.0,
                                                 value=25_000.0, step=10_000.0)
            scan_min_txns = f4.number_input("最低 24h 交易数", min_value=0, value=20, step=10)
            risk_col1, risk_col2, risk_col3 = st.columns([1.2, 1, 1.5])
            enable_risk_filter = risk_col1.checkbox("启用基础貔貅风险过滤", value=True)
            min_token_sells = risk_col2.number_input("每个代币最低 24h 卖出数", min_value=0,
                                                     value=5, step=1,
                                                     disabled=not enable_risk_filter)
            max_buy_sell_ratio = risk_col3.number_input("最大买/卖笔数比", min_value=1.0,
                                                        value=20.0, step=1.0,
                                                        disabled=not enable_risk_filter)
            gmgn_col1, gmgn_col2 = st.columns([1.3, 1])
            enable_gmgn_check = gmgn_col1.checkbox(
                "使用 GMGN 检测貔貅合约", value=True,
                help="按每个唯一代币地址查询一次 GMGN Robinhood 安全接口；查询失败或无结论标为未知。",
            )
            max_gmgn_checks = gmgn_col2.selectbox(
                "GMGN 最多查询合约数", [50, 100, 200, 500], index=1,
                disabled=not enable_gmgn_check,
            )
            if enable_gmgn_check:
                st.caption("每个唯一代币地址消耗一次 GMGN 查询；超出上限的地址显示为未知并需人工复核。")
            scan_limit = st.selectbox("最多返回池数", [100, 300, 600], index=1)
            do_scan = st.form_submit_button("🔎 扫描符合条件的池子", type="primary")

        if do_scan:
            with st.spinner("正在查询 DexPaprika Robinhood Uniswap 池数据…"):
                scan_result = dexpaprika_robinhood.search_uniswap_pools(
                        dex_id=dex_labels[selected_dex],
                        min_volume_24h=scan_min_volume,
                        min_liquidity=scan_min_liquidity,
                        min_transactions_24h=int(scan_min_txns),
                        max_results=int(scan_limit),
                    )
                gmgn_status = {}
                if enable_gmgn_check and scan_result.get("rows"):
                    token_addresses = list(dict.fromkeys(
                        address
                        for row in scan_result["rows"]
                        for token in (row.get("tokens") or [])
                        if isinstance(token, dict)
                        for address in [dexpaprika_robinhood.token_address(token)]
                        if address
                    ))
                    checks = token_addresses[:int(max_gmgn_checks)]
                    if dexpaprika_robinhood.gmgn_api_key():
                        progress = st.progress(0, text=f"GMGN 貔貅检查：0 / {len(checks)}")
                        for index, address in enumerate(checks, start=1):
                            gmgn_status[address] = dexpaprika_robinhood.gmgn_honeypot_check(address)
                            progress.progress(index / max(1, len(checks)),
                                              text=f"GMGN 貔貅检查：{index} / {len(checks)}")
                        progress.empty()
                    else:
                        gmgn_status = {
                            address: {"status": "unknown", "reason": "未配置 GMGN_API_KEY"}
                            for address in token_addresses
                        }
                    for address in token_addresses[len(checks):]:
                        gmgn_status[address] = {
                            "status": "unknown", "reason": f"达到本次查询上限（{max_gmgn_checks}）",
                        }
                    for row in scan_result["rows"]:
                        token_checks = []
                        for token in (row.get("tokens") or []):
                            if not isinstance(token, dict):
                                continue
                            address = dexpaprika_robinhood.token_address(token)
                            if address:
                                token_checks.append({
                                    "address": address,
                                    "symbol": token.get("symbol") or token.get("name") or address[:10],
                                    **gmgn_status.get(address, {
                                        "status": "unknown", "reason": "缺少 GMGN 检查结果",
                                    }),
                                })
                        row["gmgn_checks"] = token_checks
                scan_result.update({
                    "risk_filter_enabled": enable_risk_filter,
                    "min_token_sells": int(min_token_sells),
                    "max_buy_sell_ratio": float(max_buy_sell_ratio),
                    "gmgn_filter_enabled": enable_gmgn_check,
                    "gmgn_status": gmgn_status,
                })
                st.session_state["dexpaprika_robinhood_scan"] = scan_result

        scan_result = st.session_state.get("dexpaprika_robinhood_scan")
        if scan_result:
            if scan_result.get("reason"):
                (st.warning if scan_result.get("ok") else st.error)(scan_result["reason"])
            scan_rows = scan_result.get("rows") or []
            if scan_rows:
                risk_filter_on = bool(scan_result.get("risk_filter_enabled"))
                risk_checked = [
                    (row, dexpaprika_robinhood.basic_token_trade_risk(
                        row,
                        min_sells_per_token=int(scan_result.get("min_token_sells", 5)),
                        max_buy_sell_ratio=float(scan_result.get("max_buy_sell_ratio", 20.0)),
                    ))
                    for row in scan_rows
                ]
                basic_accepted = [(row, risk) for row, risk in risk_checked
                                  if risk["passed"] or not risk_filter_on]
                gmgn_filter_on = bool(scan_result.get("gmgn_filter_enabled"))
                gmgn_rejected = [
                    (row, risk) for row, risk in basic_accepted
                    if gmgn_filter_on and any(
                        item.get("status") == "honeypot"
                        for item in row.get("gmgn_checks", [])
                    )
                ]
                accepted = [(row, risk) for row, risk in basic_accepted
                            if (row, risk) not in gmgn_rejected]
                rejected = [(row, risk) for row, risk in risk_checked if not risk["passed"]]
                st.success(
                    f"DexPaprika 返回 {len(scan_rows):,} 个候选池；"
                    f"当前结果 {len(accepted):,} 个；基础活动风险排除 {len(rejected):,} 个，"
                    f"GMGN 标记貔貅排除 {len(gmgn_rejected):,} 个。"
                )
                st.caption(
                    "基础风险过滤检查每个代币的 24h 卖出笔数和买卖失衡程度；"
                    "GMGN 未知/未覆盖的代币会保留并标记人工复核，不能视为安全通过。"
                )

                def gmgn_summary(row):
                    checks = row.get("gmgn_checks") or []
                    if not gmgn_filter_on:
                        return "未启用"
                    if any(item.get("status") == "honeypot" for item in checks):
                        return "貔貅（已排除）"
                    if checks and all(item.get("status") == "clear" for item in checks):
                        return "GMGN 未标记貔貅"
                    return "未知 / 人工复核"

                def gmgn_reason(row):
                    checks = row.get("gmgn_checks") or []
                    return "；".join(
                        f"{item.get('symbol')}: {item.get('reason')}"
                        for item in checks if item.get("status") == "unknown"
                    ) or "—"

                unknown_pool_count = sum(
                    gmgn_summary(row) == "未知 / 人工复核" for row, _ in accepted
                )
                if gmgn_filter_on:
                    st.caption(f"当前结果中有 {unknown_pool_count:,} 个池至少含一个未验证代币。")

                scan_df = pd.DataFrame.from_records([{
                    "池地址 / ID": row.get("id"),
                    "协议": row.get("dex_name") or row.get("dex_id"),
                    "交易对": "/".join(
                        str(token.get("symbol") or token.get("name") or "?")
                        for token in (row.get("tokens") or []) if isinstance(token, dict)
                    ),
                    "价格 ($)": row.get("price_usd"),
                    "流动性 ($)": row.get("liquidity_usd"),
                    "24h 成交量 ($)": row.get("volume_usd_24h"),
                    "24h 交易数": row.get("transactions_24h"),
                    "1h 涨跌 (%)": row.get("price_change_percentage_1h"),
                    "24h 涨跌 (%)": row.get("price_change_percentage_24h"),
                    "最低代币卖出数": risk.get("min_sells"),
                    "最高买/卖比": risk.get("max_ratio"),
                    "基础风险筛查": "通过" if risk.get("passed") else "需排查",
                    "风险原因": risk.get("reason") or "—",
                    "GMGN 貔貅检测": gmgn_summary(row),
                    "GMGN 未知原因": gmgn_reason(row),
                    "创建时间": row.get("created_at"),
                } for row, risk in accepted])
                if not scan_df.empty:
                    st.dataframe(scan_df, width="stretch", hide_index=True)
                else:
                    st.warning("当前门槛下的候选池均被基础风险过滤排除。")
                if rejected:
                    with st.expander(f"查看被排除的池（{len(rejected)}）"):
                        rejected_df = pd.DataFrame.from_records([{
                            "池地址 / ID": row.get("id"),
                            "协议": row.get("dex_name") or row.get("dex_id"),
                            "交易对": "/".join(
                                str(token.get("symbol") or token.get("name") or "?")
                                for token in (row.get("tokens") or []) if isinstance(token, dict)
                            ),
                            "最低代币卖出数": risk.get("min_sells"),
                            "最高买/卖比": risk.get("max_ratio"),
                            "风险原因": risk.get("reason"),
                        } for row, risk in rejected])
                        st.dataframe(rejected_df, width="stretch", hide_index=True)
                if gmgn_rejected:
                    with st.expander(f"查看 GMGN 标记为貔貅、已排除的池（{len(gmgn_rejected)}）"):
                        gmgn_df = pd.DataFrame.from_records([{
                            "池地址 / ID": row.get("id"),
                            "协议": row.get("dex_name") or row.get("dex_id"),
                            "交易对": "/".join(str(token.get("symbol") or token.get("name") or "?")
                                                for token in (row.get("tokens") or [])
                                                if isinstance(token, dict)),
                            "GMGN 风险代币": ", ".join(
                                str(item.get("symbol")) for item in row.get("gmgn_checks", [])
                                if item.get("status") == "honeypot"
                            ),
                        } for row, _ in gmgn_rejected])
                        st.dataframe(gmgn_df, width="stretch", hide_index=True)
                st.download_button(
                    "⬇️ 下载扫描结果 CSV",
                    scan_df.to_csv(index=False).encode("utf-8-sig"),
                    file_name="robinhood_uniswap_pools.csv",
                    mime="text/csv",
                )
            elif scan_result.get("ok"):
                st.info("没有池子满足当前筛选条件。可降低成交量或流动性门槛后重新扫描。")
    if status is None:
        st.warning("rhpools 索引服务尚未连接。DexPaprika 候选池扫描仍可使用；启动索引服务后可查看链上池列表与价差。")
        if st.session_state.get("rhp_last_error"):
            st.error("⛔ 上次启动失败：\n\n```\n"
                     + st.session_state["rhp_last_error"] + "\n```")
        with st.expander("手动启动（本机终端）"):
            st.code(
                "git submodule update --init\n"
                "PYTHONPATH=modules/robinhoodpools/src python -m rhpools.lp_server",
                language="bash",
            )
    else:

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

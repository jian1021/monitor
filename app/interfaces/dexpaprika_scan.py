"""DexPaprika Robinhood Uniswap pool discovery widget."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.infrastructure.market_data import dexpaprika_robinhood


def render_dexpaprika_scan() -> None:
    """Render the DexPaprika Robinhood Uniswap pool scanner."""
    with st.container(border=True):
        st.subheader("🔎 DexPaprika Robinhood Uniswap 池扫描")
        st.caption(
            "直接从 DexPaprika 的 Robinhood Chain 池搜索 API 获取 Uniswap V2/V3/V4 候选池，"
            "按成交量、流动性（TVL）、交易数、代币市值（FDV）与池费率筛选。"
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
                                              value=1_000_000.0, step=100_000.0)
            scan_min_liquidity = f3.number_input("最低流动性/TVL ($)", min_value=0.0,
                                                 value=10_000.0, step=5_000.0)
            scan_min_txns = f4.number_input("最低 24h 交易数", min_value=0, value=30, step=10)
            g1, g2 = st.columns(2)
            scan_min_fdv = g1.number_input("最低代币市值 FDV ($)", min_value=0.0,
                                           value=100_000.0, step=50_000.0,
                                           help="要求池中每个代币的 FDV 都达标；任一代币缺失 FDV 视为不满足。")
            scan_min_fee_pct = g2.number_input("最低池费率 (%)", min_value=0.0,
                                               value=0.2, step=0.05,
                                               help="按 DexPaprika 上报的池费率过滤；V2 等未上报费率的池会被排除。")
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
                    "min_token_fdv": float(scan_min_fdv),
                    "min_fee_rate": float(scan_min_fee_pct) / 100.0,
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
                min_fdv_gate = float(scan_result.get("min_token_fdv", 100_000.0) or 0)
                min_fee_gate = float(scan_result.get("min_fee_rate", 0.002) or 0)
                gate_checked = [
                    (row, dexpaprika_robinhood.apply_pool_thresholds(
                        row,
                        min_token_fdv=min_fdv_gate,
                        min_fee_rate=min_fee_gate,
                    ))
                    for row in scan_rows
                ]
                gate_passed = [(row, gate) for row, gate in gate_checked if gate["passed"]]
                gate_rejected = [(row, gate) for row, gate in gate_checked if not gate["passed"]]
                risk_filter_on = bool(scan_result.get("risk_filter_enabled"))
                risk_checked = [
                    (row, dexpaprika_robinhood.basic_token_trade_risk(
                        row,
                        min_sells_per_token=int(scan_result.get("min_token_sells", 5)),
                        max_buy_sell_ratio=float(scan_result.get("max_buy_sell_ratio", 20.0)),
                    ), gate)
                    for row, gate in gate_passed
                ]
                basic_accepted = [(row, risk, gate) for row, risk, gate in risk_checked
                                  if risk["passed"] or not risk_filter_on]
                gmgn_filter_on = bool(scan_result.get("gmgn_filter_enabled"))
                gmgn_rejected = [
                    (row, risk, gate) for row, risk, gate in basic_accepted
                    if gmgn_filter_on and any(
                        item.get("status") == "honeypot"
                        for item in row.get("gmgn_checks", [])
                    )
                ]
                rejected_ids = {(id(row), id(risk), id(gate)) for row, risk, gate in gmgn_rejected}
                accepted = [(row, risk, gate) for row, risk, gate in basic_accepted
                            if (id(row), id(risk), id(gate)) not in rejected_ids]
                rejected = [(row, risk) for row, risk, _ in risk_checked if not risk["passed"]]
                st.success(
                    f"DexPaprika 返回 {len(scan_rows):,} 个候选池；"
                    f"当前结果 {len(accepted):,} 个；市值/费率门槛排除 {len(gate_rejected):,} 个，"
                    f"基础活动风险排除 {len(rejected):,} 个，"
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
                    gmgn_summary(row) == "未知 / 人工复核" for row, _, _ in accepted
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
                    "费率": (f"{gate.get('fee_rate'):.2%}"
                             if gate.get("fee_rate") is not None else "未知"),
                    "最大代币市值 ($)": gate.get("max_fdv"),
                    "1h 涨跌 (%)": row.get("price_change_percentage_1h"),
                    "24h 涨跌 (%)": row.get("price_change_percentage_24h"),
                    "最低代币卖出数": risk.get("min_sells"),
                    "最高买/卖比": risk.get("max_ratio"),
                    "基础风险筛查": "通过" if risk.get("passed") else "需排查",
                    "风险原因": risk.get("reason") or "—",
                    "GMGN 貔貅检测": gmgn_summary(row),
                    "GMGN 未知原因": gmgn_reason(row),
                    "创建时间": row.get("created_at"),
                } for row, risk, gate in accepted])
                if not scan_df.empty:
                    st.dataframe(scan_df, width="stretch", hide_index=True)
                else:
                    st.warning("当前门槛下的候选池均被门槛或风险过滤排除。")
                if gate_rejected:
                    with st.expander(f"查看未达市值/费率门槛的池（{len(gate_rejected)}）"):
                        gate_df = pd.DataFrame.from_records([{
                            "池地址 / ID": row.get("id"),
                            "协议": row.get("dex_name") or row.get("dex_id"),
                            "交易对": "/".join(
                                str(token.get("symbol") or token.get("name") or "?")
                                for token in (row.get("tokens") or []) if isinstance(token, dict)
                            ),
                            "流动性 ($)": row.get("liquidity_usd"),
                            "费率": (f"{gate.get('fee_rate'):.2%}"
                                     if gate.get("fee_rate") is not None else "未知"),
                            "最大代币市值 ($)": gate.get("max_fdv"),
                            "原因": gate.get("reason"),
                        } for row, gate in gate_rejected])
                        st.dataframe(gate_df, width="stretch", hide_index=True)
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
                        } for row, _, _ in gmgn_rejected])
                        st.dataframe(gmgn_df, width="stretch", hide_index=True)
                st.download_button(
                    "⬇️ 下载扫描结果 CSV",
                    scan_df.to_csv(index=False).encode("utf-8-sig"),
                    file_name="robinhood_uniswap_pools.csv",
                    mime="text/csv",
                )
            elif scan_result.get("ok"):
                st.info("没有池子满足当前筛选条件。可降低成交量或流动性门槛后重新扫描。")

"""
Meteora 全池扫描与筛选器 V3.1 - Streamlit 主程序。

页面：
  1. 全池扫描  2. 筛选条件  3. 筛选结果  4. API 状态与错误日志  5. 使用说明

数据全部来自 Meteora 官方 DLMM API，绝不生成模拟数据。
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from app.application.meteora_scanner import config
from app.application.meteora_scanner.db import Database
from app.application.meteora_scanner.filters import apply_filters, to_bool
from app.application.meteora_scanner.price_range import (DEFAULT_ATR_PERIOD, DEFAULT_MULTIPLIER, DEFAULT_POOL_TIMEFRAME,
                             DEFAULT_TIMEFRAME, POOL_TIMEFRAMES, TIMEFRAMES, compute_range,
                             fetch_market, fetch_pool_market, resolve_coin, resolve_pool)
from app.application.meteora_scanner.providers import coingecko, dexpaprika
from app.application.meteora_scanner.scanner import Scanner
from app.application.meteora_scanner.scoring import (COMPONENT_LABELS, DEFAULT_WEIGHTS, compute_score,
                         formula_text, stability_score, volatility_score_fallback)
from app.interfaces.monitor_controls import render_monitor_controls

if __name__ == "__main__":
    st.set_page_config(page_title=config.APP_TITLE, page_icon="📊", layout="wide")

# ===========================================================================
# 资源
# ===========================================================================
@st.cache_resource(show_spinner=False)
def get_db() -> Database:
    return Database(config.DB_PATH)


@st.cache_resource(show_spinner=False)
def get_scanner() -> Scanner:
    return Scanner(get_db())


@st.cache_resource(show_spinner=False)
def get_coingecko():
    """页面⑨ 专用 CoinGecko 客户端（自动携带 COINGECKO_API_KEY 请求头）。"""
    return coingecko.new_client(base_url=config.COINGECKO_BASE_URL,
                                requests_per_second=config.COINGECKO_RPS)


@st.cache_resource(show_spinner=False)
def get_dexpaprika():
    """页面⑨ 专用 DEX Paprika 客户端（自动携带 DEXPAPRIKA_API_KEY 请求头）。"""
    return dexpaprika.new_client(base_url=config.DEXPAPRIKA_BASE_URL,
                                 requests_per_second=config.DEXPAPRIKA_RPS)


def load_pools(_signature: str) -> list[dict]:
    """Load pools only when the session evaluation cache is stale.

    Avoid st.cache_data here: serializing a full scan result into Streamlit's
    process-wide cache duplicates a potentially very large pool list. The
    filtered evaluation is already cached per session by get_evaluated().
    """
    return get_db().load_pools(config.SUPPORTED_POOL_TYPES)


def data_signature() -> str:
    sig = st.session_state.get("refresh_counter", 0)
    rows = get_db().get_scan_status()
    tail = ",".join(f"{r.get('pool_type')}:{r.get('last_scan_time')}" for r in rows)
    return f"{sig}|{tail}|{get_db().count_pools()}"


def get_filters() -> dict:
    if "filters" not in st.session_state:
        saved = get_db().get_setting("filters")
        st.session_state.filters = {**config.DEFAULT_FILTERS, **(saved or {})}
        st.session_state.filters["pool_types"] = [
            pt for pt in st.session_state.filters.get("pool_types", [])
            if pt in config.SUPPORTED_POOL_TYPES
        ] or list(config.SUPPORTED_POOL_TYPES)
    return st.session_state.filters


def get_weights() -> dict:
    if "weights" not in st.session_state:
        saved = get_db().get_setting("weights")
        st.session_state.weights = {**config.DEFAULT_WEIGHTS, **(saved or {})}
    return st.session_state.weights


def get_evaluated() -> list[dict]:
    """返回按当前筛选条件评估后的池列表。

    结果缓存在 session_state 中，仅当数据签名或筛选条件变化时重新计算，
    避免每次交互重复对全量池（可能数十万条）执行筛选。
    """
    filters = get_filters()
    signature = data_signature()
    key = f"{signature}|{json.dumps(filters, sort_keys=True, ensure_ascii=False)}"
    cache = st.session_state.get("_eval_cache")
    if not cache or cache.get("key") != key:
        cache = {"key": key, "rows": apply_filters(load_pools(signature), filters)}
        st.session_state["_eval_cache"] = cache
    return cache["rows"]


# ===========================================================================
# 格式化
# ===========================================================================
def fmt_num(v, digits=2) -> str:
    if v is None:
        return "-"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    if abs(f) >= 1_000_000:
        return f"{f / 1_000_000:,.2f}M"
    if abs(f) >= 1_000:
        return f"{f / 1_000:,.2f}K"
    return f"{f:,.{digits}f}"


def now_str() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


# ===========================================================================
# 顶部状态栏（文档 11.）
# ===========================================================================
def render_status_bar(embedded: bool = False) -> None:
    db = get_db()
    counts = db.count_pools()
    status = {r["pool_type"]: r for r in db.get_scan_status()}
    scanner = get_scanner()
    prog = scanner.progress

    running_types = [pt for pt, p in prog.items() if p.get("state") == "running"]
    current = "、".join(running_types) if running_types else "空闲"

    dlmm_found = status.get(config.POOL_TYPE_DLMM, {}).get("pools_discovered") or counts.get(config.POOL_TYPE_DLMM, 0)
    dlmm_status = status.get(config.POOL_TYPE_DLMM, {})
    dlmm_found = dlmm_status.get("pools_discovered") or counts.get(config.POOL_TYPE_DLMM, 0)
    processed = dlmm_status.get("pools_processed") or 0
    failures = dlmm_status.get("failures") or 0
    last_scan = dlmm_status.get("last_scan_time") or "-"

    if not embedded:
        st.markdown(f"### 📈 {config.APP_TITLE} {config.APP_VERSION}")
    c = st.columns(5)
    c[0].metric("当前扫描类型", current)
    c[1].metric("DLMM 已发现", f"{dlmm_found:,}")
    c[2].metric("已处理池数", f"{processed:,}")
    c[3].metric("API 失败", f"{failures:,}")
    c[4].metric("最后扫描", last_scan[11:19] if len(last_scan) > 11 else last_scan)

    # 覆盖范围
    cov = []
    for pt in config.SUPPORTED_POOL_TYPES:
        s = status.get(pt, {})
        avail = s.get("total_available")
        disc = s.get("pools_discovered")
        if avail:
            cov.append(f"{pt}: 已发现 {disc:,} / API 报告 {avail:,}")
        elif disc:
            cov.append(f"{pt}: 已发现 {disc:,}")
    if cov:
        st.caption("实际数据覆盖范围（以 API 可访问页面为准，不等于链上全部历史/现存池）：" + "；".join(cov))
    st.divider()


# ===========================================================================
# 页面 1：全池扫描
# ===========================================================================
def page_scan() -> None:
    st.header("① 全池扫描")
    st.caption("扫描 Meteora DLMM 流动池。支持分页、去重、断点续扫。")
    render_monitor_controls("meteora_scan", "Meteora DLMM 全池扫描")

    scanner = get_scanner()
    db = get_db()

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        types = st.multiselect("扫描池类型", config.SUPPORTED_POOL_TYPES,
                               default=list(config.SUPPORTED_POOL_TYPES))
    with col2:
        max_pages = st.number_input("最多扫描页数（0=不限，全量）", min_value=0, value=0, step=10)
    with col3:
        resume = st.checkbox("断点续扫", value=True, help="从上次中断的页继续")
    with col4:
        st.caption("后台扫描需保持 `python main.py` 运行。")

    b1, b2, b3 = st.columns(3)
    with b1:
        start = st.button("▶️ 开始扫描", type="primary", width="stretch",
                          disabled=scanner.is_running())
    with b2:
        stop = st.button("⏹ 停止", width="stretch", disabled=not scanner.is_running())
    with b3:
        if st.button("🔄 刷新数据缓存", width="stretch"):
            st.session_state.refresh_counter = st.session_state.get("refresh_counter", 0) + 1
            st.cache_data.clear()
            st.rerun()

    if stop:
        scanner.request_stop()
        st.warning("已发送停止信号，将在当前页完成后停止。")

    if start and not scanner.is_running():
        scanner.reset_stop()
        st.session_state.scan_running = True
        st.session_state.scan_started_at = time.time()

        def _run():
            scanner.scan(types, resume=resume, max_pages=(max_pages or None))

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        st.session_state.scan_thread = t

    # 实时进度
    if st.session_state.get("scan_running"):

        @st.fragment(run_every=1.0)
        def _progress_fragment():
            sc = get_scanner()
            prog = sc.progress
            if not prog:
                return
            st.markdown("#### 扫描进度")
            for pt, p in prog.items():
                total_pages = p.get("total_pages") or "?"
                st.markdown(
                    f"**{pt}** — 状态：`{p.get('state')}` ｜ 已扫描页：{p.get('pages_scanned')}/{total_pages} "
                    f"｜ 已发现：{p.get('discovered'):,} ｜ 已处理：{p.get('processed'):,} "
                    f"｜ 失败：{p.get('failures')} ｜ {p.get('message') or ''}")
                if p.get("total_pages"):
                    st.progress(min(1.0, p["pages_scanned"] / max(1, p["total_pages"])))
            if not sc.is_running():
                st.session_state.scan_running = False
                st.session_state.refresh_counter = st.session_state.get("refresh_counter", 0) + 1
                st.toast("扫描结束。", icon="✅")
                st.rerun()

        _progress_fragment()
    else:
        st.info("未在扫描。点击“开始扫描”以获取全池数据。首次运行会遍历全部可访问页面，需要一定时间。")

    st.divider()
    st.subheader("扫描状态（持久化）")
    status = db.get_scan_status()
    if status:
        st.dataframe(pd.DataFrame(status), width="stretch", hide_index=True)
    else:
        st.caption("尚无扫描记录。")

    st.caption("后台扫描完整结束后，会按已保存的筛选条件自动评估结果并发送飞书报警。")


# ===========================================================================
# 页面 2：筛选条件
# ===========================================================================
def page_filters() -> None:
    st.header("② 筛选条件")
    st.caption("以下为默认值，可随时修改并保存。市值筛选为硬性条件，默认在展示前执行。")
    f = get_filters()

    with st.form("filter_form"):
        c1, c2 = st.columns(2)
        with c1:
            min_mcap = st.number_input("代币市值下限（USD，严格大于）", min_value=0.0,
                                       value=float(f["min_market_cap"]), step=100_000.0)
            mode_label = st.radio("市值满足模式",
                                  ["任一代币满足", "两个代币均满足"],
                                  index=0 if f.get("market_cap_mode", "any") == "any" else 1)
            min_vol = st.number_input("24h 成交量下限（USD，≥）", min_value=0.0,
                                      value=float(f["min_volume_24h"]), step=10_000.0)
        with c2:
            min_tvl = st.number_input("TVL 下限（USD，≥）", min_value=0.0,
                                       value=float(f["min_tvl"]), step=10_000.0)
            min_ftr = st.number_input("24h Fees/TVL 下限（%，≥）", min_value=0.0,
                                       value=float(f["min_fee_tvl_ratio"]), step=0.1, format="%.3f")
        c3, c4 = st.columns(2)
        with c3:
            bs_min = st.number_input("DLMM bin step 最小值", min_value=0, step=1,
                                     value=int(f.get("bin_step_min", 80)))
            bs_max = st.number_input("DLMM bin step 最大值", min_value=0, step=1,
                                     value=int(f.get("bin_step_max", 0)))
            st.caption("bin step 仅对 DLMM 生效，0 表示不限制")
        with c4:
            min_fee_rate = st.number_input("费率下限（%）", min_value=0.0, step=0.01,
                                          value=float(f.get("min_fee_rate_pct", 0.0)), format="%.3f")
            max_fee_rate = st.number_input("费率上限（%）", min_value=0.0, step=0.01,
                                          value=float(f.get("max_fee_rate_pct", 0.0)), format="%.3f")
            st.caption("0 表示不限制")
        
        # 持仓筛选条件
        st.subheader("代币持仓筛选")
        st.caption("数据来自 Meteora 官方池发现接口（随扫描一并获取）；数据缺失的指标会标注"
                   "「数据不足（已跳过）」而不误杀。")
        c5, c6, c7 = st.columns(3)
        with c5:
            min_holders = st.number_input("代币X持仓地址数下限", min_value=0, step=1000,
                                         value=int(f.get("min_token_x_holders", 10000)))
            st.caption("默认 10K")
        with c6:
            max_top10 = st.number_input("前10持仓占比上限（%）", min_value=0.0, max_value=100.0, step=1.0,
                                        value=float(f.get("max_token_x_top10_holders", 30.0)), format="%.1f")
            st.caption("默认 30%")
        with c7:
            max_dev = st.number_input("开发方持仓占比上限（%）", min_value=0.0, max_value=100.0, step=1.0,
                                     value=float(f.get("max_token_x_dev_holders", 0.0)), format="%.1f")
            st.caption("默认 0%（按 2 位小数精度比较，接近 0 的真实值不会被误判）")
        
        c8, c9 = st.columns(2)
        with c8:
            freeze_auth = st.selectbox("冻结授权", ["any", "yes", "no"], 
                                      index=0 if f.get("token_x_freeze_auth", "no") == "any" else 
                                            (1 if f.get("token_x_freeze_auth", "no") == "yes" else 2))
            st.caption("默认 no")
        with c9:
            mint_auth = st.selectbox("铸币授权", ["any", "yes", "no"], 
                                    index=0 if f.get("token_x_mint_auth", "no") == "any" else 
                                          (1 if f.get("token_x_mint_auth", "no") == "yes" else 2))
            st.caption("默认 no")
        
        # 开仓人数筛选
        min_open_positions = st.number_input(
            "开仓人数下限", min_value=0, step=10,
            value=int(f.get("min_open_positions", 100)),
            help="数据来自 Meteora 官方池发现接口（open_positions），随扫描一并获取。")
        st.caption("数据缺失时标记为「数据不足（已跳过）」，不参与淘汰。")
        
        pool_types = st.multiselect("池子类型", config.SUPPORTED_POOL_TYPES,
                                    default=[pt for pt in f.get("pool_types", config.SUPPORTED_POOL_TYPES)
                                             if pt in config.SUPPORTED_POOL_TYPES])
        require_complete = st.checkbox("要求关键指标完整（缺失则不通过）",
                                       value=bool(f.get("require_complete", True)))
        show_failed = st.checkbox("显示未通过筛选的池子（排查用）",
                                  value=bool(f.get("show_failed", False)))
        submitted = st.form_submit_button("保存筛选条件", type="primary")

    if submitted:
        f.update({
            "min_market_cap": min_mcap,
            "market_cap_mode": "any" if mode_label == "任一代币满足" else "both",
            "min_volume_24h": min_vol,
            "min_tvl": min_tvl,
            "min_fee_tvl_ratio": min_ftr,
            "bin_step_min": bs_min,
            "bin_step_max": bs_max,
            "min_fee_rate_pct": min_fee_rate,
            "max_fee_rate_pct": max_fee_rate,
            "min_token_x_holders": min_holders,
            "max_token_x_top10_holders": max_top10,
            "max_token_x_dev_holders": max_dev,
            "token_x_freeze_auth": "any" if freeze_auth == "any" else ("yes" if freeze_auth == "yes" else "no"),
            "token_x_mint_auth": "any" if mint_auth == "any" else ("yes" if mint_auth == "yes" else "no"),
            "min_open_positions": min_open_positions,
            "pool_types": pool_types,
            "require_complete": require_complete,
            "show_failed": show_failed,
        })
        get_db().set_setting("filters", f)
        st.success("已保存。")

    st.divider()
    st.subheader("评分权重（手续费机会观察评分）")
    st.caption(formula_text(get_weights()))
    w = get_weights()
    with st.form("weight_form"):
        cols = st.columns(5)
        
        keys = list(DEFAULT_WEIGHTS.keys())
        vals = {}
        for col, k in zip(cols, keys):
            vals[k] = col.number_input(COMPONENT_LABELS[k], min_value=0.0, max_value=100.0,
                                       value=float(w[k]), step=1.0)
        if st.form_submit_button("保存权重"):
            get_weights().update(vals)
            get_db().set_setting("weights", get_weights())
            st.success("已保存权重。")


# ===========================================================================
# 页面 3：筛选结果
# ===========================================================================
def _auth_label(v) -> str:
    """把布尔授权位格式化为 是 / 否 / 不可用（数据缺失）。"""
    b = to_bool(v)
    if b is None:
        return "不可用"
    return "是" if b else "否"


def _build_result_df(rows: list[dict], show_failed: bool) -> pd.DataFrame:
    weights = get_weights()
    data = []
    for r in rows:
        if not show_failed and not r["passed"]:
            continue
        sc = compute_score(r, weights, stability=None,
                           volatility=volatility_score_fallback(r.get("price_change_24h")))
        which = {"x": "代币X", "y": "代币Y", "both": "两者", "none": "无"}.get(r.get("mcap_which"), "-")
        data.append({
            "通过": "✅" if r["passed"] else "❌",
            "池类型": r.get("pool_type"),
            "池地址": r.get("pool_address"),
            "代币X": r.get("token_x_symbol") or r.get("token_x_name") or "-",
            "代币X市值": r.get("token_x_market_cap"),
            "代币Y": r.get("token_y_symbol") or r.get("token_y_name") or "-",
            "代币Y市值": r.get("token_y_market_cap"),
            "满足市值": which,
            "当前价格": r.get("price"),
            "TVL": r.get("tvl"),
            "24h成交量": r.get("volume_24h"),
            "24h手续费收入": r.get("fees_24h"),
            "配置手续费率%": r.get("fee_rate_pct"),
            "binStep": r.get("bin_step"),
            "代币X持仓数": r.get("token_x_holders"),
            "前10持仓%": r.get("token_x_top10_holders"),
            "开发方持仓%": r.get("token_x_dev_holders"),
            "冻结授权": _auth_label(r.get("token_x_freeze_auth")),
            "铸币授权": _auth_label(r.get("token_x_mint_auth")),
            "开仓人数": r.get("open_positions"),
            "24hFeesTVL%": r.get("fee_tvl_ratio_24h"),
            "24h价格变化%": r.get("price_change_24h"),
            "评分": sc["score"],
            "数据更新时间": r.get("last_updated"),
            "未通过原因": "；".join(r.get("reasons") or []),
        })
    return pd.DataFrame(data)


def page_results() -> None:
    st.header("③ 筛选结果")
    flash = st.session_state.pop("_results_flash", None)
    if flash:
        st.success(flash)
    f = get_filters()
    rows = get_evaluated()
    df = _build_result_df(rows, bool(f.get("show_failed", False)))

    if df.empty:
        st.info("暂无符合条件的池子。请先执行“全池扫描”，或放宽筛选条件。")
        return

    st.caption(f"共 {len(df):,} 条（{'含未通过' if f.get('show_failed') else '仅通过'}）。"
               f"市值缺失（默认不通过）需在排查模式查看。")
    st.caption("数据来源：池行情与代币指标（持仓数 / 前10持仓占比 / 开发方持仓占比 / 冻结与铸币授权 / "
               "开仓人数）均来自 Meteora 官方池发现接口，随扫描一并获取；缺失字段标记为「数据不足（已跳过）」。")
    st.caption("行情为上次扫描时的快照。可勾选通过筛选的结果，并在下方导入 Meme 超卖超买监控列表。")

    sort_col = st.selectbox("排序字段", ["评分", "代币X市值", "代币Y市值", "24h成交量", "TVL",
                                          "24h手续费收入", "24hFeesTVL%"])
    ascending = st.checkbox("升序", value=False)
    df_sorted = df.sort_values(by=sort_col, ascending=ascending, na_position="last")

    page_size = st.selectbox("每页行数", [25, 50, 100, 200], index=1)
    total_pages = max(1, (len(df_sorted) + page_size - 1) // page_size)
    page = st.number_input("页码", min_value=1, max_value=total_pages, value=1)
    start = (page - 1) * page_size
    view = df_sorted.iloc[start:start + page_size].reset_index(drop=True)

    event = st.dataframe(
        view, width="stretch", hide_index=True,
        on_select="rerun", selection_mode="multi-row",
        column_config={
            "代币X市值": st.column_config.NumberColumn(format="%.0f"),
            "代币Y市值": st.column_config.NumberColumn(format="%.0f"),
            "当前价格": st.column_config.NumberColumn(format="%.8g"),
            "TVL": st.column_config.NumberColumn(format="%.0f"),
            "24h成交量": st.column_config.NumberColumn(format="%.0f"),
            "24h手续费收入": st.column_config.NumberColumn(format="%.2f"),
            "配置手续费率%": st.column_config.NumberColumn(format="%.4f"),
            "24hFeesTVL%": st.column_config.NumberColumn(format="%.4f"),
            "24h价格变化%": st.column_config.NumberColumn(format="%.2f"),
            "开仓人数": st.column_config.NumberColumn(format="%d"),
            "评分": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f"),
            "未通过原因": st.column_config.TextColumn(width="medium"),
        },
    )

    selected = event.selection.rows if event and event.selection else []
    csv = df_sorted.to_csv(index=False).encode("utf-8-sig")
    st.download_button("⬇️ 导出 CSV", data=csv,
                       file_name=f"meteora_filtered_{now_str().replace(':', '')}.csv",
                       mime="text/csv")

    selected_pools = {}
    for idx in selected:
        row = view.iloc[idx]
        if row.get("通过") != "✅":
            continue
        address = str(row.get("池地址") or "").strip()
        if not address:
            continue
        label = (f"{row.get('代币X') or '?'} / {row.get('代币Y') or '?'} · "
                 f"{address[:10]}…")
        selected_pools[label] = (address, f"{row.get('代币X') or '?'}-{row.get('代币Y') or '?'}")

    with st.container(border=True):
        st.subheader("➕ 导入到 Meme 超卖超买监控列表")
        st.caption("先在上方结果表勾选通过筛选的池子，再选择要导入的池。")
        labels = st.multiselect(
            "选择导入池子", options=list(selected_pools),
            key="scanner_meme_pools_to_import", disabled=not selected_pools,
        )
        if not selected_pools:
            st.info("请先勾选一条或多条通过筛选的结果。")
        if st.button("添加到 Meme 超卖超买监控", type="primary", disabled=not labels):
            from app.infrastructure.db.meme_watchlist import add_meme_pools

            added = add_meme_pools([selected_pools[label] for label in labels])
            if added:
                st.success(f"已导入 {added} 个池。可在“链上代币 → 批量 Meme 超卖超买”中查看。")
            else:
                st.error("导入失败，请检查数据库配置。")


# ===========================================================================
# 页面 4：API 状态与错误日志
# ===========================================================================
def page_api_status() -> None:
    st.header("④ API 状态与错误日志")
    db = get_db()
    st.subheader("接口信息（已实测验证）")
    st.markdown(f"""
| 数据源 | 基础地址 | 说明 |
|---|---|---|
| DLMM 池发现 | `{config.DLMM_BASE_URL}` | 全量分页发现及池详情，随池返回代币与风险指标 |
| DLMM K线 | `{config.DLMM_KLINE_BASE_URL}` | `GET /pools/{{address}}/ohlcv`，用于监控信号 |

当前 DLMM 限频：{config.DLMM_RPS} RPS。
""")
    if st.button("清空错误日志"):
        db.clear_api_errors()
        st.rerun()
    errs = db.get_api_errors(300)
    if errs:
        st.subheader("API 错误日志")
        st.dataframe(pd.DataFrame(errs), width="stretch", hide_index=True)
    else:
        st.success("暂无 API 错误记录。")


# ===========================================================================
# 页面 5：使用说明
# ===========================================================================
def page_help() -> None:
    st.header("⑤ 使用说明")
    st.markdown("""
### 快速开始
1. 按项目 README 配置环境变量；飞书 Webhook 全项目统一使用 `FEISHU_WEBHOOK`，Meteora 可选签名密钥为 `FEISHU_WEBHOOK_SECRET`。
2. 在主项目根目录运行 `streamlit run index.py`；需要后台扫描时同时运行 `python main.py`。
3. 进入「全池扫描」，点击「开始扫描」。首次为全量扫描，耗时较长，可随时停止并断点续扫。
4. 在「筛选条件」调整市值/成交量/TVL/Fees-TVL 与评分权重并保存。
5. 后台扫描完整结束后，会按已保存筛选条件评估结果并通过飞书报警（需要后台进程及 Webhook 配置）。
6. 在「筛选结果」查看、排序、导出 CSV；勾选通过筛选的池后，可导入「链上代币 → 批量 Meme 超卖超买」监控列表。

### 数据说明与口径
- 市值：DLMM 直接使用官方 `market_cap`，缺失时保留为空。
- 代币持仓/授权/开仓人数：DLMM 的「持仓数 / 前10持仓占比 / 开发方持仓占比 / 冻结·铸币授权 / 开仓人数(open_positions)」均由 Meteora 官方池发现接口随扫描一次性返回。
- 数据缺失处理：某项持仓/授权/开仓数据缺失时，如实标注「数据不足（已跳过）」而**不误杀**该池，绝不使用模拟值。
- Fees/TVL：按 `24h 手续费收入 ÷ TVL × 100%` 计算（口径一致时才计算）。
- 年化：`24h 手续费收入 × 365 ÷ TVL × 100%` 为**静态年化估算**，不代表未来 APR 或实际收益。
- 价格趋势：使用真实 DLMM K 线；数据不足时如实标注。
- 评分：仅用于排序观察，不代表盈利概率。市值>100万为硬性筛选条件，不参与加分。

### 扫描范围
- 当前仅扫描和展示 Meteora DLMM。数据库中旧的其他类型记录不会进入候选池或策略池。

### 覆盖范围
- 覆盖范围取决于官方 API 可访问页面，**不等于链上全部历史/现存池**。页面顶部展示 DLMM 的发现数量与 API 报告数量。

### 免责声明
- 本工具仅用于数据观察与研究，不构成投资建议。
""")


# ===========================================================================
# 价格区间计算（ATR）；由全链扫描工作区作为独立标签页呈现
# ===========================================================================
def _fmt_price(v) -> str:
    """价格显示：保留 8 位有效数字，适配大价格与极小价格。"""
    if v is None:
        return "-"
    return f"{float(v):,.8g}"


def render_price_range() -> None:
    st.header("📐 价格区间计算（ATR）")
    st.caption("输入 **池地址**（DEX Paprika **池级 K 线**）或 **代币**（CoinGecko 代币 K 线），"
               "实时获取美元价格与 K 线，用 **ta 库**计算 ATR，按倍数（默认 1.5）计算上下价格区间：")
    st.markdown("**上价 = 当前价格 + 倍数 × ATR**　｜　**下价 = 当前价格 − 倍数 × ATR**")

    source = st.radio("数据源", ["池地址 · DEX Paprika（池级 K 线）", "代币 · CoinGecko（代币 K 线）"],
                      horizontal=True)
    is_pool = source.startswith("池地址")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        if is_pool:
            symbol = st.text_input("池地址", value="",
                                   placeholder="Solana 池地址，例：2YaGc2iYZt44B5zn5Tvk59APbAn51xGekzNQAPF2PGn1")
        else:
            symbol = st.text_input("代币（符号 / 名称 / ID）", value="", placeholder="例：SOL 或 solana 或 Bitcoin")
    with col2:
        if is_pool:
            timeframe = st.selectbox("ATR 周期", POOL_TIMEFRAMES,
                                     index=POOL_TIMEFRAMES.index(DEFAULT_POOL_TIMEFRAME),
                                     help="DEX Paprika **免费注册 Key** 支持 15m/30m/1h（最近 7 天）；"
                                          "未配置 Key 时仅 1h 可用（最近 24 小时）。")
        else:
            timeframe = st.selectbox("ATR 周期", TIMEFRAMES, index=TIMEFRAMES.index(DEFAULT_TIMEFRAME),
                                     help="CoinGecko 官方不提供 15 分钟粒度：1d 由 4 小时 K 线聚合，1h 由 30 分钟"
                                          "K 线聚合，30m 为官方原生最细粒度。")
    with col3:
        period = st.number_input("ATR 样本数", min_value=1, max_value=60, value=DEFAULT_ATR_PERIOD, step=1,
                                 help="ta 库 AverageTrueRange 的窗口；超过可用 K 线数时自动使用最大可用样本数。")
    with col4:
        multiplier = st.number_input("倍数", min_value=0.1, max_value=50.0, value=float(DEFAULT_MULTIPLIER),
                                     step=0.1, help="上价 = 当前价格 + 倍数×ATR；下价 = 当前价格 − 倍数×ATR。")

    if st.button("计算", type="primary", disabled=not symbol.strip()):
        period_i = int(period)
        if is_pool:
            client = get_dexpaprika()
            with st.spinner("从 DEX Paprika 获取池信息与池级 K 线…"):
                info = resolve_pool(client, symbol.strip())
                if not info.get("error"):
                    try:
                        market = fetch_pool_market(client, info["pool_address"], timeframe)
                    except Exception as exc:  # noqa: BLE001
                        info = {**info, "error": f"获取池 K 线失败：{exc}"}
                        market = None
                    if market is not None:
                        res = compute_range(market["price"], market["candles"],
                                            multiplier=multiplier, period=period_i)
            if info.get("error"):
                st.error(info["error"])
                return
            st.success(f"已匹配池：**{info['name']}**（DEX Paprika / {info['dex']}）")
            st.caption(f"池地址：`{info['pool_address']}`")
            source_label, source_note = "DEX Paprika", (
                f"DEX Paprika /pools/`{info['pool_address'][:8]}…`/ohlcv，原始粒度 {market['raw_granularity']}。")
        else:
            client = get_coingecko()
            with st.spinner("从 CoinGecko 获取代币与 K 线…"):
                info = resolve_coin(client, symbol.strip())
                if not info.get("error"):
                    market = fetch_market(client, info["coin_id"], timeframe)
                    res = compute_range(market["price"], market["candles"],
                                        multiplier=multiplier, period=period_i)
            if info.get("error"):
                st.error(info["error"])
                return
            st.success(f"已匹配代币：**{info['name']}**（{info['symbol']} / {info['coin_id']}）")
            source_label, source_note = "CoinGecko", (
                f"CoinGecko /coins/ohlc，原始粒度 {market['raw_granularity']}。")

        m1, m2 = st.columns(2)
        m1.metric("当前价格（USD）", _fmt_price(market["price"]))
        m2.metric("数据来源", source_label)

        st.caption(f"K 线覆盖：最近 {res['candle_count']} 根 {timeframe}（{source_note}）")
        if res["atr"] is None:
            if market["price"] is None:
                st.warning("未获取到当前价格（数据源无美元报价）。")
            elif res["candle_count"] < 2:
                st.warning(f"K 线数据不足（{res['candle_count']} 根），无法计算 ATR。")
            else:
                st.warning("无法计算 ATR（K 线异常）。")
            return

        if res["period_used"] != period_i:
            st.caption(f"所选 ATR 样本数 {period_i} 超出可用 K 线数，已自动使用 {res['period_used']} 根计算。")

        price = float(market["price"])
        atr_v, upper, lower = res["atr"], res["upper"], res["lower"]
        st.markdown("#### 计算结果")
        cm = st.columns(5)
        cm[0].metric("ATR", _fmt_price(atr_v))
        cm[1].metric("上价（价格+倍数×ATR）", _fmt_price(upper))
        cm[2].metric("较当前", f"{(upper / price - 1) * 100:+.2f}%")
        cm[3].metric("下价（价格−倍数×ATR）", _fmt_price(lower))
        cm[4].metric("较当前", f"{(lower / price - 1) * 100:+.2f}%")

        st.caption(f"区间宽度 **{_fmt_price(upper - lower)}**（约占当前价格的 "
                   f"**{((upper - lower) / price * 100):.2f}%**），中间价为 {_fmt_price((upper + lower) / 2)}。")


# ===========================================================================
# 主入口
# ===========================================================================
PAGES = {
    "① 全池扫描": page_scan,
    "② 筛选条件": page_filters,
    "③ 筛选结果": page_results,
    "④ API 状态与错误日志": page_api_status,
    "⑤ 使用说明": page_help,
}

PAGE_DESCRIPTIONS = {
    "① 全池扫描": "启动 DLMM 池扫描并查看实时进度。",
    "② 筛选条件": "配置市值、成交量、持有人和风险规则。",
    "③ 筛选结果": "查看通过筛选的池及评分详情。",
    "④ API 状态与错误日志": "查看数据源状态、请求统计和错误记录。",
    "⑤ 使用说明": "查看扫描器功能与使用帮助。",
}


def main(embedded: bool = False) -> None:
    render_status_bar(embedded=embedded)
    tabs = st.tabs(list(PAGES), on_change="rerun", key="meteora_scanner_tabs")
    for (label, render_page), tab in zip(PAGES.items(), tabs):
        if tab.open:
            with tab:
                st.caption(PAGE_DESCRIPTIONS[label])
                render_page()


if __name__ == "__main__":
    main()

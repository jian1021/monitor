import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from app.interfaces.monitor_controls import render_monitor_controls

from app.application.monitors import meme_underval as mu
from app.infrastructure.db.meme_config import (
    get_meme_underval_params, update_meme_underval_params)
from app.application.monitors.rsi import get_meteora_native_rsi

st.set_page_config(page_title="Meme 低估监控", page_icon="🧲", layout="wide")
st.title("🧲 Meme 低估监控")
st.caption("Meteora Top Performers（按 24h 费/TVL 排序）预筛后，找 RSI(3, 1h) 超卖或现价接近 48h 高点的池子。")
render_monitor_controls("meme_underval", "Meme 低估监控")

saved = get_meme_underval_params()


@st.cache_data(ttl=120, show_spinner=False)
def _cached_pools(page_size, max_pages):
    return mu.fetch_top_performers(page_size=page_size, max_pages=max_pages)


@st.cache_data(ttl=600, show_spinner=False)
def _cached_rsi(pool_address, period):
    return get_meteora_native_rsi(pool_address, "hour", 1, int(period))


@st.cache_data(ttl=600, show_spinner=False)
def _cached_price_position(pool_address):
    return mu.fetch_48h_price_position(pool_address, "1h")


SCAN_MODE_RSI = "RSI 超卖"
SCAN_MODE_48H = "现价 ≥ 48h高点80%"

scan_mode = st.segmented_control("扫描模式", [SCAN_MODE_RSI, SCAN_MODE_48H], default=SCAN_MODE_RSI,
                                 label_visibility="collapsed") or SCAN_MODE_RSI
is_48h = scan_mode == SCAN_MODE_48H

with st.form("meme_scan_form", border=False):
    f1, f2, f3, f4, f5, f6 = st.columns([1.2, 1.1, 0.8, 0.9, 1.1, 0.9], vertical_alignment="bottom")
    min_market_cap = f1.number_input("市值 ≥ ($)", min_value=0.0,
                                     value=float(saved["min_market_cap"]), step=100_000.0)
    min_tvl = f2.number_input("TVL ≥ ($)", min_value=0.0, value=float(saved["min_tvl_usd"]), step=5_000.0)
    min_bin_step = f3.number_input("bin_step ≥", min_value=0, value=int(saved["min_bin_step"]), step=1)
    min_base_fee = f4.number_input("基础费率 ≥ %", min_value=0.0,
                                   value=float(saved["min_base_fee_pct"]), step=0.5)
    if is_48h:
        price_position_min = f5.number_input("现价/48h最高 ≥", min_value=0.0, max_value=1.0, step=0.05,
                                             format="%.2f", value=float(saved["price_position_min"]))
        rsi_max = float(saved["rsi_max"])
    else:
        rsi_max = f5.number_input("RSI(3, 1h) ≤", min_value=0.0, max_value=100.0,
                                  value=float(saved["rsi_max"]), step=1.0)
        price_position_min = float(saved["price_position_min"])
    scan = f6.form_submit_button("🔍 扫描", type="primary", width="stretch")
    with st.expander("高级参数 / 保存为后台默认"):
        a1, a2, a3 = st.columns(3)
        page_size = a1.number_input("每页池数", min_value=10, max_value=1000,
                                    value=int(mu.DEFAULT_PAGE_SIZE), step=50)
        max_pages = a2.number_input("拉取页数", min_value=1, max_value=10, value=int(mu.DEFAULT_MAX_PAGES), step=1)
        rsi_top_n = a3.number_input("最多计算候选数", min_value=1, max_value=200, value=30, step=5)
        save_default = st.checkbox("扫描时把以上阈值保存为后台默认值")

if scan and save_default:
    if update_meme_underval_params({
        "min_market_cap": float(min_market_cap), "min_tvl_usd": float(min_tvl),
        "min_bin_step": int(min_bin_step), "min_base_fee_pct": float(min_base_fee),
        "rsi_max": float(rsi_max), "price_position_min": float(price_position_min),
    }):
        st.toast("已保存为后台默认参数", icon="💾")
    else:
        st.error("❌ 参数保存失败（数据库不可用），本次扫描仍用页面值")

if not scan:
    st.caption("设好阈值后点「扫描」。")
    st.stop()

with st.spinner("正在拉取 Top Performers..."):
    pools = _cached_pools(int(page_size), int(max_pages))
if not pools:
    st.error("未能拉取到 Top Performers 数据，稍后重试")
    st.stop()

unique = mu.dedupe_by_meme_mint(pools)
prefiltered = [p for p in unique
               if mu.pool_passes_prefilter(p, min_market_cap, int(min_bin_step),
                                           min_base_fee, min_tvl)]
prefiltered.sort(key=mu.fee_ratio_24h, reverse=True)

st.caption(f"拉取 {len(pools)} 个池 → 按币去重 {len(unique)} → 通过预筛 {len(prefiltered)} → "
           f"计算前 {min(len(prefiltered), int(rsi_top_n))} 个")

if not prefiltered:
    st.info(f"当前阀值下无预筛标的（市值≥${min_market_cap:,.0f}，"
            f"TVL≥${min_tvl:,.0f}，bin_step≥{int(min_bin_step)}，"
            f"基础费率≥{min_base_fee:.1f}%）")
    st.stop()

if scan_mode == SCAN_MODE_48H:
    with st.spinner(f"正在计算 {min(len(prefiltered), int(rsi_top_n))} 个候选的 48h 价格位置..."):
        progress = st.progress(0, text="准备计算 48h 价格位置...")

        def _report_48h(done, total, symbol):
            progress.progress(min(done / max(total, 1), 1.0),
                              text=f"48h {done}/{total}：{symbol}")

        hits = mu.scan_price_position(
            pools, min_market_cap=min_market_cap, min_bin_step=int(min_bin_step),
            min_base_fee_pct=min_base_fee, min_tvl_usd=min_tvl,
            top_n=int(rsi_top_n), price_position_min=float(price_position_min),
            price_fetcher=lambda addr: _cached_price_position(addr),
            on_progress=_report_48h,
        )
        progress.empty()

    if not hits:
        st.warning(f"预筛有标的，但无「现价 ÷ 48h最高 ≥ {price_position_min:.0%}」的标的")
        st.stop()

    st.success(f"🎯 命中 **{len(hits)}** 个价格强度标的")
    hits.sort(key=lambda h: h["price_ratio_48h"], reverse=True)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=[h["symbol"] for h in hits[:20]],
        y=[h["price_ratio_48h"] * 100 for h in hits[:20]],
        text=[f"{h['price_ratio_48h']:.0%}" for h in hits[:20]],
        textposition="outside",
        name="现价/48h最高 %",
        marker_color="#ef5350",
    ))
    fig.update_layout(title="命中标的 现价 / 48h最高（%）",
                      xaxis_title="池子", yaxis_title="现价/48h最高 %",
                      template="plotly_dark", height=380)
    with st.expander("图表"):
        st.plotly_chart(fig, width="stretch")

    df = pd.DataFrame(hits)
    df["price_ratio_pct"] = df["price_ratio_48h"] * 100
    df["gmgn_link"] = df["meme_mint"].apply(
        lambda ca: f"https://gmgn.ai/sol/token/{ca}" if ca else "")
    df["meteora_link"] = df["pool_address"].apply(
        lambda pa: f"https://app.meteora.ag/dlmm/{pa}" if pa else "")
    st.dataframe(
        df,
        column_config={
            "symbol": "池子",
            "gmgn_link": st.column_config.LinkColumn("GMGN 盘面", display_text="🔗 开盘"),
            "meteora_link": st.column_config.LinkColumn("Meteora 池子", display_text="🌊 进池"),
            "meme_mint": "Meme 合约",
            "pool_address": "池地址",
            "market_cap": st.column_config.NumberColumn("Meme 市值 (USD)", format="$%d"),
            "tvl": st.column_config.NumberColumn("TVL (USD)", format="$%d"),
            "base_fee_pct": st.column_config.NumberColumn("基础费率 (Fee %)", format="%.2f"),
            "fee_ratio_24h": st.column_config.NumberColumn("24h 费/TVL %", format="%.2f"),
            "fees_24h_usd": st.column_config.NumberColumn("24h 手续费 (USD)", format="$%d"),
            "volume_24h_usd": st.column_config.NumberColumn("24h 交易量 (USD)", format="$%d"),
            "bin_step": "bin_step",
            "price_ratio_pct": st.column_config.NumberColumn("现价/48h最高 (%)", format="%.1f"),
            "current_price": st.column_config.NumberColumn("现价 (USD)", format="$%.8f"),
            "high_48h": st.column_config.NumberColumn("48h最高 (USD)", format="$%.8f"),
            "age_hours": st.column_config.NumberColumn("池龄 (小时)", format="%.1f"),
        },
        column_order=["symbol", "gmgn_link", "meteora_link", "market_cap", "tvl",
                      "price_ratio_pct", "current_price", "high_48h", "base_fee_pct",
                      "fee_ratio_24h", "bin_step", "fees_24h_usd", "volume_24h_usd",
                      "age_hours", "meme_mint", "pool_address"],
        width="stretch",
        hide_index=True,
    )
    st.stop()

with st.spinner(f"正在计算 {min(len(prefiltered), int(rsi_top_n))} 个候选的 RSI(3, 1h)..."):
    progress = st.progress(0, text="准备计算 RSI...")

    def _report(done, total, symbol):
        progress.progress(min(done / max(total, 1), 1.0),
                          text=f"RSI {done}/{total}：{symbol}")

    hits = mu.scan_undervalued(
        pools, min_market_cap=min_market_cap, min_bin_step=int(min_bin_step),
        min_base_fee_pct=min_base_fee, min_tvl_usd=min_tvl,
        rsi_period=mu.DEFAULT_RSI_PERIOD,
        rsi_max=rsi_max, rsi_top_n=int(rsi_top_n),
        rsi_fetcher=lambda addr, _tf, _agg, length: _cached_rsi(addr, length),
        on_progress=_report,
    )
    progress.empty()

if not hits:
    st.warning("预筛有标的，但 RSI(3, 1h) 无低于上限的超卖标的")
    st.stop()

st.success(f"🎯 命中 **{len(hits)}** 个低估标的")
hits.sort(key=lambda h: h["fee_ratio_24h"], reverse=True)

fig = go.Figure()
fig.add_trace(go.Bar(
    x=[h["symbol"] for h in hits[:20]],
    y=[h["fee_ratio_24h"] for h in hits[:20]],
    text=[f"RSI {h['rsi']:.1f}" for h in hits[:20]],
    textposition="outside",
    name="24h 费率 %",
    marker_color="#26a69a",
))
fig.update_layout(title="命中标的 24h 费 / TVL 比率（%）",
                  xaxis_title="池子", yaxis_title="费/TVL %",
                  template="plotly_dark", height=380)
with st.expander("图表"):
    st.plotly_chart(fig, width="stretch")

df = pd.DataFrame(hits)
df["gmgn_link"] = df["meme_mint"].apply(
    lambda ca: f"https://gmgn.ai/sol/token/{ca}" if ca else "")
df["meteora_link"] = df["pool_address"].apply(
    lambda pa: f"https://app.meteora.ag/dlmm/{pa}" if pa else "")
st.dataframe(
    df,
    column_config={
        "symbol": "池子",
        "gmgn_link": st.column_config.LinkColumn("GMGN 盘面", display_text="🔗 开盘"),
        "meteora_link": st.column_config.LinkColumn("Meteora 池子", display_text="🌊 进池"),
        "meme_mint": "Meme 合约",
        "pool_address": "池地址",
        "market_cap": st.column_config.NumberColumn("Meme 市值 (USD)", format="$%d"),
        "tvl": st.column_config.NumberColumn("TVL (USD)", format="$%d"),
        "base_fee_pct": st.column_config.NumberColumn("基础费率 (Fee %)", format="%.2f"),
        "fee_ratio_24h": st.column_config.NumberColumn("24h 费/TVL %", format="%.2f"),
        "fees_24h_usd": st.column_config.NumberColumn("24h 手续费 (USD)", format="$%d"),
        "volume_24h_usd": st.column_config.NumberColumn("24h 交易量 (USD)", format="$%d"),
        "bin_step": "bin_step",
        "rsi": st.column_config.NumberColumn("RSI(3, 1h)", format="%.1f"),
        "age_hours": st.column_config.NumberColumn("池龄 (小时)", format="%.1f"),
    },
    column_order=["symbol", "gmgn_link", "meteora_link", "market_cap", "tvl",
                  "base_fee_pct", "fee_ratio_24h", "rsi", "bin_step", "fees_24h_usd",
                  "volume_24h_usd", "age_hours", "meme_mint", "pool_address"],
    width="stretch",
    hide_index=True,
)

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import monitor_meme_underval as mu
from db import get_meme_underval_params, update_meme_underval_params
from monitor_rsi import get_meteora_native_rsi

st.set_page_config(page_title="Meme 低估监控", layout="wide")
st.title("🧲 Meme 低估监控")
st.caption("直连 Meteora Top Performers（按 24h 费 / TVL 排序），经市值 / TVL / bin_step / 基础费率预筛后，对头部候选计算 RSI(3, 1h)，只保留超卖钝化标的。")

saved = get_meme_underval_params()


@st.cache_data(ttl=120, show_spinner=False)
def _cached_pools(page_size, max_pages):
    return mu.fetch_top_performers(page_size=page_size, max_pages=max_pages)


@st.cache_data(ttl=600, show_spinner=False)
def _cached_rsi(pool_address, period):
    return get_meteora_native_rsi(pool_address, "hour", 1, int(period))


f1, f2, f3, f4, f5 = st.columns(5)
with f1:
    min_market_cap = st.number_input("市值下限 (USD)", min_value=0.0,
                                     value=float(saved["min_market_cap"]), step=100_000.0)
with f2:
    min_tvl = st.number_input("TVL 下限 (USD)", min_value=0.0,
                              value=float(saved["min_tvl_usd"]), step=5_000.0)
with f3:
    min_bin_step = st.number_input("bin_step 下限", min_value=0,
                                   value=int(saved["min_bin_step"]), step=1)
with f4:
    min_base_fee = st.number_input("基础费率下限 (Fee %)", min_value=0.0,
                                   value=float(saved["min_base_fee_pct"]), step=0.5)
with f5:
    rsi_max = st.number_input("RSI(3, 1h) 上限", min_value=0.0, max_value=100.0,
                              value=float(saved["rsi_max"]), step=1.0)

with st.expander("高级参数", expanded=False):
    a1, a2, a3 = st.columns(3)
    with a1:
        page_size = st.number_input("每页池数", min_value=10, max_value=1000,
                                    value=int(mu.DEFAULT_PAGE_SIZE), step=50)
    with a2:
        max_pages = st.number_input("拉取页数", min_value=1, max_value=10,
                                    value=int(mu.DEFAULT_MAX_PAGES), step=1)
    with a3:
        rsi_top_n = st.number_input("最多算 RSI 的候选数", min_value=1,
                                    max_value=200, value=30, step=5)
    if st.button("🧹 清除缓存并重拉"):
        st.cache_data.clear()
        st.rerun()

s1, s2 = st.columns([1, 4])
with s1:
    save = st.button("💾 保存参数", use_container_width=True)
with s2:
    st.caption("保存后同时作为手动扫描默认值与后台定时任务的参数。")
if save:
    ok = update_meme_underval_params({
        "min_market_cap": float(min_market_cap),
        "min_tvl_usd": float(min_tvl),
        "min_bin_step": int(min_bin_step),
        "min_base_fee_pct": float(min_base_fee),
        "rsi_max": float(rsi_max),
    })
    if ok:
        st.success("✅ 参数已保存，手动与自动运行同步生效")
    else:
        st.error("❌ 参数保存失败（数据库不可用），本次扫描仍用页面值")

scan = st.button("🔍 开始扫描", type="primary", use_container_width=True)
if not scan:
    st.info("设置好五个阀值后点击「开始扫描」")
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

m1, m2, m3, m4 = st.columns(4)
m1.metric("拉取池数", len(pools))
m2.metric("按币去重", len(unique))
m3.metric("通过预筛", len(prefiltered))
m4.metric("待算 RSI", min(len(prefiltered), int(rsi_top_n)))

if not prefiltered:
    st.info(f"当前阀值下无预筛标的（市值≥${min_market_cap:,.0f}，"
            f"TVL≥${min_tvl:,.0f}，bin_step≥{int(min_bin_step)}，"
            f"基础费率≥{min_base_fee:.1f}%）")
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
st.plotly_chart(fig, use_container_width=True)

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
    use_container_width=True,
    hide_index=True,
)

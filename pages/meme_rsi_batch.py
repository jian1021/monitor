import pandas as pd
import streamlit as st

from app.application.monitors import meme_rsi_batch as mrb
from app.infrastructure.db.meme_watchlist import (
    add_meme_pools,
    get_meme_rsi_params,
    list_meme_watchlist_pools,
    remove_meme_pool,
    set_meme_pool_enabled,
    update_meme_rsi_params,
)
from app.interfaces.monitor_controls import render_monitor_controls
def render_page() -> None:
    st.title("📡 批量 Meme 超卖超买监控")
    st.caption("粘贴 Solana(Meteora) 池子地址生成监控列表；常驻进程按所选周期的 RSI(3) 越界合并推送飞书。")

    render_monitor_controls("meme_rsi_batch", "批量 Meme 超买超卖")

    params = get_meme_rsi_params()
    st.caption(f"RSI 周期固定为 {mrb.RSI_PERIOD}（不可改）；可选 15 分钟或 1 小时 K 线周期。")
    p0, p1, p2, p3 = st.columns([1, 1, 1, 1])
    with p0:
        timeframe = st.selectbox("K 线周期", ["15m", "1h"],
                                 index=0 if params.get("timeframe", "15m") == "15m" else 1,
                                 format_func={"15m": "15 分钟", "1h": "1 小时"}.get)
    with p1:
        rsi_low = st.number_input("超卖阈值 (RSI <)", min_value=0.0, max_value=100.0,
                                  value=float(params["rsi_low"]), step=1.0)
    with p2:
        rsi_high = st.number_input("超买阈值 (RSI >)", min_value=0.0, max_value=100.0,
                                   value=float(params["rsi_high"]), step=1.0)
    with p3:
        st.write("")
        if st.button("💾 保存周期和阈值", use_container_width=True):
            ok = update_meme_rsi_params({"timeframe": timeframe,
                                         "rsi_low": float(rsi_low),
                                         "rsi_high": float(rsi_high)})
            if ok:
                st.success("✅ 周期和阈值已保存；后台监控将在下一轮使用新设置")
            else:
                st.error("❌ 保存失败（数据库不可用）")

    st.divider()
    st.markdown("##### ➕ 导入池子地址")
    text = st.text_area("粘贴 Solana 池子地址（每行 / 逗号 / 空格分隔）",
                        height=140, key="mrb_input")
    if st.button("解析并添加", type="primary"):
        valid, invalid = mrb.parse_pool_addresses(text)
        if valid:
            with st.spinner(f"解析到 {len(valid)} 个地址，正在读取池子名..."):
                named = [(addr, mrb.fetch_pool_name(addr)) for addr in valid]
            added = add_meme_pools(named)
            st.success(f"✅ 已添加/更新 {added} 个池子")
        else:
            st.error("未解析到合法池子地址")
        if invalid:
            st.warning(f"⚠️ {len(invalid)} 个片段不是合法池子地址："
                       + ", ".join(invalid[:10]) + (" ..." if len(invalid) > 10 else ""))

    st.divider()
    st.markdown("##### 📋 监控列表")

    pools = list_meme_watchlist_pools()
    if not pools:
        st.info("监控列表为空，先在上方导入池子地址。")
        return

    df = pd.DataFrame(pools)
    df["meteora_link"] = df["address"].apply(lambda a: f"https://app.meteora.ag/dlmm/{a}")
    st.caption("最新 RSI 值对应所选周期；修改后台告警周期后需点击上方保存按钮生效。")
    st.dataframe(
        df,
        column_config={
            "symbol": "池子",
            "address": "池地址",
            "enabled": "启用",
            "last_rsi": st.column_config.NumberColumn("最新 RSI", format="%.1f"),
            "last_price": st.column_config.NumberColumn("现价 (USD)", format="$%.8f"),
            "last_checked_at": "上次检查",
            "meteora_link": st.column_config.LinkColumn("Meteora 池子", display_text="🌊 进池"),
        },
        column_order=["symbol", "address", "enabled", "last_rsi", "last_price",
                      "last_checked_at", "meteora_link"],
        use_container_width=True,
        hide_index=True,
    )

    with st.expander("🛠️ 管理（单池启停 / 删除）", expanded=False):
        labels = {p["address"]: (p["symbol"] or p["address"][:8]) for p in pools}
        target = st.selectbox("选择池子", list(labels.keys()),
                              format_func=lambda a: f"{labels[a]} · {a[:8]}...")
        m1, m2, m3 = st.columns(3)
        current = next((p["enabled"] for p in pools if p["address"] == target), True)
        with m1:
            if st.button("启用" if not current else "停用", use_container_width=True):
                set_meme_pool_enabled(target, not current)
                st.rerun()
        with m2:
            if st.button("🗑️ 删除", use_container_width=True):
                remove_meme_pool(target)
                st.rerun()

    if st.button("🔍 立即扫描一次", type="primary", use_container_width=True):
        with st.spinner(f"正在扫描 {len(pools)} 个池子..."):
            progress = st.progress(0, text="准备扫描...")

            def _report(done, total, symbol):
                progress.progress(min(done / max(total, 1), 1.0),
                                  text=f"RSI {done}/{total}：{symbol}")

            hits = mrb.scan_watchlist(
                pools, rsi_low=float(rsi_low), rsi_high=float(rsi_high),
                period=mrb.RSI_PERIOD, timeframe=timeframe,
                on_progress=_report,
            )
            progress.empty()
        if hits:
            st.warning(f"命中 {len(hits)} 个越界标的（仅展示，未发飞书）")
            st.dataframe(pd.DataFrame(hits), use_container_width=True, hide_index=True)
        else:
            st.success("本轮无越界标的")

if __name__ == "__main__":
    st.set_page_config(page_title="批量 Meme 超卖超买监控", layout="wide")
    render_page()

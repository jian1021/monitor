"""Unified tabbed workspace for on-chain tools and pool scanners.

所有内嵌页统一通过 `render_page(embedded=True)` 渲染：嵌入时标题由外层标签页
提供，子页面不再输出自己的大标题，避免同屏出现两个标题。
"""

import streamlit as st

st.set_page_config(page_title="全链扫描与链上代币", page_icon="🌐", layout="wide")
st.title("🌐 全链扫描与链上代币")
st.caption("链上代币管理、Meme 批量监控、Meteora / Robinhood 全池扫描与价格区间计算，"
           "统一在本页的标签页内完成；标签名即当前功能的标题。")

# 标签名与各自页面的标题保持一致（emoji + 名称）
token_tab, meme_tab, meteora_tab, robinhood_tab, price_range_tab = st.tabs([
    "🔗 链上代币",
    "📡 批量 Meme 超卖超买",
    "🌊 Meteora 全池扫描",
    "🦅 Robinhood 全池扫描",
    "📐 价格区间计算",
], on_change="rerun", key="onchain_workspace_tabs")

if token_tab.open:
    with token_tab:
        from pages.st_onchain_token import render_page as render_token_page

        render_token_page(embedded=True)

if meme_tab.open:
    with meme_tab:
        from pages.meme_rsi_batch import render_page as render_meme_page

        render_meme_page(embedded=True)

if meteora_tab.open:
    with meteora_tab:
        from pages.meteora_scanner import render_page as render_meteora_page

        render_meteora_page(embedded=True)

if robinhood_tab.open:
    with robinhood_tab:
        from pages.fee_screen import render_page as render_robinhood_page

        render_robinhood_page(embedded=True)

if price_range_tab.open:
    with price_range_tab:
        from pages.meteora_scanner import render_price_range

        render_price_range(embedded=True)

"""Unified tabbed workspace for on-chain tools and pool scanners."""

import streamlit as st


st.set_page_config(page_title="全链扫描与链上代币", page_icon="🔗", layout="wide")
st.title("🔗 全链扫描与链上代币")
st.caption("在标签页中直接使用代币工具和链上全池扫描器。")

token_tab, meme_tab, meteora_tab, robinhood_tab, price_range_tab = st.tabs([
    "🪙 链上代币管理",
    "📡 批量 Meme 超卖超买",
    "🌊 Meteora 全池扫描",
    "🦅 Robinhood 全池扫描",
    "📐 价格区间计算",
], on_change="rerun", key="onchain_workspace_tabs")
if token_tab.open:
    with token_tab:
        from pages.st_onchain_token import render_page

        render_page(embedded=True)

if meme_tab.open:
    with meme_tab:
        from pages.meme_rsi_batch import render_page

        render_page(embedded=True)

if meteora_tab.open:
    with meteora_tab:
        from pages.meteora_scanner import main as render_meteora_scanner

        render_meteora_scanner(embedded=True)

if robinhood_tab.open:
    with robinhood_tab:
        from pages.fee_screen import render_page

        render_page(embedded=True)

if price_range_tab.open:
    with price_range_tab:
        from pages.meteora_scanner import render_price_range

        render_price_range()

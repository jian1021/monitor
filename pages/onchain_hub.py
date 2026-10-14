"""Card switcher for the on-chain token tools."""

import streamlit as st


st.set_page_config(page_title="链上代币", page_icon="🔗", layout="wide")
st.title("🔗 链上代币")
st.caption("切换标签选择链上代币工具。")

token_tab, meme_tab = st.tabs(["🪙 链上代币管理", "📡 批量 Meme 超买超卖"])
with token_tab:
    st.markdown("#### 链上代币管理")
    st.caption("按公链添加、搜索和管理需要监控的代币地址。")
    if st.button("打开代币管理 →", key="open_onchain_tokens", type="primary"):
        st.switch_page("pages/st_onchain_token.py")

with meme_tab:
    st.markdown("#### 批量 Meme 超买超卖")
    st.caption("批量监控 Meme 代币 RSI，支持 15 分钟和 1 小时周期。")
    if st.button("打开 Meme 监控 →", key="open_meme_rsi_batch", type="primary"):
        st.switch_page("pages/meme_rsi_batch.py")

# -*- coding: utf-8 -*-
"""Robinhood Chain：DexPaprika Uniswap 候选池扫描。

本页面只保留 DexPaprika 外部池扫描（发现 + 基础风险过滤 + GMGN 检查）；
原先基于本地 rhpools 索引的筛选参数 / 流动池列表 / 单池详情 / 监控报警已移除。
"""
import streamlit as st

from app.interfaces.dexpaprika_scan import render_dexpaprika_scan
from app.interfaces.monitor_controls import render_monitor_controls


def render_page(embedded: bool = False) -> None:
    if not embedded:
        st.title("🦅 Robinhood 全池扫描")
    st.caption("扫描 Robinhood Chain 上的 Uniswap V2/V3/V4 候选池，按成交量、流动性、交易数筛选；"
               "下方开关控制的是「LP 池子价格告警」后台模块，不影响扫描。")
    render_monitor_controls("lp_alert", "LP 仓位 / 池子价格告警")
    render_dexpaprika_scan()


if __name__ == "__main__":
    st.set_page_config(page_title="Robinhood 全池扫描", page_icon="🦅", layout="wide")
    render_page()

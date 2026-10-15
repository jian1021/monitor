# -*- coding: utf-8 -*-
"""Robinhood Chain：DexPaprika Uniswap 候选池扫描。

本页面只保留 DexPaprika 外部池扫描（发现 + 基础风险过滤 + GMGN 检查）；
原先基于本地 rhpools 索引的筛选参数 / 流动池列表 / 单池详情 / 监控报警已移除。
"""
import streamlit as st

from app.interfaces.dexpaprika_scan import render_dexpaprika_scan
from app.interfaces.monitor_controls import render_monitor_controls


def render_page() -> None:
    st.title("🦅 Robinhood Chain 全池扫描")
    st.caption("扫描 Robinhood Chain（4663）上的 Uniswap V2/V3/V4 候选池，"
               "按成交量、流动性与交易数筛选，可选基础貔貅风险过滤与 GMGN 合约检测。")
    render_monitor_controls("lp_alert", "LP 仓位 / 池子价格告警")
    st.caption("Robinhood 池的价格监控由「LP 仓位 / 池子价格告警」后台模块执行"
               "（支持 robinhood 链池子地址）；规则在该模块页面添加，此处仅控制启停与间隔。")
    render_dexpaprika_scan()


if __name__ == "__main__":
    st.set_page_config(page_title="Robinhood Chain 全池扫描", page_icon="🦅", layout="wide")
    render_page()

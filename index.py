import os
import streamlit as st
from app.core.settings import ADMIN_PASS, ADMIN_USER

# Preload the exit-strategy page's package tree before Streamlit begins routing
# page scripts. This avoids intermittent import-cache KeyErrors during a hot
# app update when the multipage runner imports that page on demand.
from app.application.meteora_scanner import config as _scanner_config
from app.application.meteora_scanner.db import Database as _ScannerDatabase
from app.application.meteora_scanner.filters import apply_filters as _apply_scanner_filters
from app.infrastructure.db.exit_strategy import (
    load_exit_strategy as _load_exit_strategy,
    save_exit_strategy as _save_exit_strategy,
)
from app.infrastructure.db.navigation import NAVIGATION_GROUPS, get_navigation_order


# ================= 1. 初始化 Session 状态 =================
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False


# ================= 2. 登录与退出视图函数 =================
def login():
    st.title("🔐 监控看板")

    with st.form("login_form"):
        username = st.text_input("账号", placeholder="请输入管理员账号")
        password = st.text_input("密码", type="password", placeholder="请输入密码")
        submit_btn = st.form_submit_button("登录系统", width="stretch")

    if submit_btn:
        user_input = username.strip() if username else ""
        pass_input = password.strip() if password else ""

        if user_input == ADMIN_USER and pass_input == ADMIN_PASS:
            st.session_state.logged_in = True
            st.success("✅ 登录成功，正在进入系统...")
            st.rerun()  # 状态改变，重新触发路由解析
        else:
            st.error("❌ 账号或密码错误")


def logout():
    st.title("👤 个人账号设置")
    st.info("当前状态：已安全登录")
    
    if st.button("🚪 退出登录", type="primary"):
        st.session_state.logged_in = False
        st.rerun()  # 状态改变，切回登录路由


# ================= 3. 定义 Streamlit 页面路由 =================
# 登录与退出视图（使用函数定义）
login_page = st.Page(login, title="登录", icon=":material/login:")
logout_page = st.Page(logout, title="退出登录", icon=":material/logout:")

# 业务页面（指向 pages/ 目录下的独立文件）
dashboard = st.Page("pages/dashboard.py", title="超卖监控", icon=":material/dashboard:", default=True)
mainstream_crypto = st.Page("pages/st_mainstream_crypto.py", title="主流加密货币", icon=":material/monetization_on:")
onchain_token = st.Page("pages/st_onchain_token.py", title="链上代币管理", icon=":material/link:", visibility="hidden")
discover_lp = st.Page("pages/discover_lp.py", title="Meme 低估监控", icon=":material/trending_down:")
meme_rsi_batch = st.Page("pages/meme_rsi_batch.py", title="批量 Meme 超卖超买", icon=":material/radar:", visibility="hidden")
lp_bands = st.Page("pages/lp_bands.py", title="LP 区间可视化", icon=":material/ssid_chart:")
price_monitor = st.Page("pages/st_monitor_price.py", title="价格监控", icon=":material/track_changes:")
pool_simulator = st.Page("pages/st_pool_simulator.py", title="池子价格模拟器", icon=":material/science:")
token_distribution = st.Page("pages/distribution.py", title="池内代币分布图", icon=":material/bar_chart:")
lp_position_alert_page = st.Page("pages/lp_position_alert.py", title="LP 仓位告警", icon=":material/notifications_active:")
lp_apr = st.Page("pages/lp_apr.py", title="LP 区间 APR", icon=":material/function:")
exit_strategy = st.Page("pages/exit_strategy.py", title="退出策略", icon=":material/notification_important:")
monitor_settings = st.Page("pages/monitor_settings.py", title="监控模块设置", icon=":material/tune:")


onchain_hub_page = st.Page("pages/onchain_hub.py", title="全链扫描与链上代币", icon=":material/link:")


# ================= 4. 路由守卫与导航挂载 =================
if st.session_state.logged_in:
    page_by_id = {
        "dashboard": dashboard,
        "mainstream_crypto": mainstream_crypto,
        "discover_lp": discover_lp,
        "lp_bands": lp_bands,
        "price_monitor": price_monitor,
        "pool_simulator": pool_simulator,
        "token_distribution": token_distribution,
        "lp_position_alert": lp_position_alert_page,
        "lp_apr": lp_apr,
        "exit_strategy": exit_strategy,
        "onchain_hub": onchain_hub_page,
        "onchain_token": onchain_token,
        "meme_rsi_batch": meme_rsi_batch,
        "monitor_settings": monitor_settings,
        "logout": logout_page,
    }
    saved_order = get_navigation_order()
    navigation = {
        group: [
            page_by_id[key]
            for key in saved_order.get(group, NAVIGATION_GROUPS[group])
            if key in page_by_id
        ]
        for group in NAVIGATION_GROUPS
    }
    # Keep legacy child routes available while the tools render inside hub tabs.
    navigation["策略与数据"].extend([onchain_token, meme_rsi_batch])
    pg = st.navigation(navigation)
else:
    # 未登录：隐藏所有业务页面，仅挂载登录页
    pg = st.navigation([login_page])


# ================= 5. 渲染入口 =================
pg.run()

import os
import streamlit as st
from config import  ADMIN_PASS , ADMIN_USER


# ================= 1. 初始化 Session 状态 =================
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False


# ================= 2. 登录与退出视图函数 =================
def login():
    st.title("🔐 监控看板")

    with st.form("login_form"):
        username = st.text_input("账号", placeholder="请输入管理员账号")
        password = st.text_input("密码", type="password", placeholder="请输入密码")
        submit_btn = st.form_submit_button("登录系统", use_container_width=True)

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
dashboard = st.Page("pages/app.py", title="超卖监控", icon=":material/dashboard:", default=True)
mainstream_crypto = st.Page("pages/st_mainstream_crypto.py", title="主流加密货币", icon=":material/monetization_on:")
onchain_token = st.Page("pages/st_onchain_token.py", title="链上代币", icon=":material/link:")
discover_lp = st.Page("pages/discover_lp.py", title="LP 策略探索", icon=":material/find_in_page:")
lp_bands = st.Page("pages/lp_bands.py", title="LP 区间可视化", icon=":material/ssid_chart:")
price_monitor = st.Page("pages/st_monitor_price.py", title="价格监控", icon=":material/track_changes:")
pool_simulator = st.Page("pages/st_pool_simulator.py", title="池子价格模拟", icon=":material/science:")
token_distribution = st.Page("pages/distribution.py", title="代币分布图", icon=":material/bar_chart:")
lp_position_alert_page = st.Page("pages/lp_position_alert.py", title="LP 仓位告警", icon=":material/notifications_active:")


# ================= 4. 路由守卫与导航挂载 =================
if st.session_state.logged_in:
    # 登录成功：挂载所有监控业务模块与退出页面
    pg = st.navigation(
        {
            "策略与数据": [dashboard, mainstream_crypto, onchain_token, discover_lp, lp_bands, price_monitor, pool_simulator, token_distribution, lp_position_alert_page],
            "系统管理": [logout_page],
        }
    )
else:
    # 未登录：隐藏所有业务页面，仅挂载登录页
    pg = st.navigation([login_page])


# ================= 5. 渲染入口 =================
pg.run()
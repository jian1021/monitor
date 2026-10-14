# config.py
import os
from pathlib import Path
from dotenv import load_dotenv

# ========== 从仓库根目录加载 .env ==========
# 用相对 __file__ 的绝对路径，确保从任意工作目录启动后台或 Streamlit 都能读取。
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)


def _streamlit_secret(name, default=""):
    try:
        import streamlit as st
        return st.secrets.get(name, default) or default
    except Exception:
        return default


def _setting(name, default=""):
    """先读取进程环境变量（本地 .env 与托管环境变量均可），
    再退回 Streamlit 的 st.secrets。"""
    value = os.getenv(name)
    return value if value else _streamlit_secret(name, default)


def setting_source(name):
    if os.getenv(name):
        return "环境变量 / .env"
    if _streamlit_secret(name):
        return "st.secrets"
    return "未配置"


def streamlit_secret_keys():
    try:
        import streamlit as st
        return sorted(st.secrets.keys())
    except Exception as exc:
        return [f"<无法读取 st.secrets: {type(exc).__name__}>"]


# ========== 数据库配置 ==========
LIBSQL_URL = _setting("LIBSQL_URL")
LIBSQL_TOKEN = _setting("LIBSQL_TOKEN")

# ========== 飞书告警推送 ==========
FEISHU_WEBHOOK = _setting("FEISHU_WEBHOOK")

# ========== Streamlit 后台管理员账号 ==========
ADMIN_USER = _setting("ADMIN_USER", "admin")
ADMIN_PASS = _setting("ADMIN_PASS", "123456")

# ========== Robinhood 链 RPC（可选，换付费节点可放宽 eth_getLogs 范围限制） ==========
ROBINHOOD_RPC = _setting("ROBINHOOD_RPC", "")


# ========== 可选：生产环境严格校验（建议开启） ==========
# 如果你不想空值上线报错，可以放开下面校验
'''
required_vars = [
    ("LIBSQL_URL", LIBSQL_URL),
    ("LIBSQL_TOKEN", LIBSQL_TOKEN),
    ("FEISHU_WEBHOOK", FEISHU_WEBHOOK)
]

for name, value in required_vars:
    if not value:
        raise ValueError(f"环境变量 {name} 没有配置！")
'''

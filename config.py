# config.py
import os
from pathlib import Path
from dotenv import load_dotenv

# ========== 仅本地开发加载 .env，云端忽略此文件 ==========
# 用相对 __file__ 的绝对路径：否则 .env 是否被读取取决于启动时的 CWD，
# 从仓库外启动 streamlit（如 streamlit run /path/to/index.py）会读不到配置。
_ENV_PATH = Path(__file__).resolve().parent / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)


def _streamlit_secret(name, default=""):
    try:
        import streamlit as st
        return st.secrets.get(name, default) or default
    except Exception:
        return default


def _setting(name, default=""):
    """先读环境变量（本地 .env 与 GitHub Actions secrets 都走这条），
    再退回 Streamlit 的 st.secrets（Streamlit Cloud 用的是这个）。"""
    value = os.getenv(name)
    return value if value else _streamlit_secret(name, default)


# ========== 数据库配置 ==========
LIBSQL_URL = _setting("LIBSQL_URL")
LIBSQL_TOKEN = _setting("LIBSQL_TOKEN")

# ========== 飞书告警推送 ==========
FEISHU_WEBHOOK = _setting("FEISHU_WEBHOOK")

# ========== Streamlit 后台管理员账号 ==========
ADMIN_USER = _setting("ADMIN_USER", "admin")
ADMIN_PASS = _setting("ADMIN_PASS", "123456")


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

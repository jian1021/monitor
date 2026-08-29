# config.py
import os


# 读取数据库配置 (带空字符串默认值，防止未配置时报 ImportError)
LIBSQL_URL = os.getenv("LIBSQL_URL", "")
LIBSQL_TOKEN = os.getenv("LIBSQL_TOKEN", "")

# 飞书与账号配置
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK", "")
# 配置管理员账号streamlit cloud
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "123456")
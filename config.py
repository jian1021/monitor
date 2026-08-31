# config.py
import os
from dotenv import load_dotenv

# ========== 仅本地开发加载 .env，云端忽略此文件 ==========
if os.path.exists(".env"):
    load_dotenv()

# ========== 数据库配置 ==========
LIBSQL_URL = os.getenv("LIBSQL_URL", "")
LIBSQL_TOKEN = os.getenv("LIBSQL_TOKEN", "")

# ========== 飞书告警推送 ==========
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK", "")

# ========== Streamlit 后台管理员账号 ==========
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "123456")


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

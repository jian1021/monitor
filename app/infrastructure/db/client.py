"""libSQL/Turso 连接客户端。"""

from libsql_client import create_client_sync

from app.core.settings import LIBSQL_TOKEN, LIBSQL_URL


def get_db_client():
    if not LIBSQL_URL or not LIBSQL_TOKEN:
        print("❌ 缺失数据库 URL 或 Token 配置！")
        return None

    # 强制转换 libsql:// 为 https:// 避免 WebSocket (wss://) 400 异常
    db_url = LIBSQL_URL.replace("libsql://", "https://")
    if not db_url.startswith("https://") and not db_url.startswith("http://"):
        db_url = f"https://{db_url}"

    try:
        return create_client_sync(url=db_url, auth_token=LIBSQL_TOKEN)
    except Exception as e:
        print(f"❌ 建立数据库连接失败: {e}")
        return None

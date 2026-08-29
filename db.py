"""远程标的库：Turso (libSQL) 的 asset_config 表取代本地 config.json"""
import os
import json
from libsql_client import create_client_sync
from config import LIBSQL_URL, LIBSQL_TOKEN

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






def load_instruments():
    """读取启用中的标的，返回与原 config.json 同构的字典（已增加 meteora）"""
    cfg = {"crypto_okx": [], "meteora": [], "convertible_bonds": [], "etfs": []}
    
    if not LIBSQL_TOKEN:
        print("❌ 错误：未配置 TURSO_AUTH_TOKEN / LIBSQL_TOKEN 环境变量")
        return cfg

    client = create_client_sync(url=LIBSQL_URL, auth_token=LIBSQL_TOKEN)
    try:
        rs = client.execute(
            "SELECT asset_type, code, name FROM asset_config WHERE enabled = 1 ORDER BY id"
        )
    finally:
        client.close()

    for asset_type, code, name in rs.rows:
        if asset_type == "crypto":
            cfg["crypto_okx"].append({"symbol": code})
        elif asset_type == "meteora":
            cfg["meteora"].append({"code": code, "name": name or code})
        elif asset_type == "bond":
            cfg["convertible_bonds"].append({"code": code, "name": name or code})
        elif asset_type == "etf":
            cfg["etfs"].append({"code": code, "name": name or code})
            
    return cfg
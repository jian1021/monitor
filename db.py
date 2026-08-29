"""远程标的库：Turso (libSQL) 的 asset_config 表取代本地 config.json"""
import os
import json
from libsql_client import create_client_sync

# 环境变量读取，优先读取 TURSO_AUTH_TOKEN 或 LIBSQL_TOKEN
LIBSQL_URL = os.getenv("TURSO_DATABASE_URL") or os.getenv("LIBSQL_URL") or "https://monitor-db-jian1021.aws-ap-northeast-1.turso.io"
LIBSQL_TOKEN = os.getenv("TURSO_AUTH_TOKEN") or os.getenv("LIBSQL_TOKEN")





def _seed_from_config(client, seed_file):
    """一次性迁移：把旧 config.json 的标的写入远程库（仅表为空时触发）"""
    try:
        with open(seed_file, encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        print(f"⚠️ 表为空且未找到 {seed_file}，跳过播种")
        return
        
    # 增加 meteora 的映射关系
    type_map = {
        "crypto_okx": "crypto", 
        "meteora": "meteora", 
        "convertible_bonds": "bond", 
        "etfs": "etf"
    }
    inserted_count = 0

    for key, asset_type in type_map.items():
        for item in cfg.get(key, []):
            code = str(item.get("symbol") or item.get("code") or item.get("address") or "").strip()
            if code:
                # 参数必须是 List [...]
                client.execute(
                    "INSERT INTO asset_config(asset_type, code, name) VALUES (?, ?, ?)",
                    [asset_type, code, item.get("name", code)]
                )
                inserted_count += 1
                
    print(f"✅ 成功从 {seed_file} 迁移播种了 {inserted_count} 条标的数据")


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
"""远程标的库：Turso (libSQL) 的 asset_config 表取代本地 config.json"""
import os
import json
from libsql_client import create_client_sync

# 环境变量读取，优先读取 TURSO_AUTH_TOKEN 或 LIBSQL_TOKEN
LIBSQL_URL = os.getenv("TURSO_DATABASE_URL") or os.getenv("LIBSQL_URL") or "https://monitor-db-jian1021.aws-ap-northeast-1.turso.io"
LIBSQL_TOKEN = os.getenv("TURSO_AUTH_TOKEN") or os.getenv("LIBSQL_TOKEN")


def init_db(seed_file="config.json"):
    """建表；表为空且本地存在旧 config.json 时自动播种一次"""
    if not LIBSQL_TOKEN:
        print("❌ 错误：未配置 TURSO_AUTH_TOKEN / LIBSQL_TOKEN 环境变量")
        return

    client = create_client_sync(url=LIBSQL_URL, auth_token=LIBSQL_TOKEN)
    try:
        # 1. 创建 asset_config 表
        client.execute("""
            CREATE TABLE IF NOT EXISTS asset_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_type TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT,
                enabled INTEGER DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 2. 创建 rsi_records 历史与日志表
        client.execute("""
            CREATE TABLE IF NOT EXISTS rsi_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_type TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT,
                price REAL NOT NULL,
                rsi REAL NOT NULL,
                period INTEGER DEFAULT 14,
                status TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 3. 创建索引（必须单独执行，不能拼接）
        client.execute("""
            CREATE INDEX IF NOT EXISTS idx_rsi_code_time ON rsi_records(code, created_at DESC)
        """)
        client.execute("""
            CREATE INDEX IF NOT EXISTS idx_rsi_status ON rsi_records(status)
        """)

        # 4. 检查是否需要播种初始数据
        rs = client.execute("SELECT COUNT(*) FROM asset_config")
        if rs.rows[0][0] == 0:
            _seed_from_config(client, seed_file)
    finally:
        client.close()


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
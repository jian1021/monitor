"""远程标的库：Turso (libSQL) 的 asset_config 表取代本地 config.json"""
import os
import json
from libsql_client import create_client_sync

# 凭据支持环境变量覆盖；默认值内嵌以便开箱即用（建议上线前改用环境变量并轮换 token）
# 备注：libsql:// 的 WebSocket 通道在当前客户端版本握手失败（400），统一走 HTTPS 端点
# 备注：该客户端的后台线程非 daemon 且退出阶段 close 会挂死，必须每次操作后显式 close
LIBSQL_URL = os.getenv("LIBSQL_URL", "https://monitor-db-jian1021.aws-ap-northeast-1.turso.io")
LIBSQL_TOKEN = os.getenv( "LIBSQL_TOKEN")


def init_db(seed_file="config.json"):
    """建表；表为空且本地存在旧 config.json 时自动播种一次"""
    client = create_client_sync(url=LIBSQL_URL, auth_token=LIBSQL_TOKEN)
    try:
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
    type_map = {"crypto_okx": "crypto", "convertible_bonds": "bond", "etfs": "etf"}
    for key, asset_type in type_map.items():
        for item in cfg.get(key, []):
            code = str(item.get("symbol") or item.get("code") or "").strip()
            if code:
                client.execute(
                    "INSERT INTO asset_config(asset_type, code, name) VALUES (?, ?, ?)",
                    (asset_type, code, item.get("name", code)),
                )


def load_instruments():
    """读取启用中的标的，返回与原 config.json 同构的字典，调用方零改动"""
    client = create_client_sync(url=LIBSQL_URL, auth_token=LIBSQL_TOKEN)
    try:
        rs = client.execute(
            "SELECT asset_type, code, name FROM asset_config WHERE enabled = 1 ORDER BY id"
        )
    finally:
        client.close()
    cfg = {"crypto_okx": [], "convertible_bonds": [], "etfs": []}
    for asset_type, code, name in rs.rows:
        if asset_type == "crypto":
            cfg["crypto_okx"].append({"symbol": code})
        elif asset_type == "bond":
            cfg["convertible_bonds"].append({"code": code, "name": name or code})
        elif asset_type == "etf":
            cfg["etfs"].append({"code": code, "name": name or code})
    return cfg


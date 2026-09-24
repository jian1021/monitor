"""module_settings 表：模块启停设置。"""

from app.infrastructure.db.client import get_db_client


def ensure_module_settings():
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute("""
            CREATE TABLE IF NOT EXISTS module_settings (
                module_name TEXT PRIMARY KEY,
                enabled INTEGER DEFAULT 1,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        return True
    except Exception as e:
        print(f"❌ 创建 module_settings 表失败: {e}")
        return False
    finally:
        client.close()


def get_module_settings() -> dict:
    """返回所有模块的启停状态，格式: {模块名: True/False}"""
    ensure_module_settings()
    client = get_db_client()
    if not client:
        return {"rsi": True, "crypto": True, "onchain_token": True, "meteora_pump": True, "robinhood_pump": True, "lp_alert": True}
    try:
        rs = client.execute("SELECT module_name, enabled FROM module_settings")
        result = {row[0]: bool(row[1]) for row in rs.rows}
        defaults = {"rsi": True, "crypto": True, "onchain_token": True, "meteora_pump": True, "robinhood_pump": True, "lp_alert": True}
        defaults.update(result)
        return defaults
    except Exception as e:
        print(f"❌ 读取模块设置失败: {e}")
        return {"rsi": True, "crypto": True, "onchain_token": True, "meteora_pump": True, "robinhood_pump": True, "lp_alert": True}
    finally:
        client.close()


def update_module_setting(module_name: str, enabled: bool):
    ensure_module_settings()
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute(
            "INSERT INTO module_settings (module_name, enabled, updated_at) VALUES (?, ?, datetime('now'))"
            " ON CONFLICT(module_name) DO UPDATE SET enabled = ?, updated_at = datetime('now')",
            [module_name, int(enabled), int(enabled)]
        )
        return True
    except Exception as e:
        print(f"❌ 更新模块设置失败: {e}")
        return False
    finally:
        client.close()

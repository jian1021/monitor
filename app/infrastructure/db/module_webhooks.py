"""module_webhooks 表：按模块指定飞书 Webhook（未配置则回落到全局 FEISHU_WEBHOOK）。"""

from app.infrastructure.db.client import get_db_client

TABLE = "module_webhooks"


def ensure_table() -> bool:
    """确保 module_webhooks 表存在（module_name → webhook）。"""
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                module_name TEXT PRIMARY KEY,
                webhook TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        return True
    except Exception as e:
        print(f"❌ 创建 module_webhooks 表失败: {e}")
        return False
    finally:
        client.close()


def get_module_webhook(module_name: str, fallback: str = "") -> str:
    """取某模块的独立 Webhook；没配置或读取失败时返回 fallback（通常是全局 Webhook）。"""
    if not ensure_table():
        return fallback
    client = get_db_client()
    if not client:
        return fallback
    try:
        rs = client.execute(
            f"SELECT webhook FROM {TABLE} WHERE module_name = ?", [module_name]
        )
        if rs.rows:
            value = str(rs.rows[0][0] or "").strip()
            return value or fallback
        return fallback
    except Exception as e:
        print(f"⚠️ 读取模块 Webhook 失败（{module_name}），回落到全局: {e}")
        return fallback
    finally:
        client.close()


def set_module_webhook(module_name: str, webhook: str | None) -> bool:
    """写入/清除模块 Webhook；传空串或 None 表示删除，恢复使用全局。"""
    if not ensure_table():
        return False
    client = get_db_client()
    if not client:
        return False
    try:
        value = (webhook or "").strip()
        if not value:
            client.execute(f"DELETE FROM {TABLE} WHERE module_name = ?", [module_name])
            return True
        client.execute(
            f"INSERT INTO {TABLE} (module_name, webhook, updated_at)"
            " VALUES (?, ?, datetime('now'))"
            " ON CONFLICT(module_name) DO UPDATE SET"
            " webhook = ?, updated_at = datetime('now')",
            [module_name, value, value],
        )
        return True
    except Exception as e:
        print(f"❌ 保存模块 Webhook 失败: {e}")
        return False
    finally:
        client.close()

"""fee-screen 持久化：监控设置（KV）、池子状态快照、告警记录。"""

from __future__ import annotations

import json
import time

from app.infrastructure.db.client import get_db_client


def _ensure_schema() -> bool:
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute("""
            CREATE TABLE IF NOT EXISTS fee_screen_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        client.execute("""
            CREATE TABLE IF NOT EXISTS fee_screen_state (
                pool_id TEXT PRIMARY KEY,
                metrics TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        client.execute("""
            CREATE TABLE IF NOT EXISTS fee_screen_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                pool_id TEXT,
                message TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        return True
    except Exception as e:
        print(f"❌ 创建 fee_screen 表失败: {e}")
        return False
    finally:
        client.close()


# ---------------------------------------------------------------- 设置 KV
def get_setting(key: str, default: str | None = None) -> str | None:
    _ensure_schema()
    client = get_db_client()
    if not client:
        return default
    try:
        rs = client.execute(
            "SELECT value FROM fee_screen_settings WHERE key = ?", [key])
        for row in rs.rows:
            return str(row[0])
        return default
    except Exception:
        return default
    finally:
        client.close()


def set_setting(key: str, value: str) -> bool:
    _ensure_schema()
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute(
            "INSERT INTO fee_screen_settings (key, value, updated_at)"
            " VALUES (?, ?, datetime('now'))"
            " ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = datetime('now')",
            [key, value, value],
        )
        return True
    except Exception as e:
        print(f"❌ 写入 fee_screen 设置失败: {e}")
        return False
    finally:
        client.close()


def get_screening_config_json() -> str | None:
    return get_setting("screening_config")


def set_screening_config_json(payload: str) -> bool:
    return set_setting("screening_config", payload)


# ---------------------------------------------------------------- 池子状态
def load_pool_state() -> dict[str, dict]:
    _ensure_schema()
    client = get_db_client()
    if not client:
        return {}
    try:
        rs = client.execute("SELECT pool_id, metrics FROM fee_screen_state")
        state = {}
        for row in rs.rows:
            try:
                state[str(row[0])] = json.loads(str(row[1]))
            except (TypeError, ValueError):
                continue
        return state
    except Exception:
        return {}
    finally:
        client.close()


def save_pool_state(state: dict[str, dict]) -> bool:
    _ensure_schema()
    client = get_db_client()
    if not client:
        return False
    try:
        for pool_id, metrics in state.items():
            client.execute(
                "INSERT INTO fee_screen_state (pool_id, metrics, updated_at)"
                " VALUES (?, ?, datetime('now'))"
                " ON CONFLICT(pool_id) DO UPDATE SET"
                " metrics = ?, updated_at = datetime('now')",
                [pool_id, json.dumps(metrics, ensure_ascii=False),
                 json.dumps(metrics, ensure_ascii=False)],
            )
        return True
    except Exception as e:
        print(f"❌ 写入 fee_screen 状态失败: {e}")
        return False
    finally:
        client.close()


# ---------------------------------------------------------------- 告警记录
def log_alert(kind: str, message: str, pool_id: str | None = None) -> bool:
    _ensure_schema()
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute(
            "INSERT INTO fee_screen_alerts (kind, pool_id, message, created_at)"
            " VALUES (?, ?, ?, datetime('now'))",
            [kind, pool_id, message],
        )
        return True
    except Exception as e:
        print(f"❌ 写入 fee_screen 告警失败: {e}")
        return False
    finally:
        client.close()


def list_alerts(limit: int = 50) -> list[dict]:
    _ensure_schema()
    client = get_db_client()
    if not client:
        return []
    try:
        rs = client.execute(
            "SELECT kind, pool_id, message, created_at FROM fee_screen_alerts"
            " ORDER BY id DESC LIMIT ?", [limit])
        return [
            {"kind": str(r[0]), "pool_id": r[1],
             "message": str(r[2]), "created_at": str(r[3])}
            for r in rs.rows
        ]
    except Exception:
        return []
    finally:
        client.close()


def now_ts() -> float:
    return time.time()

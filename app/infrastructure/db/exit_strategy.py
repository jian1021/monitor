"""退出策略池清单与告警状态持久化。"""

import json

from app.infrastructure.db.client import get_db_client

_KEY = "exit_strategy_v1"


def load_exit_strategy() -> dict:
    client = get_db_client()
    if not client:
        return {"enabled": False, "pools": [], "rsi_high": 90.0, "timeframe": "5m"}
    try:
        client.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT)")
        rows = client.execute("SELECT value FROM app_settings WHERE key = ?", [_KEY]).rows
        if rows and rows[0][0]:
            try:
                value = json.loads(rows[0][0])
                if isinstance(value, dict):
                    return {"enabled": False, "pools": [], "rsi_high": 90.0,
                            "timeframe": "5m", **value}
            except (TypeError, ValueError):
                pass
        return {"enabled": False, "pools": [], "rsi_high": 90.0, "timeframe": "5m"}
    except Exception as exc:
        print(f"退出策略配置读取失败: {exc}")
        return {"enabled": False, "pools": [], "rsi_high": 90.0, "timeframe": "5m"}
    finally:
        client.close()


def save_exit_strategy(config: dict) -> bool:
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT)")
        client.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [_KEY, json.dumps(config, ensure_ascii=False)],
        )
        return True
    except Exception as exc:
        print(f"退出策略配置保存失败: {exc}")
        return False
    finally:
        client.close()


def save_exit_alert_state(pool_key: str, state: dict) -> None:
    client = get_db_client()
    if not client:
        return
    try:
        client.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT)")
        client.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [f"exit_alert:{pool_key}", json.dumps(state)],
        )
    except Exception as exc:
        print(f"退出策略告警状态保存失败: {exc}")
    finally:
        client.close()


def load_exit_alert_state(pool_key: str) -> dict:
    client = get_db_client()
    if not client:
        return {}
    try:
        rows = client.execute("SELECT value FROM app_settings WHERE key = ?",
                              [f"exit_alert:{pool_key}"]).rows
        return json.loads(rows[0][0]) if rows and rows[0][0] else {}
    except Exception:
        return {}
    finally:
        client.close()

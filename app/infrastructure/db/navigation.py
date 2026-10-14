"""Sidebar navigation order persistence."""

import json

from app.infrastructure.db.client import get_db_client


NAVIGATION_GROUPS = {
    "全链扫描": ["meteora_scanner", "fee_screen"],
    "策略与数据": [
        "dashboard", "mainstream_crypto", "discover_lp", "lp_bands",
        "price_monitor", "pool_simulator", "token_distribution",
        "lp_position_alert", "robinhood_pools", "lp_apr", "exit_strategy",
    ],
    "链上代币": ["onchain_token", "meme_rsi_batch"],
    "系统管理": ["monitor_settings", "logout"],
}

_KEY = "sidebar_navigation_order_v1"


def normalize_navigation_order(value: object) -> dict[str, list[str]]:
    """Validate saved order and append any newly added pages in default order."""
    candidate = value if isinstance(value, dict) else {}
    normalized = {}
    for group, defaults in NAVIGATION_GROUPS.items():
        requested = candidate.get(group, [])
        requested = requested if isinstance(requested, list) else []
        valid = [page for page in requested if page in defaults]
        normalized[group] = valid + [page for page in defaults if page not in valid]
    return normalized


def get_navigation_order() -> dict[str, list[str]]:
    defaults = normalize_navigation_order({})
    client = get_db_client()
    if not client:
        return defaults
    try:
        client.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT)")
        rows = client.execute("SELECT value FROM app_settings WHERE key = ?", [_KEY]).rows
        if not rows or not rows[0][0]:
            return defaults
        try:
            return normalize_navigation_order(json.loads(rows[0][0]))
        except (TypeError, ValueError):
            return defaults
    except Exception as exc:
        print(f"侧边栏顺序读取失败: {exc}")
        return defaults
    finally:
        client.close()


def save_navigation_order(value: dict[str, list[str]]) -> bool:
    normalized = normalize_navigation_order(value)
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT)")
        client.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            [_KEY, json.dumps(normalized, ensure_ascii=False)],
        )
        return True
    except Exception as exc:
        print(f"侧边栏顺序保存失败: {exc}")
        return False
    finally:
        client.close()

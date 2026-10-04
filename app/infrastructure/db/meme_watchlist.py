"""meme_watchlist / meme_rsi_config 表：批量 meme 池子监控列表与阈值配置。"""

from app.infrastructure.db.client import get_db_client

MEME_RSI_PARAM_DEFAULTS = {
    "rsi_low": 10.0,
    "rsi_high": 90.0,
}


def ensure_meme_watchlist_schema():
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute("""
            CREATE TABLE IF NOT EXISTS meme_watchlist (
                address TEXT PRIMARY KEY,
                symbol TEXT,
                enabled INTEGER DEFAULT 1,
                added_at TEXT DEFAULT (datetime('now')),
                last_rsi REAL,
                last_price REAL,
                last_checked_at TEXT
            )
        """)
        client.execute("""
            CREATE TABLE IF NOT EXISTS meme_rsi_config (
                param TEXT PRIMARY KEY,
                value REAL NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        return True
    except Exception as e:
        print(f"❌ 创建 meme_watchlist 表失败: {e}")
        return False
    finally:
        client.close()


def add_meme_pools(pools) -> int:
    """写入监控列表；pools 为 address 或 (address, symbol)，重复地址按 symbol 覆盖。"""
    unique = {}
    for item in pools or []:
        if isinstance(item, (tuple, list)):
            address = str(item[0] if item else "").strip()
            symbol = str(item[1]) if len(item) > 1 and item[1] else ""
        else:
            address, symbol = str(item or "").strip(), ""
        if address and address not in unique:
            unique[address] = symbol
    if not unique:
        return 0
    ensure_meme_watchlist_schema()
    client = get_db_client()
    if not client:
        return 0
    try:
        for address, symbol in unique.items():
            client.execute(
                "INSERT INTO meme_watchlist (address, symbol, enabled, added_at)"
                " VALUES (?, ?, 1, datetime('now'))"
                " ON CONFLICT(address) DO UPDATE SET"
                " symbol = COALESCE(NULLIF(excluded.symbol, ''), meme_watchlist.symbol)",
                [address, symbol],
            )
        return len(unique)
    except Exception as e:
        print(f"❌ 写入监控列表失败: {e}")
        return 0
    finally:
        client.close()


def list_meme_watchlist_pools() -> list:
    ensure_meme_watchlist_schema()
    client = get_db_client()
    if not client:
        return []
    try:
        rs = client.execute(
            "SELECT address, symbol, enabled, last_rsi, last_price, last_checked_at"
            " FROM meme_watchlist ORDER BY added_at, address"
        )
        return [
            {
                "address": row[0],
                "symbol": row[1],
                "enabled": bool(row[2]),
                "last_rsi": row[3],
                "last_price": row[4],
                "last_checked_at": row[5],
            }
            for row in rs.rows
        ]
    except Exception as e:
        print(f"❌ 读取监控列表失败: {e}")
        return []
    finally:
        client.close()


def remove_meme_pool(address) -> bool:
    ensure_meme_watchlist_schema()
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute("DELETE FROM meme_watchlist WHERE address = ?", [str(address)])
        return True
    except Exception as e:
        print(f"❌ 删除监控池子失败: {e}")
        return False
    finally:
        client.close()


def set_meme_pool_enabled(address, enabled: bool) -> bool:
    ensure_meme_watchlist_schema()
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute("UPDATE meme_watchlist SET enabled = ? WHERE address = ?",
                       [int(bool(enabled)), str(address)])
        return True
    except Exception as e:
        print(f"❌ 更新监控池子状态失败: {e}")
        return False
    finally:
        client.close()


def update_meme_pool_rsi(address, rsi, price) -> bool:
    ensure_meme_watchlist_schema()
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute(
            "UPDATE meme_watchlist SET last_rsi = ?, last_price = ?,"
            " last_checked_at = datetime('now') WHERE address = ?",
            [float(rsi), None if price is None else float(price), str(address)],
        )
        return True
    except Exception as e:
        print(f"❌ 回写监控池子 RSI 失败: {e}")
        return False
    finally:
        client.close()


def get_meme_rsi_params() -> dict:
    base = dict(MEME_RSI_PARAM_DEFAULTS)
    ensure_meme_watchlist_schema()
    client = get_db_client()
    if not client:
        return base
    try:
        rs = client.execute("SELECT param, value FROM meme_rsi_config")
        for row in rs.rows:
            name = str(row[0])
            if name not in base:
                continue
            raw = row[1]
            if not isinstance(raw, (int, float, str)):
                continue
            try:
                base[name] = float(raw)
            except (TypeError, ValueError):
                continue
        return base
    except Exception as e:
        print(f"❌ 读取 meme RSI 参数失败: {e}")
        return base
    finally:
        client.close()


def update_meme_rsi_params(params: dict) -> bool:
    known = {k: v for k, v in dict(params or {}).items() if k in MEME_RSI_PARAM_DEFAULTS}
    if not known:
        return False
    ensure_meme_watchlist_schema()
    client = get_db_client()
    if not client:
        return False
    try:
        values = []
        for name, raw in known.items():
            try:
                values.append((name, float(raw)))
            except (TypeError, ValueError):
                continue
        if not values:
            return False
        for name, number in values:
            client.execute(
                "INSERT INTO meme_rsi_config (param, value, updated_at)"
                " VALUES (?, ?, datetime('now'))"
                " ON CONFLICT(param) DO UPDATE SET"
                " value = ?, updated_at = datetime('now')",
                [name, number, number],
            )
        return True
    except Exception as e:
        print(f"❌ 保存 meme RSI 参数失败: {e}")
        return False
    finally:
        client.close()

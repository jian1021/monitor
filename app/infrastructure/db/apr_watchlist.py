"""lp_apr_watchlist 表：LP 区间 APR 监控列表（pool + 区间参数 + 告警阈值）."""

from app.infrastructure.db.client import get_db_client

APR_PARAM_DEFAULTS = {}  # 预留：全局默认阈值（当前每池自带）


def ensure_apr_watchlist_schema():
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute("""
            CREATE TABLE IF NOT EXISTS lp_apr_watchlist (
                pool TEXT PRIMARY KEY,
                name TEXT,
                width REAL NOT NULL DEFAULT 10,
                capital REAL NOT NULL DEFAULT 10000,
                min_apr REAL,
                max_apr REAL,
                enabled INTEGER DEFAULT 1,
                last_apr REAL,
                last_checked_at TEXT,
                added_at TEXT DEFAULT (datetime('now'))
            )
        """)
        return True
    except Exception as e:
        print(f"❌ 创建 lp_apr_watchlist 表失败: {e}")
        return False
    finally:
        client.close()


def list_apr_watchlist() -> list[dict]:
    """全部行（dict 列表）；DB 不可用返回空列表."""
    ensure_apr_watchlist_schema()
    client = get_db_client()
    if not client:
        return []
    try:
        rs = client.execute(
            "SELECT pool, name, width, capital, min_apr, max_apr, enabled,"
            " last_apr, last_checked_at FROM lp_apr_watchlist ORDER BY added_at"
        )
        keys = ["pool", "name", "width", "capital", "min_apr", "max_apr",
                "enabled", "last_apr", "last_checked_at"]
        return [dict(zip(keys, row)) for row in rs.rows]
    except Exception as e:
        print(f"❌ 读取 lp_apr_watchlist 失败: {e}")
        return []
    finally:
        client.close()


def add_apr_pool(pool: str, name: str = "", width: float = 10, capital: float = 10000,
                 min_apr: float | None = None, max_apr: float | None = None) -> bool:
    pool = (pool or "").strip()
    if not pool:
        return False
    ensure_apr_watchlist_schema()
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute(
            "INSERT INTO lp_apr_watchlist (pool, name, width, capital, min_apr, max_apr)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(pool) DO UPDATE SET name=excluded.name, width=excluded.width,"
            " capital=excluded.capital, min_apr=excluded.min_apr, max_apr=excluded.max_apr",
            [pool, (name or "").strip(), float(width), float(capital),
             None if min_apr is None else float(min_apr),
             None if max_apr is None else float(max_apr)],
        )
        return True
    except Exception as e:
        print(f"❌ 添加 APR 监控池失败: {e}")
        return False
    finally:
        client.close()


def remove_apr_pool(pool: str) -> bool:
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute("DELETE FROM lp_apr_watchlist WHERE pool = ?", [pool])
        return True
    except Exception as e:
        print(f"❌ 删除 APR 监控池失败: {e}")
        return False
    finally:
        client.close()


def set_apr_pool_enabled(pool: str, enabled: bool) -> bool:
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute("UPDATE lp_apr_watchlist SET enabled = ? WHERE pool = ?",
                       [1 if enabled else 0, pool])
        return True
    except Exception as e:
        print(f"❌ 更新 APR 监控池启停失败: {e}")
        return False
    finally:
        client.close()


def record_apr_result(pool: str, apr: float | None) -> bool:
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute(
            "UPDATE lp_apr_watchlist SET last_apr = ?, last_checked_at = datetime('now')"
            " WHERE pool = ?",
            [None if apr is None else float(apr), pool],
        )
        return True
    except Exception as e:
        print(f"❌ 记录 APR 结果失败: {e}")
        return False
    finally:
        client.close()

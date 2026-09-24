"""pump_alert_sent 表：pump 类监控的跨轮 / 跨重启推送去重。"""

from app.infrastructure.db.client import get_db_client

PUMP_ALERT_TTL_HOURS = 24


def ensure_pump_alert_schema():
    """确保 pump_alert_sent 表存在（记录已推送过的代币地址）."""
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute("""
            CREATE TABLE IF NOT EXISTS pump_alert_sent (
                module TEXT NOT NULL,
                address TEXT NOT NULL,
                sent_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (module, address)
            )
        """)
        return True
    except Exception as e:
        print(f"❌ 创建 pump_alert_sent 表失败: {e}")
        return False
    finally:
        client.close()


def filter_unpushed(module: str, addresses, ttl_hours: int = PUMP_ALERT_TTL_HOURS) -> list:
    """返回 addresses 中 TTL 内尚未推送过的地址（保序去重）；出错时全部返回，宁可重复也不漏报."""
    unique = list(dict.fromkeys(a for a in addresses if a))
    if not unique:
        return []
    ensure_pump_alert_schema()
    client = get_db_client()
    if not client:
        return unique
    try:
        placeholders = ",".join("?" for _ in unique)
        rs = client.execute(
            f"SELECT address FROM pump_alert_sent WHERE module = ?"
            f" AND address IN ({placeholders}) AND sent_at >= datetime('now', ?)",
            [module, *unique, f"-{int(ttl_hours)} hours"],
        )
        pushed = {row[0] for row in rs.rows}
        return [a for a in unique if a not in pushed]
    except Exception as e:
        print(f"❌ 读取推送去重失败: {e}")
        return unique
    finally:
        client.close()


def mark_pushed(module: str, addresses) -> bool:
    """记录已推送地址；失败不抛异常，不阻塞告警发送."""
    unique = list(dict.fromkeys(a for a in addresses if a))
    if not unique:
        return False
    ensure_pump_alert_schema()
    client = get_db_client()
    if not client:
        return False
    try:
        for address in unique:
            client.execute(
                "INSERT INTO pump_alert_sent (module, address, sent_at)"
                " VALUES (?, ?, datetime('now'))"
                " ON CONFLICT(module, address) DO UPDATE SET sent_at = datetime('now')",
                [module, address],
            )
        return True
    except Exception as e:
        print(f"❌ 写入推送去重失败: {e}")
        return False
    finally:
        client.close()

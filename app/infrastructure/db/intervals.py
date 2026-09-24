"""module_intervals 表：模块执行间隔（分钟，UI 可改）。"""

from app.infrastructure.db.client import get_db_client

# 各子程序默认执行间隔（分钟）；module_intervals 表可按模块覆盖。
DEFAULT_INTERVALS = {
    "rsi":              1440,   # RSI 监控（Meteora 池 / 可转债 / ETF）：24 小时
    "crypto":             30,   # 加密货币（OKX）RSI 监控：30 分钟
    "onchain_token":      30,   # 链上代币 RSI 监控：30 分钟
    "meteora_pump":        5,   # Meteora pump 策略监控：5 分钟
    "robinhood_pump":      5,   # RobinHood pump 策略监控：5 分钟
    "lp_alert":            5,   # LP 仓位 / 池子价格告警：5 分钟
}


def ensure_interval_schema():
    """确保 module_intervals 表存在（module_name → interval_minutes）."""
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute("""
            CREATE TABLE IF NOT EXISTS module_intervals (
                module_name TEXT PRIMARY KEY,
                interval_minutes INTEGER NOT NULL DEFAULT 1440,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        return True
    except Exception as e:
        print(f"❌ 创建 module_intervals 表失败: {e}")
        return False
    finally:
        client.close()


def get_module_intervals(defaults: dict | None = None) -> dict:
    """返回各模块执行间隔（分钟）；DB 有值时覆盖默认值，任何失败回退默认。"""
    base = dict(DEFAULT_INTERVALS if defaults is None else defaults)
    ensure_interval_schema()
    client = get_db_client()
    if not client:
        return base
    try:
        rs = client.execute("SELECT module_name, interval_minutes FROM module_intervals")
        for row in rs.rows:
            try:
                name = str(row[0])
                if name in base:
                    base[name] = max(1, int(str(row[1])))
            except (TypeError, ValueError):
                continue
        return base
    except Exception as e:
        print(f"❌ 读取模块间隔设置失败: {e}")
        return base
    finally:
        client.close()


def update_module_interval(module_name: str, interval_minutes: int):
    ensure_interval_schema()
    client = get_db_client()
    if not client:
        return False
    try:
        minutes = max(1, int(interval_minutes))
        client.execute(
            "INSERT INTO module_intervals (module_name, interval_minutes, updated_at)"
            " VALUES (?, ?, datetime('now'))"
            " ON CONFLICT(module_name) DO UPDATE SET"
            " interval_minutes = ?, updated_at = datetime('now')",
            [module_name, minutes, minutes]
        )
        return True
    except Exception as e:
        print(f"❌ 更新模块间隔设置失败: {e}")
        return False
    finally:
        client.close()

"""远程标的库：Turso (libSQL) 的 asset_config 表取代本地 config.json"""
import os
import json
from libsql_client import create_client_sync
from config import LIBSQL_URL, LIBSQL_TOKEN

def get_db_client():



    if not LIBSQL_URL or not LIBSQL_TOKEN:
        print("❌ 缺失数据库 URL 或 Token 配置！")
        return None

    # 强制转换 libsql:// 为 https:// 避免 WebSocket (wss://) 400 异常
    db_url = LIBSQL_URL.replace("libsql://", "https://")
    if not db_url.startswith("https://") and not db_url.startswith("http://"):
        db_url = f"https://{db_url}"

    try:
        return create_client_sync(url=db_url, auth_token=LIBSQL_TOKEN)
    except Exception as e:
        print(f"❌ 建立数据库连接失败: {e}")
        return None






def ensure_asset_schema():
    """确保 asset_config 有 chain / timeframe 列（链上代币需要，老表自动补列）."""
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute("ALTER TABLE asset_config ADD COLUMN chain TEXT DEFAULT 'sol'")
    except Exception:
        pass
    try:
        client.execute("ALTER TABLE asset_config ADD COLUMN timeframe TEXT DEFAULT '1d'")
    except Exception:
        pass
    finally:
        client.close()
    return True


# ================= 模块启停设置 =================
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


def update_asset_timeframe(asset_type: str, old_tf: str, new_tf: str):
    """批量更新某类资产的时间级别"""
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute(
            "UPDATE asset_config SET timeframe = ? WHERE asset_type = ? AND timeframe = ?",
            [new_tf, asset_type, old_tf]
        )
        return True
    except Exception as e:
        print(f"❌ 更新时间级别失败: {e}")
        return False
    finally:
        client.close()


def load_instruments():
    """读取启用中的标的，返回与原 config.json 同构的字典（已增加 meteora / tokens）"""
    cfg = {"crypto_okx": [], "meteora": [], "convertible_bonds": [], "etfs": [], "tokens": []}

    if not LIBSQL_URL or not LIBSQL_TOKEN:
        print("❌ 错误：未配置 LIBSQL_URL / LIBSQL_TOKEN 环境变量")
        return cfg

    ensure_asset_schema()

    # 强制转换 libsql:// 为 https:// 避免 WebSocket (wss://) 400 异常
    db_url = LIBSQL_URL.replace("libsql://", "https://")
    if not db_url.startswith("https://") and not db_url.startswith("http://"):
        db_url = f"https://{db_url}"

    client = create_client_sync(url=db_url, auth_token=LIBSQL_TOKEN)
    try:
        try:
            rs = client.execute(
                "SELECT asset_type, code, name, chain, timeframe FROM asset_config"
                " WHERE enabled = 1 ORDER BY id"
            )
            has_chain, has_timeframe = True, True
        except Exception:
            try:
                rs = client.execute(
                    "SELECT asset_type, code, name, chain FROM asset_config"
                    " WHERE enabled = 1 ORDER BY id"
                )
                has_chain, has_timeframe = True, False
            except Exception:
                rs = client.execute(
                    "SELECT asset_type, code, name FROM asset_config"
                    " WHERE enabled = 1 ORDER BY id"
                )
                has_chain, has_timeframe = False, False
    finally:
        client.close()

    for row in rs.rows:
        asset_type, code, name = row[0], row[1], row[2]
        chain = (row[3] if has_chain and len(row) > 3 else None) or "sol"
        timeframe = (row[4] if has_timeframe and len(row) > 4 else None) or "1d"
        if asset_type == "crypto":
            cfg["crypto_okx"].append({"symbol": code})
        elif asset_type == "meteora":
            cfg["meteora"].append({"code": code, "name": name or code})
        elif asset_type == "bond":
            cfg["convertible_bonds"].append({"code": code, "name": name or code})
        elif asset_type == "etf":
            cfg["etfs"].append({"code": code, "name": name or code})
        elif asset_type == "token":
            cfg["tokens"].append({"code": code, "name": name or code, "chain": chain, "timeframe": timeframe})

    return cfg


# ================= 模块执行间隔设置（分钟，UI 可改） =================
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
                base[str(row[0])] = max(1, int(str(row[1])))
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
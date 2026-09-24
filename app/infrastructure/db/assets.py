"""asset_config 表：schema 补齐、读取启用标的、更新时间级别、写入告警状态。"""

from libsql_client import create_client_sync

from app.core.settings import LIBSQL_TOKEN, LIBSQL_URL
from app.infrastructure.db.client import get_db_client


def ensure_asset_schema():
    """确保 asset_config 有扩展字段（老表自动补列）."""
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute("ALTER TABLE asset_config ADD COLUMN chain TEXT DEFAULT 'sol'")
    except Exception:
        pass
    for column, declaration in (
        ("alarm_active", "INTEGER DEFAULT 0"),
        ("alert_latched", "INTEGER DEFAULT 0"),
        ("last_alert_at", "TEXT"),
        ("last_rsi", "REAL"),
        ("last_price", "REAL"),
    ):
        try:
            client.execute(f"ALTER TABLE asset_config ADD COLUMN {column} {declaration}")
        except Exception:
            pass
    try:
        client.execute("ALTER TABLE asset_config ADD COLUMN timeframe TEXT DEFAULT '1d'")
    except Exception:
        pass
    finally:
        client.close()
    return True


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
                "SELECT id, asset_type, code, name, chain, timeframe, alarm_active, alert_latched, last_alert_at, last_rsi, last_price FROM asset_config"
                " WHERE enabled = 1 ORDER BY id"
            )
            has_chain, has_timeframe = True, True
        except Exception:
            try:
                rs = client.execute(
                    "SELECT id, asset_type, code, name, chain FROM asset_config"
                    " WHERE enabled = 1 ORDER BY id"
                )
                has_chain, has_timeframe = True, False
            except Exception:
                rs = client.execute(
                    "SELECT id, asset_type, code, name FROM asset_config"
                    " WHERE enabled = 1 ORDER BY id"
                )
                has_chain, has_timeframe = False, False
    finally:
        client.close()

    for row in rs.rows:
        asset_id, asset_type, code, name = row[0], row[1], row[2], row[3]
        chain = (row[4] if has_chain and len(row) > 4 else None) or "sol"
        timeframe = (row[5] if has_timeframe and len(row) > 5 else None) or "1d"
        alert_state = {
            "id": asset_id,
            "alarm_active": bool(row[6]) if len(row) > 6 else False,
            "alert_latched": bool(row[7]) if len(row) > 7 else False,
            "last_alert_at": row[8] if len(row) > 8 else None,
            "last_rsi": row[9] if len(row) > 9 else None,
            "last_price": row[10] if len(row) > 10 else None,
        }
        if asset_type == "crypto":
            cfg["crypto_okx"].append({"symbol": code, "timeframe": timeframe, **alert_state})
        elif asset_type == "meteora":
            cfg["meteora"].append({"code": code, "name": name or code, **alert_state})
        elif asset_type == "bond":
            cfg["convertible_bonds"].append({"code": code, "name": name or code, **alert_state})
        elif asset_type == "etf":
            cfg["etfs"].append({"code": code, "name": name or code, **alert_state})
        elif asset_type == "token":
            cfg["tokens"].append({"code": code, "name": name or code, "chain": chain, "timeframe": timeframe, **alert_state})

    return cfg


def update_asset_alert_state(asset_id, *, active, latched, rsi, price, alert):
    """写入 RSI 标的运行状态；首次触发时记录报警时间。"""
    client = get_db_client()
    if not client:
        return False
    try:
        sql = (
            "UPDATE asset_config SET alarm_active = ?, alert_latched = ?, "
            "last_rsi = ?, last_price = ?, "
            "last_alert_at = CASE WHEN ? = 1 THEN datetime('now') ELSE last_alert_at END "
            "WHERE id = ?"
        )
        client.execute(sql, [int(active), int(latched), rsi, price, int(alert), asset_id])
        return True
    except Exception as exc:
        print(f"❌ 更新资产报警状态失败: {exc}")
        return False
    finally:
        client.close()

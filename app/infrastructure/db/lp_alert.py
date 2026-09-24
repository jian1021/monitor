"""lp_position_alert 表：建表、迁移与 CRUD。"""

from app.domain.lp_alert.models import STATUS_OPEN
from app.infrastructure.db.client import get_db_client


COLUMNS = (
    "kind", "chain", "pool_address", "wallet", "position_address",
    "pool_name", "token_x_symbol", "token_y_symbol",
    "lower_bin_id", "upper_bin_id", "min_price", "max_price", "floor_price",
    "target_mode", "target_pct", "entry_price",
    "enable_target_alert", "enable_floor_alert", "rearm", "enabled",
    "target_alerted", "floor_alerted",
    "alarm_active", "last_alert_at",
    "last_pnl_pct", "last_active_price", "last_checked_at",
    "is_out_of_range", "status",
    "token_id", "entry_tick", "price_basis",
)


MIGRATION_COLUMNS = (
    ("token_id", "INTEGER"),
    ("entry_tick", "INTEGER"),
    ("price_basis", "TEXT"),
    ("alarm_active", "INTEGER NOT NULL DEFAULT 0"),
    ("last_alert_at", "TEXT"),
)


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS lp_position_alert (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    kind                TEXT    NOT NULL DEFAULT 'dlmm',
    chain               TEXT    NOT NULL DEFAULT 'sol',
    pool_address        TEXT    NOT NULL,
    wallet              TEXT,
    position_address    TEXT,
    pool_name           TEXT,
    token_x_symbol      TEXT,
    token_y_symbol      TEXT,
    lower_bin_id        INTEGER,
    upper_bin_id        INTEGER,
    min_price           REAL,
    max_price           REAL,
    floor_price         REAL,
    target_mode         TEXT    NOT NULL DEFAULT 'pnl_pct',
    target_pct          REAL,
    entry_price         REAL,
    enable_target_alert INTEGER NOT NULL DEFAULT 1,
    enable_floor_alert  INTEGER NOT NULL DEFAULT 1,
    rearm               INTEGER NOT NULL DEFAULT 1,
    enabled             INTEGER NOT NULL DEFAULT 1,
    target_alerted      INTEGER NOT NULL DEFAULT 0,
    floor_alerted       INTEGER NOT NULL DEFAULT 0,
    alarm_active        INTEGER NOT NULL DEFAULT 0,
    last_alert_at       TEXT,
    last_pnl_pct        REAL,
    last_active_price   REAL,
    last_checked_at     TEXT,
    is_out_of_range     INTEGER,
    status              TEXT    NOT NULL DEFAULT 'open',
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    token_id            INTEGER,
    entry_tick          INTEGER,
    price_basis         TEXT
);
"""


CREATE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_lp_position_alert_enabled "
    "ON lp_position_alert(enabled, status);"
)


def _row_to_rule(row):
    rule = {name: row[i] for i, name in enumerate(COLUMNS)}
    rule["id"] = row[len(COLUMNS)]
    for flag in ("enable_target_alert", "enable_floor_alert", "rearm",
                 "enabled", "target_alerted", "floor_alerted", "alarm_active"):
        rule[flag] = bool(rule[flag])
    if rule.get("is_out_of_range") is not None:
        rule["is_out_of_range"] = bool(rule["is_out_of_range"])
    for numeric in ("min_price", "max_price", "floor_price", "target_pct",
                    "entry_price", "last_pnl_pct", "last_active_price"):
        if rule.get(numeric) is not None:
            rule[numeric] = float(rule[numeric])
    return rule


def _execute(sql, params=None, fetch=False):
    client = get_db_client()
    if not client:
        print("❌ 无法连接数据库")
        return None
    try:
        res = client.execute(sql, params or [])
        return res.rows if fetch else True
    except Exception as e:
        print(f"❌ SQL 执行失败: {e}")
        return None
    finally:
        client.close()


def ensure_table():
    client = get_db_client()
    if not client:
        print("❌ 无法连接数据库，跳过建表")
        return False
    try:
        client.batch([CREATE_TABLE_SQL, CREATE_INDEX_SQL])
        for column, decl in MIGRATION_COLUMNS:
            try:
                client.execute(
                    f"ALTER TABLE lp_position_alert ADD COLUMN {column} {decl}")
            except Exception:
                pass
        return True
    except Exception as e:
        print(f"❌ 建表失败: {e}")
        return False
    finally:
        client.close()


def add_rule(rule):
    cols = [c for c in COLUMNS if c in rule]
    placeholders = ", ".join("?" for _ in cols)
    sql = (f"INSERT INTO lp_position_alert ({', '.join(cols)}) "
           f"VALUES ({placeholders})")
    return _execute(sql, [rule[c] for c in cols]) is True


def load_rules(enabled_only=True, kind=None):
    cols = ", ".join(COLUMNS)
    sql = f"SELECT {cols}, id FROM lp_position_alert"
    where, params = [], []
    if enabled_only:
        where.append("enabled = 1")
        where.append("status = ?")
        params.append(STATUS_OPEN)
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id"
    rows = _execute(sql, params, fetch=True)
    if not isinstance(rows, list):
        return []
    return [_row_to_rule(r) for r in rows]


def delete_rule(rule_id):
    return _execute("DELETE FROM lp_position_alert WHERE id = ?", [rule_id]) is True


def set_enabled(rule_id, enabled):
    return _execute("UPDATE lp_position_alert SET enabled = ? WHERE id = ?",
                    [1 if enabled else 0, rule_id]) is True


def clear_alert_flag(rule_id, field):
    if field not in ("target_alerted", "floor_alerted"):
        raise ValueError(f"非法字段: {field}")
    other = "floor_alerted" if field == "target_alerted" else "target_alerted"
    return _execute(
        f"UPDATE lp_position_alert SET {field} = 0, "
        f"alarm_active = CASE WHEN {other} = 1 THEN 1 ELSE 0 END WHERE id = ?",
                    [rule_id]) is True


def set_status(rule_id, status):
    return _execute("UPDATE lp_position_alert SET status = ? WHERE id = ?",
                    [status, rule_id]) is True


def update_runtime(rule_id, values):
    cols = [c for c in values if c in COLUMNS]
    if not cols:
        return False
    sets = ", ".join(f"{c} = ?" for c in cols) + ", last_checked_at = datetime('now')"
    return _execute(f"UPDATE lp_position_alert SET {sets} WHERE id = ?",
                    [values[c] for c in cols] + [rule_id]) is True


def mark_alerted(rule_id, field):
    if field not in ("target_alerted", "floor_alerted"):
        raise ValueError(f"非法字段: {field}")
    return _execute(
        f"UPDATE lp_position_alert SET {field} = 1, "
        "alarm_active = 1, last_alert_at = datetime('now'), "
        "last_checked_at = datetime('now') WHERE id = ?",
        [rule_id]) is True

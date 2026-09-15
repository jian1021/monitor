"""lp_position_alert.py — LP 仓位 / 池子价格告警.

两种 kind:
  dlmm       Solana / Meteora DLMM，读链上仓位区间（minPrice）+ 真实持仓盈亏（pnlPctChange）
  pool_price Robinhood Chain / Uniswap，只有池子价格，盈利按价格涨幅、最低价取历史或手填

用法:
  python lp_position_alert.py            # 单轮检查后退出（GitHub Actions cron）
  python lp_position_alert.py --loop     # 本地常驻
"""
import sys
import time
import traceback

import requests

from config import FEISHU_WEBHOOK
from db import get_db_client
from send_feishu_msg import send_feishu_msg

METEORA_BASE = "https://dlmm.datapi.meteora.ag"
DEXSCREENER_BASE = "https://api.dexscreener.com"
GECKO_BASE = "https://api.geckoterminal.com/api/v2"
HEADERS = {"User-Agent": "Mozilla/5.0"}
DEFAULT_INTERVAL = 300

DEXSCREENER_CHAIN = {
    "sol": "solana", "solana": "solana",
    "bsc": "bsc", "base": "base",
    "eth": "ethereum", "robinhood": "robinhood",
}
GECKO_NETWORK = {
    "sol": "solana", "solana": "solana",
    "bsc": "bsc", "base": "base",
    "eth": "eth", "robinhood": "robinhood",
}


def to_float(value):
    try:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_dlmm_position(pos):
    return {
        "position_address": pos.get("positionAddress"),
        "min_price": to_float(pos.get("minPrice")),
        "max_price": to_float(pos.get("maxPrice")),
        "lower_bin_id": pos.get("lowerBinId"),
        "upper_bin_id": pos.get("upperBinId"),
        "pnl_pct": to_float(pos.get("pnlPctChange")),
        "active_price": to_float(pos.get("poolActivePrice")),
        "is_out_of_range": pos.get("isOutOfRange"),
        "is_closed": bool(pos.get("isClosed")),
    }


def parse_dexscreener_pair(payload):
    pairs = (payload or {}).get("pairs") or []
    if not pairs:
        return None
    p = pairs[0] or {}
    return {
        "price": to_float(p.get("priceUsd")),
        "base_symbol": (p.get("baseToken") or {}).get("symbol"),
        "quote_symbol": (p.get("quoteToken") or {}).get("symbol"),
        "liquidity_usd": to_float((p.get("liquidity") or {}).get("usd")),
        "pair_created_at": p.get("pairCreatedAt"),
    }


def floor_from_ohlcv(ohlcv_list):
    lows = []
    for row in ohlcv_list or []:
        if not row or len(row) < 5:
            continue
        low = to_float(row[3])
        if low is not None:
            lows.append(low)
    return min(lows) if lows else None


def _current_price(rule, cur):
    if rule.get("kind") == "dlmm":
        return cur.get("active_price")
    return cur.get("price")


def evaluate(rule, cur):
    fires = {"target": False, "floor": False}

    if rule.get("enable_target_alert") and rule.get("target_pct") is not None:
        tgt = float(rule["target_pct"])
        if rule.get("target_mode") == "pnl_pct":
            value = cur.get("pnl_pct")
            if value is not None and value >= tgt:
                fires["target"] = True
        else:
            value = cur.get("price")
            base = rule.get("entry_price")
            if value is not None and base is not None and float(base) > 0:
                # 必须用 (100+tgt)/100 而不是 (1+tgt/100)：后者对 base=100、tgt=10
                # 会算出 110.00000000000001，导致「价格正好 +10%」判定为未达标。
                target_price = float(base) * (100.0 + tgt) / 100.0
                if value >= target_price:
                    fires["target"] = True

    if rule.get("enable_floor_alert") and rule.get("floor_price") is not None:
        value = _current_price(rule, cur)
        if value is not None and value <= float(rule["floor_price"]):
            fires["floor"] = True

    return fires


def needs_rearm(rule, cur):
    if not rule.get("rearm") or not rule.get("floor_alerted"):
        return False
    if rule.get("floor_price") is None:
        return False
    value = _current_price(rule, cur)
    if value is None:
        return False
    return value > float(rule["floor_price"])


def units_plausible(active_price, pool_current_price, tolerance=100.0):
    """校验 poolActivePrice 与池子 current_price 同尺度.

    /pools 的 current_price 已实测为「token Y per token X」，而 poolActivePrice
    同为池子活跃 bin 的价格，两者本应相等。若相差超过 tolerance 倍，说明字段单位
    不一致或解析出错 —— 此时静默比较会让跌穿告警永久失效，必须拒绝判定并报警日志。
    任一侧缺失时返回 True（信息不足，不阻断）。
    """
    if active_price is None or pool_current_price is None:
        return True
    if active_price <= 0 or pool_current_price <= 0:
        return False
    ratio = active_price / pool_current_price
    return (1.0 / tolerance) <= ratio <= tolerance


STATUS_OPEN = "open"
STATUS_CLOSED = "closed"
STATUS_ERROR = "error"

COLUMNS = (
    "kind", "chain", "pool_address", "wallet", "position_address",
    "pool_name", "token_x_symbol", "token_y_symbol",
    "lower_bin_id", "upper_bin_id", "min_price", "max_price", "floor_price",
    "target_mode", "target_pct", "entry_price",
    "enable_target_alert", "enable_floor_alert", "rearm", "enabled",
    "target_alerted", "floor_alerted",
    "last_pnl_pct", "last_active_price", "last_checked_at",
    "is_out_of_range", "status",
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
    last_pnl_pct        REAL,
    last_active_price   REAL,
    last_checked_at     TEXT,
    is_out_of_range     INTEGER,
    status              TEXT    NOT NULL DEFAULT 'open',
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
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
                 "enabled", "target_alerted", "floor_alerted"):
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
    return [_row_to_rule(r) for r in (rows or [])]


def delete_rule(rule_id):
    return _execute("DELETE FROM lp_position_alert WHERE id = ?", [rule_id]) is True


def set_enabled(rule_id, enabled):
    return _execute("UPDATE lp_position_alert SET enabled = ? WHERE id = ?",
                    [1 if enabled else 0, rule_id]) is True


def reset_alerts(rule_id):
    return _execute(
        "UPDATE lp_position_alert SET target_alerted = 0, floor_alerted = 0 WHERE id = ?",
        [rule_id]) is True


def clear_alert_flag(rule_id, field):
    if field not in ("target_alerted", "floor_alerted"):
        raise ValueError(f"非法字段: {field}")
    return _execute(f"UPDATE lp_position_alert SET {field} = 0 WHERE id = ?",
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
        f"last_checked_at = datetime('now') WHERE id = ?",
        [rule_id]) is True

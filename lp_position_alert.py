"""lp_position_alert.py — LP 仓位 / 池子价格告警.

三种 kind:
  dlmm       Solana / Meteora DLMM，读链上仓位区间（minPrice）+ 真实持仓盈亏（pnlPctChange）
  evm_v4     Robinhood Chain / Uniswap v4，按钱包枚举仓位 NFT，用 tick 判定跌破区间下界
  pool_price 只有池子价格，盈利按价格涨幅、最低价取历史或手填

用法:
  python lp_position_alert.py            # 单轮检查后退出（GitHub Actions cron）
  python lp_position_alert.py --loop     # 本地常驻
"""
import math
import sys
import time
import traceback

import requests

from config import FEISHU_WEBHOOK, ROBINHOOD_RPC
from db import get_db_client
from keccak_pure import keccak256
from send_feishu_msg import send_feishu_msg
from db import get_db_client
from send_feishu_msg import send_feishu_msg

METEORA_BASE = "https://dlmm.datapi.meteora.ag"
VERSION = "2026-09-15.9"
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

EVM_RPC = ROBINHOOD_RPC or "https://rpc.mainnet.chain.robinhood.com"
V4_POSITION_MANAGER = "0x58daec3116aae6d93017baaea7749052e8a04fa7"
V4_STATE_VIEW = "0xf3334192d15450cdd385c8b70e03f9a6bd9e673b"
USDG_ADDRESS = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TICK_BASE = 1.0001
SEL_BALANCE_OF = "0x70a08231"
SEL_OWNER_OF = "0x6352211e"
SEL_POOL_AND_POSITION_INFO = "0x7ba03aad"
SEL_POSITION_LIQUIDITY = "0x1efeed33"
SEL_DECIMALS = "0x313ce567"
SEL_SYMBOL = "0x95d89b41"
SEL_GET_SLOT0 = "0xc815641c"
INFO_TICK_SHIFT = 8


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
    if rule.get("kind") == "evm_v4":
        return cur.get("active_price")
    return cur.get("price")


def evaluate(rule, cur):
    fires = {"target": False, "floor": False}

    if rule.get("kind") == "evm_v4":
        price = cur.get("active_price")
        if (rule.get("enable_floor_alert") and rule.get("floor_price") is not None
                and price is not None):
            if price < float(rule["floor_price"]):
                fires["floor"] = True
        if (rule.get("enable_target_alert") and rule.get("target_pct") is not None
                and price is not None and rule.get("entry_price")):
            if price >= float(rule["entry_price"]) * (100.0 + float(rule["target_pct"])) / 100.0:
                fires["target"] = True
        return fires

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
    if rule.get("kind") == "evm_v4":
        price = cur.get("active_price")
        floor = rule.get("floor_price")
        return price is not None and floor is not None and price > float(floor)
    if rule.get("floor_price") is None:
        return False
    value = _current_price(rule, cur)
    if value is None:
        return False
    return value > float(rule["floor_price"])


def _target_price(rule):
    """目标价门槛 = entry_price * (100 + target_pct) / 100。"""
    entry = rule.get("entry_price")
    tgt = rule.get("target_pct")
    if entry is None or tgt is None:
        return None
    try:
        return float(entry) * (100.0 + float(tgt)) / 100.0
    except (TypeError, ValueError):
        return None


def needs_rearm_target(rule, cur):
    """目标回落至门槛以下时解除 target_alerted，使下次达标重新告警。"""
    if not rule.get("rearm") or not rule.get("target_alerted"):
        return False
    if rule.get("target_mode") == "pnl_pct":
        value = cur.get("pnl_pct")
        tgt = rule.get("target_pct")
        if value is None or tgt is None:
            return False
        return value < float(tgt)
    tp = _target_price(rule)
    if tp is None:
        return False
    if rule.get("kind") == "evm_v4":
        price = cur.get("active_price")
        return price is not None and price < tp
    price = cur.get("price")
    if price is None or float(rule.get("entry_price")) <= 0:
        return False
    return price < tp


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


def reset_alerts(rule_id):
    return _execute(
        "UPDATE lp_position_alert SET alarm_active = 0 WHERE id = ?",
        [rule_id]) is True


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


def http_get_json(url, params=None, timeout=20):
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        print(f"⚠️ HTTP {resp.status_code}: {url}")
    except Exception as e:
        print(f"⚠️ 请求异常: {url} -> {e}")
    return None


def fetch_meteora_pool(pool_address):
    data = http_get_json(f"{METEORA_BASE}/pools/{pool_address}")
    if not data:
        return None
    x = data.get("token_x") or {}
    y = data.get("token_y") or {}
    return {
        "name": data.get("name"),
        "current_price": to_float(data.get("current_price")),
        "token_x_symbol": x.get("symbol"),
        "token_y_symbol": y.get("symbol"),
        "tvl": to_float(data.get("tvl")),
        "is_blacklisted": data.get("is_blacklisted"),
        "created_at": data.get("created_at"),
    }


def fetch_meteora_positions(pool_address, wallet):
    data = http_get_json(
        f"{METEORA_BASE}/positions/{pool_address}/pnl",
        params={"user": wallet, "status": "open", "page_size": 100},
        timeout=25,
    )
    if not isinstance(data, dict):
        return None
    return [parse_dlmm_position(p) for p in (data.get("positions") or [])]


def _dexscreener_chain(chain):
    return DEXSCREENER_CHAIN.get(str(chain or "").strip().lower(), str(chain).strip().lower())


def _gecko_network(chain):
    return GECKO_NETWORK.get(str(chain or "").strip().lower(), str(chain).strip().lower())


def fetch_dexscreener_pair(chain, pool_address):
    data = http_get_json(f"{DEXSCREENER_BASE}/latest/dex/pairs/{_dexscreener_chain(chain)}/{pool_address}")
    return parse_dexscreener_pair(data)


def fetch_pool_floor(chain, pool_address):
    data = http_get_json(
        f"{GECKO_BASE}/networks/{_gecko_network(chain)}/pools/{pool_address}/ohlcv/day",
        params={"limit": 100}, timeout=25,
    )
    if not isinstance(data, dict):
        return None
    attrs = (data.get("data") or {}).get("attributes") or {}
    return floor_from_ohlcv(attrs.get("ohlcv_list") or [])


def _cur_dlmm(rule):
    positions = fetch_meteora_positions(rule["pool_address"], rule["wallet"])
    if positions is None:
        return None, STATUS_ERROR
    for pos in positions:
        if pos.get("position_address") == rule.get("position_address"):
            if pos.get("is_closed"):
                return None, STATUS_CLOSED
            return pos, STATUS_OPEN
    return None, STATUS_CLOSED


def _cur_pool_price(rule):
    pair = fetch_dexscreener_pair(rule["chain"], rule["pool_address"])
    if pair is None or pair.get("price") is None:
        return None, STATUS_ERROR
    return pair, STATUS_OPEN


def _token_decimals(address_word):
    address = _hex_address(address_word)
    if int(address, 16) == 0:
        return 18
    result = _evm_call(address, SEL_DECIMALS)
    return int(result, 16) if result and len(result) > 2 else 18


def _cur_evm_v4(rule):
    token_id = rule.get("token_id")
    if token_id is None:
        return None, STATUS_ERROR
    info = _evm_call(V4_POSITION_MANAGER, SEL_POOL_AND_POSITION_INFO + _pad_uint(token_id))
    if not info:
        return None, STATUS_ERROR
    c0, c1 = _word(info, 0), _word(info, 1)
    fee, spacing, hooks = int(_word(info, 2), 16), int(_word(info, 3), 16), _word(info, 4)
    pool_id = _pool_id(c0, c1, fee, spacing, hooks)
    if not pool_id:
        return None, STATUS_ERROR
    slot = _evm_call(V4_STATE_VIEW, SEL_GET_SLOT0 + pool_id)
    if not slot:
        return None, STATUS_ERROR
    active_tick = _signed24(_word(slot, 1))
    price, _, _ = quote_price(active_tick, c0, c1,
                              _token_decimals(c0), _token_decimals(c1))
    return {"active_tick": active_tick, "active_price": price,
            "pnl_pct": None, "price": None}, STATUS_OPEN


def build_message(rule, cur, fired, pool_info):
    name = (pool_info or {}).get("name") or rule.get("pool_name") or rule["pool_address"]
    lines = []
    if rule.get("kind") == "evm_v4":
        basis = rule.get("price_basis") or rule.get("token_y_symbol") or ""
        price = cur.get("active_price")
        if "target" in fired:
            base = float(rule.get("entry_price") or 0)
            pct = (price / base - 1) * 100 if base else 0.0
            lines.append(f"🎯 【盈利达标】现价 {price:.10g}，较建立规则时 {base:.10g} "
                         f"涨 {pct:.2f}%（目标 {float(rule['target_pct']):.2f}%）")
        if "floor" in fired:
            lines.append(f"🚨 【跌穿区间下界】现价 {price:.10g} < 下界 "
                         f"{float(rule['floor_price']):.10g}")
        lines.append("")
        lines.append(f"池子: {name}（robinhood / uniswap v4，{basis} 计价）")
        lines.append(f"仓位 tokenId: {rule.get('token_id')}｜当前 tick {cur.get('active_tick')}")
        lines.append(f"区间: {float(rule.get('lower_price') or 0):.10g} ~ "
                     f"{float(rule.get('max_price') or 0):.10g}")
        lines.append(f"poolId: {rule['pool_address']}")
        return "\n".join(lines)
    if "target" in fired:
        if rule.get("target_mode") == "pnl_pct":
            lines.append(f"🎯 【盈利达标】真实持仓盈亏 {cur.get('pnl_pct'):.2f}% "
                         f"（目标 {float(rule['target_pct']):.2f}%）")
        else:
            base = float(rule["entry_price"])
            pct = (cur["price"] / base - 1) * 100 if base else 0.0
            lines.append(f"🎯 【盈利达标】现价 {cur['price']:.10g}，较登记价 {base:.10g} "
                         f"涨 {pct:.2f}%（目标 {float(rule['target_pct']):.2f}%）")
    if "floor" in fired:
        price = _current_price(rule, cur)
        lines.append(f"🚨 【价格跌穿】现价 {price:.10g} ≤ 阈值 {float(rule['floor_price']):.10g}")
    lines.append("")
    lines.append(f"池子: {name} ({rule['chain']})")
    lines.append(f"池子地址: {rule['pool_address']}")
    if rule.get("position_address"):
        lines.append(f"仓位地址: {rule['position_address']}")
    if rule.get("position_address"):
        lines.append(f"仓位区间: bin {rule.get('lower_bin_id')} ~ {rule.get('upper_bin_id')}")
    if rule["kind"] == "dlmm":
        lines.append(f"链接: https://app.meteora.ag/dlmm/{rule['pool_address']}")
    else:
        lines.append(f"链接: https://dexscreener.com/{_dexscreener_chain(rule['chain'])}/{rule['pool_address']}")
    return "\n".join(lines)


def check_rule(rule):
    pool_info = None
    if rule["kind"] == "dlmm":
        pool_info = fetch_meteora_pool(rule["pool_address"])
        cur, status = _cur_dlmm(rule)
        if status == STATUS_CLOSED:
            name = rule.get("pool_name") or rule["pool_address"]
            return {
                "status": STATUS_CLOSED,
                "fired": ["closed"],
                "cur": None,
                "message": (f"ℹ️ 【仓位已关闭】{name}\n"
                            f"仓位地址: {rule.get('position_address')}\n"
                            f"该仓位已不在开放列表中，规则自动停用。"),
            }
    elif rule["kind"] == "evm_v4":
        cur, status = _cur_evm_v4(rule)
        pool_info = {"name": rule.get("pool_name") or rule["pool_address"]}
    else:
        cur, status = _cur_pool_price(rule)
        if cur is not None:
            pool_info = {"name": " / ".join(
                filter(None, [cur.get("base_symbol"), cur.get("quote_symbol")]))}

    if cur is None:
        return {"status": status, "fired": [], "cur": None, "message": None}

    cur = dict(cur)
    if rule["kind"] == "dlmm":
        cur["price"] = None
        if not units_plausible(cur.get("active_price"),
                               (pool_info or {}).get("current_price")):
            print(f"⚠️ 规则 {rule['id']} 单位校验未通过: poolActivePrice="
                  f"{cur.get('active_price')} vs pool current_price="
                  f"{(pool_info or {}).get('current_price')}；"
                  f"本轮跳过跌穿判定（不告警），请人工核对 minPrice 单位。")
            rule = dict(rule)
            rule["enable_floor_alert"] = 0
    elif rule["kind"] != "evm_v4":
        cur["pnl_pct"] = None
        cur["active_price"] = None

    fired = [k for k, v in evaluate(rule, cur).items() if v]
    if rule.get("target_alerted") and "target" in fired:
        fired.remove("target")
    if rule.get("floor_alerted") and "floor" in fired:
        fired.remove("floor")

    message = None
    if fired:
        message = build_message(rule, cur, fired, pool_info)

    if needs_rearm(rule, cur):
        clear_alert_flag(rule["id"], "floor_alerted")
    if needs_rearm_target(rule, cur):
        clear_alert_flag(rule["id"], "target_alerted")

    return {"status": STATUS_OPEN, "fired": fired, "cur": cur, "message": message}


RUN_TIME_FIELDS = ("min_price", "max_price", "last_pnl_pct", "last_active_price")


def run_once():
    rules = load_rules(enabled_only=True)
    if not rules:
        print("ℹ️ 没有启用的 LP 告警规则")
        return

    print(f"🔍 本轮检查 {len(rules)} 条 LP 告警规则 ...")
    ok = fail = 0
    for rule in rules:
        try:
            result = check_rule(rule)
        except Exception:
            print(f"❌ 规则 {rule['id']} 检查异常")
            traceback.print_exc()
            fail += 1
            continue

        cur = result["cur"]
        if result["status"] == STATUS_ERROR:
            print(f"⚠️ 规则 {rule['id']} 取数失败，跳过（不告警）")
            set_status(rule["id"], STATUS_ERROR)
            fail += 1
            continue

        ok += 1
        values = {"status": result["status"]}
        if cur:
            if cur.get("min_price") is not None:
                values["min_price"] = cur["min_price"]
            if cur.get("max_price") is not None:
                values["max_price"] = cur["max_price"]
            values["last_pnl_pct"] = cur.get("pnl_pct")
            values["last_active_price"] = _current_price(rule, cur)
            if cur.get("is_out_of_range") is not None:
                values["is_out_of_range"] = 1 if cur["is_out_of_range"] else 0
        update_runtime(rule["id"], values)

        if result["fired"]:
            print(f"🚨 规则 {rule['id']} 触发: {result['fired']}")
            text = result["message"]
            if text:
                send_feishu_msg(FEISHU_WEBHOOK, text)
            for name in ("target", "floor"):
                if name in result["fired"]:
                    mark_alerted(rule["id"], f"{name}_alerted")
            if "closed" in result["fired"]:
                set_status(rule["id"], STATUS_CLOSED)
                set_enabled(rule["id"], False)

    print(f"📊 本轮完成: 成功 {ok}，失败 {fail}")


def run_loop(interval):
    print(f"🔁 常驻监控模式: 每 {interval} 秒检查一次 (Ctrl+C 退出)")
    while True:
        try:
            run_once()
        except KeyboardInterrupt:
            print("\n👋 已退出常驻监控")
            break
        except Exception:
            traceback.print_exc()
        time.sleep(interval)


def main():
    ensure_table()
    args = sys.argv[1:]
    if "--loop" in args:
        interval = DEFAULT_INTERVAL
        if "--interval" in args:
            idx = args.index("--interval")
            if len(args) > idx + 1:
                interval = int(args[idx + 1])
        run_loop(interval)
    else:
        run_once()


CHAIN_OPTIONS = ["sol"]
POOL_PRICE_CHAINS = ["robinhood", "bsc", "base", "eth"]


def preview_dlmm(pool_address, wallet):
    pool = fetch_meteora_pool(pool_address)
    if not pool:
        return {"ok": False, "error": "❌ 连接失败：Meteora 未找到该池子地址，请核对池子地址是否正确。",
                "pool": None, "positions": []}
    positions = fetch_meteora_positions(pool_address, wallet)
    if positions is None:
        return {"ok": False, "error": "❌ 连接成功，但读取仓位失败（接口异常），请稍后重试。",
                "pool": pool, "positions": []}
    if not positions:
        return {"ok": False, "error": "⚠️ 该钱包在此池没有开放仓位。请确认钱包地址，或该仓位是否已关闭。",
                "pool": pool, "positions": []}
    return {"ok": True, "error": None, "pool": pool, "positions": positions}


def preview_pool_price(chain, pool_address):
    pair = fetch_dexscreener_pair(chain, pool_address)
    if not pair or pair.get("price") is None:
        return {"ok": False,
                "error": "❌ 连接失败：Dexscreener 未找到该池子地址，请核对地址与所属链是否正确。",
                "pair": None, "floor": None}
    return {"ok": True, "error": None, "pair": pair, "floor": None}


def fetch_open_portfolio(wallet, page_size=50):
    data = http_get_json(
        f"{METEORA_BASE}/portfolio/open",
        params={"user": wallet, "page_size": page_size},
        timeout=25,
    )
    if not isinstance(data, dict):
        return None
    pools = []
    for p in data.get("pools") or []:
        pools.append({
            "pool_address": p.get("poolAddress"),
            "pool_name": " / ".join(filter(None, [p.get("tokenX"), p.get("tokenY")])),
            "token_x_symbol": p.get("tokenX"),
            "token_y_symbol": p.get("tokenY"),
            "pool_price": to_float(p.get("poolPrice")),
            "pnl_pct": to_float(p.get("pnlPctChange")),
            "open_positions": p.get("openPositionCount"),
            "position_addresses": p.get("listPositions") or [],
        })
    return pools


def preview_wallet(wallet):
    pools = fetch_open_portfolio(wallet)
    if pools is None:
        return {"ok": False,
                "error": "❌ 连接失败：读取钱包组合失败，请核对钱包地址或稍后重试。",
                "pools": []}
    if not pools:
        return {"ok": False,
                "error": "⚠️ 该钱包没有开放的 LP 仓位（建仓后请稍等片刻再试）。",
                "pools": []}
    return {"ok": True, "error": None, "pools": pools}


_LAST_EVM_ERROR = ""
_LAST_SCAN_MODE = ""


def _fail(reason):
    global _LAST_EVM_ERROR
    _LAST_EVM_ERROR = reason
    print(f"⚠️ EVM 失败: {reason}")
    return None


def _evm_rpc(method, params, timeout=45, tries=4):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    for attempt in range(tries):
        try:
            resp = requests.post(EVM_RPC, json=body, headers=HEADERS, timeout=timeout)
            if resp.status_code == 429:
                if attempt == tries - 1:
                    return _fail(f"{method}: HTTP 429 请求过于频繁（公共节点限流）")
                time.sleep(2 * (attempt + 1))
                continue
            if resp.status_code != 200:
                return _fail(f"{method}: HTTP {resp.status_code}")
            payload = resp.json()
            if "error" in payload:
                return _fail(f"{method}: {str(payload['error'])[:120]}")
            return payload.get("result")
        except Exception as e:
            if attempt == tries - 1:
                return _fail(f"{method}: {type(e).__name__} {str(e)[:100]}")
            time.sleep(1.5)
    return None


def _evm_call(to, data):
    return _evm_rpc("eth_call", [{"to": to, "data": data}, "latest"])


def _word(hexstr, index):
    return hexstr[2 + index * 64:2 + (index + 1) * 64]


def _signed24(value):
    if isinstance(value, str):
        value = int(value, 16)
    value &= 0xFFFFFF
    return value - (1 << 24) if value >= (1 << 23) else value


def _pad_uint(value):
    return format(int(value), "064x")


def _hex_address(word_hex):
    return "0x" + word_hex[24:]


def _pool_id(c0_word, c1_word, fee, spacing, hooks_word):
    packed = (bytes.fromhex(c0_word) + bytes.fromhex(c1_word)
              + int(fee).to_bytes(32, "big") + int(spacing).to_bytes(32, "big")
              + bytes.fromhex(hooks_word))
    return keccak256(packed).hex()


def _decode_abi_string(hexstr):
    if not hexstr or len(hexstr) <= 2:
        return None
    try:
        body = hexstr[2:]
        offset = int(body[0:64], 16) * 2
        length = int(body[offset:offset + 64], 16) * 2
        return bytes.fromhex(body[offset + 64:offset + 64 + length]).decode("utf-8", "replace")
    except Exception:
        return None


def _token_meta(address_word, cache=None):
    address = _hex_address(address_word)
    if int(address, 16) == 0:
        return "ETH", 18
    if cache is not None and address in cache:
        return cache[address]
    decimals = _evm_call(address, SEL_DECIMALS)
    dec = int(decimals, 16) if decimals and len(decimals) > 2 else 18
    symbol = _decode_abi_string(_evm_call(address, SEL_SYMBOL))
    result = ((symbol or address[:10]), dec)
    if cache is not None:
        cache[address] = result
    return result


def quote_price(tick, c0_word, c1_word, dec0, dec1):
    """把 tick 换算成「以 USDG 计价」的价格.

    返回 (price, tick_sign, basis)：tick_sign 为 +1 表示价格随 tick 上升，
    -1 表示随 tick 下降（此时 USDG 是 currency0，价格是它的倒数）。
    池子两侧都没有 USDG 时退回 token1 计价，basis 为 None。
    """
    c0, c1 = _hex_address(c0_word), _hex_address(c1_word)
    if c1.lower() == USDG_ADDRESS:
        return (TICK_BASE ** tick) * (10 ** (dec0 - dec1)), 1, "USDG"
    if c0.lower() == USDG_ADDRESS:
        return (TICK_BASE ** (-tick)) * (10 ** (dec1 - dec0)), -1, "USDG"
    return (TICK_BASE ** tick) * (10 ** (dec0 - dec1)), 1, None


MULTICALL3 = "0xca11bde05977b3631167028862be2a173976ca11"
SEL_AGGREGATE3 = "0x82ad56cb"


def _pad32(data):
    return data + b"\x00" * ((32 - len(data) % 32) % 32)


def encode_aggregate3(calls):
    """calls: [(目标地址, calldata bytes)] -> Multicall3.aggregate3 的 eth_call data."""
    blocks = []
    for target, data in calls:
        blocks.append(int(target, 16).to_bytes(32, "big")
                      + (1).to_bytes(32, "big")
                      + (96).to_bytes(32, "big")
                      + len(data).to_bytes(32, "big")
                      + _pad32(data))
    offsets, cursor = [], 32 * len(blocks)
    for block in blocks:
        offsets.append(cursor)
        cursor += len(block)
    array = len(blocks).to_bytes(32, "big")
    array += b"".join(offset.to_bytes(32, "big") for offset in offsets)
    array += b"".join(blocks)
    return SEL_AGGREGATE3 + (32).to_bytes(32, "big").hex() + array.hex()


def decode_aggregate3(result_hex):
    """(bool,bytes)[] -> [(success, bytes)]；返回长度不足的条目视为失败."""
    body = bytes.fromhex(result_hex[2:] if result_hex.startswith("0x") else result_hex)
    arg_offset = int.from_bytes(body[0:32], "big")
    count = int.from_bytes(body[arg_offset:arg_offset + 32], "big")
    table = arg_offset + 32
    out = []
    for i in range(count):
        rel = int.from_bytes(body[table + i * 32:table + (i + 1) * 32], "big")
        pos = table + rel
        success = int.from_bytes(body[pos:pos + 32], "big") == 1
        bytes_rel = int.from_bytes(body[pos + 32:pos + 64], "big")
        bpos = pos + bytes_rel
        length = int.from_bytes(body[bpos:bpos + 32], "big")
        out.append((success, body[bpos + 32:bpos + 32 + length]))
    return out


def call_data(selector_hex, *values):
    data = bytes.fromhex(selector_hex[2:])
    for value in values:
        if isinstance(value, str):
            raw = value[2:] if value.startswith("0x") else value
            data += bytes.fromhex(raw.rjust(64, "0"))
        else:
            data += int(value).to_bytes(32, "big")
    return data


def _multicall(calls):
    if not calls:
        return []
    result = _evm_call(MULTICALL3, encode_aggregate3(calls))
    if not result:
        return None
    return decode_aggregate3(result)


def _uint_from(entry):
    success, ret = entry
    if not success or len(ret) < 32:
        return None
    return int.from_bytes(ret[:32], "big")


RECENT_BLOCK_WINDOW = 5000


def _scan_transfer_token_ids(wallet):
    """扫描 PositionManager 的 Transfer 事件，取出转入过该钱包的 tokenId.

    返回 (token_ids, 扫描模式)。公共 Robinhood 节点对 eth_getLogs 的区块跨度
    有硬限制（实测 5000 块可、10000 块即拒），故全历史扫描失败时降级为最近窗口，
    模式标记为 "recent"，由界面提示用户改用手填 tokenId。
    """
    topic = "0x" + "0" * 24 + wallet[2:].lower()
    common = {"address": V4_POSITION_MANAGER, "topics": [TRANSFER_TOPIC, None, topic],
              "toBlock": "latest"}
    global _LAST_SCAN_MODE
    logs = _evm_rpc("eth_getLogs", [dict(common, fromBlock="0x0")], timeout=90)
    if logs is not None:
        _LAST_SCAN_MODE = "full"
        return sorted({int(entry["topics"][3], 16) for entry in logs}), "full"
    head = _evm_rpc("eth_blockNumber", [])
    if isinstance(head, str) and head.startswith("0x"):
        start = hex(max(int(head, 16) - RECENT_BLOCK_WINDOW, 0))
        logs = _evm_rpc("eth_getLogs", [dict(common, fromBlock=start)], timeout=90)
        if logs is not None:
            _LAST_SCAN_MODE = "recent"
            return sorted({int(entry["topics"][3], 16) for entry in logs}), "recent"
    _LAST_SCAN_MODE = ""
    return None, None


def fetch_evm_v4_positions(wallet, token_ids=None):
    wallet = (wallet or "").strip()
    if not wallet.lower().startswith("0x") or len(wallet) != 42:
        return None
    if token_ids is None:
        token_ids, _mode = _scan_transfer_token_ids(wallet)
        if token_ids is None:
            return None
    else:
        token_ids = sorted({int(t) for t in token_ids})
    if not token_ids:
        return []

    probe = []
    for token_id in token_ids:
        probe.append((V4_POSITION_MANAGER, call_data(SEL_OWNER_OF, token_id)))
        probe.append((V4_POSITION_MANAGER, call_data(SEL_POSITION_LIQUIDITY, token_id)))
    decoded = _multicall(probe)
    if decoded is None or len(decoded) != len(probe):
        return None

    alive = []
    for index, token_id in enumerate(token_ids):
        success, raw = decoded[2 * index]
        owner = "0x" + raw[12:32].hex() if success and len(raw) >= 32 else None
        if not owner or owner.lower() != wallet.lower():
            continue
        liquidity = _uint_from(decoded[2 * index + 1])
        if not liquidity:
            continue
        alive.append((token_id, liquidity))
    if not alive:
        return []

    infos = _multicall([(V4_POSITION_MANAGER, call_data(SEL_POOL_AND_POSITION_INFO, tid))
                        for tid, _ in alive])
    if infos is None or len(infos) != len(alive):
        return None

    parsed = []
    for (token_id, liquidity), entry in zip(alive, infos):
        success, raw = entry
        if not success or len(raw) < 192:
            continue
        hexstr = "0x" + raw.hex()
        c0, c1 = _word(hexstr, 0), _word(hexstr, 1)
        fee, spacing, hooks = (int(_word(hexstr, 2), 16), int(_word(hexstr, 3), 16),
                               _word(hexstr, 4))
        packed = int(_word(hexstr, 5), 16)
        tick_lower = _signed24((packed >> INFO_TICK_SHIFT) & 0xFFFFFF)
        tick_upper = _signed24((packed >> (INFO_TICK_SHIFT + 24)) & 0xFFFFFF)
        if tick_lower >= tick_upper:
            continue
        parsed.append((token_id, liquidity, c0, c1, fee, spacing, hooks,
                       tick_lower, tick_upper))
    if not parsed:
        return []

    pool_ids = []
    for item in parsed:
        pid = _pool_id(item[2], item[3], item[4], item[5], item[6])
        if pid not in pool_ids:
            pool_ids.append(pid)
    slots = _multicall([(V4_STATE_VIEW, call_data(SEL_GET_SLOT0, pid)) for pid in pool_ids])
    if slots is None or len(slots) != len(pool_ids):
        return None
    active_ticks = {}
    for pid, entry in zip(pool_ids, slots):
        success, raw = entry
        active_ticks[pid] = _signed24("0x" + raw[32:64].hex()) if success and len(raw) >= 64 else None

    token_addresses = []
    for item in parsed:
        for word in (item[2], item[3]):
            address = _hex_address(word)
            if address not in token_addresses:
                token_addresses.append(address)
    meta = {}
    queried = [a for a in token_addresses if int(a, 16) != 0]
    meta_calls = []
    for address in queried:
        meta_calls.append((address, call_data(SEL_DECIMALS)))
        meta_calls.append((address, call_data(SEL_SYMBOL)))
    if meta_calls:
        results = _multicall(meta_calls)
        if results is None or len(results) != len(meta_calls):
            return None
        for index, address in enumerate(queried):
            decimals = _uint_from(results[2 * index]) or 18
            symbol_ok, symbol_raw = results[2 * index + 1]
            symbol = (_decode_abi_string("0x" + symbol_raw.hex())
                      if symbol_ok and symbol_raw else None)
            meta[address] = (symbol or address[:10], decimals)
    for address in token_addresses:
        if int(address, 16) == 0:
            meta[address] = ("ETH", 18)

    positions = []
    for token_id, liquidity, c0, c1, fee, spacing, hooks, tick_lower, tick_upper in parsed:
        symbol0, dec0 = meta[_hex_address(c0)]
        symbol1, dec1 = meta[_hex_address(c1)]
        active_tick = active_ticks.get(_pool_id(c0, c1, fee, spacing, hooks))

        p_lower, sign, basis = quote_price(tick_lower, c0, c1, dec0, dec1)
        p_upper, _, _ = quote_price(tick_upper, c0, c1, dec0, dec1)
        if sign > 0:
            tick_min, tick_max = tick_lower, tick_upper
        else:
            tick_min, tick_max = tick_upper, tick_lower
        positions.append({
            "token_id": token_id,
            "pool_id": "0x" + _pool_id(c0, c1, fee, spacing, hooks),
            "pool_name": f"{symbol0}/{symbol1}",
            "token_x_symbol": symbol0,
            "token_y_symbol": symbol1,
            "price_basis": basis or symbol1,
            "fee": fee,
            "tick_spacing": spacing,
            "tick_lower": tick_lower,
            "tick_upper": tick_upper,
            "tick_min": tick_min,
            "tick_max": tick_max,
            "lower_price": min(p_lower, p_upper),
            "upper_price": max(p_lower, p_upper),
            "active_tick": active_tick,
            "current_price": (quote_price(active_tick, c0, c1, dec0, dec1)[0]
                              if active_tick is not None else None),
            "in_range": (active_tick is not None and tick_lower <= active_tick <= tick_upper),
            "liquidity": liquidity,
        })
    return positions


def preview_evm_wallet(wallet, token_ids_text=""):
    wallet = (wallet or "").strip()
    if not wallet.lower().startswith("0x") or len(wallet) != 42:
        return {"ok": False, "error": "⚠️ 请填写 Robinhood 链的 EVM 钱包地址（0x 开头、42 位）。",
                "positions": []}
    manual = [int(t) for t in str(token_ids_text or "").replace(",", " ").split()
              if t.strip().isdigit()]
    positions = fetch_evm_v4_positions(wallet, manual or None)
    if positions is None:
        detail = f"（原因：{_LAST_EVM_ERROR}）" if _LAST_EVM_ERROR else ""
        hint = ("" if manual else
                "　公共 RPC 不允许全历史日志查询，可在下方手动填入 tokenId"
                "（从 Uniswap 界面复制），或配置 ROBINHOOD_RPC 换用付费节点。")
        return {"ok": False,
                "error": f"❌ 连接失败：读取链上仓位失败{detail}{hint}",
                "positions": []}
    if not positions:
        if _LAST_SCAN_MODE == "recent":
            return {"ok": False,
                    "error": f"⚠️ 只扫描了最近 {RECENT_BLOCK_WINDOW} 个区块"
                             "（公共 RPC 不允许全历史日志查询），未发现仓位。"
                             "请在下方手动填入 tokenId（从 Uniswap 界面复制），"
                             "或配置 ROBINHOOD_RPC 换用付费节点。",
                    "positions": []}
        return {"ok": False,
                "error": "⚠️ 没有找到 Uniswap v4 仓位。若你确实有仓位，"
                         "请在下方手动填入 tokenId。",
                "positions": []}
    return {"ok": True, "error": None, "positions": positions}


if __name__ == "__main__":
    main()

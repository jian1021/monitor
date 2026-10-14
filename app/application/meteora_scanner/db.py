"""
SQLite 数据访问层。

持久化内容（文档 9.）：
  - pools            池子基础信息
  - snapshots        历史快照
  - scan_status      扫描状态（断点续扫）
  - api_errors       API 错误日志
  - watchlist        重点池监控列表
  - alerts           报警发送记录（含去重键与失败原因）
  - tokens           代币元数据缓存
  - settings         页面可调整的筛选/权重等设置（JSON）

采用 WAL 模式 + 单连接 + 可重入锁，保证扫描后台线程与 Streamlit 主线程并发安全；
程序关闭后数据不丢失。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .models import PoolRecord, TokenInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS pools (
    pool_address        TEXT PRIMARY KEY,
    pool_type           TEXT NOT NULL,
    name                TEXT,
    token_x_mint        TEXT,
    token_x_name        TEXT,
    token_x_symbol      TEXT,
    token_x_market_cap  REAL,
    token_x_fdv         REAL,
    token_x_price       REAL,
    token_x_price_chg   REAL,
    token_x_mcap_type   TEXT,
    token_x_source      TEXT,
    token_y_mint        TEXT,
    token_y_name        TEXT,
    token_y_symbol      TEXT,
    token_y_market_cap  REAL,
    token_y_fdv         REAL,
    token_y_price       REAL,
    token_y_price_chg   REAL,
    token_y_mcap_type   TEXT,
    token_y_source      TEXT,
    tvl                 REAL,
    volume_24h          REAL,
    fees_24h            REAL,
    fee_rate_pct        REAL,
    fee_tvl_ratio_24h   REAL,
    price               REAL,
    price_change_24h    REAL,
    apr                 REAL,
    apy                 REAL,
    bin_step            INTEGER,
    created_at          INTEGER,
    data_source         TEXT,
    first_seen          TEXT,
    last_updated        TEXT,
    token_x_holders     INTEGER,
    token_x_top10_holders REAL,
    token_x_dev_holders REAL,
    token_x_freeze_auth TEXT,
    token_x_mint_auth   TEXT,
    open_positions      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_pools_type ON pools(pool_type);
CREATE INDEX IF NOT EXISTS idx_pools_tvl ON pools(tvl);
CREATE INDEX IF NOT EXISTS idx_pools_vol ON pools(volume_24h);

CREATE TABLE IF NOT EXISTS snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    pool_address        TEXT NOT NULL,
    pool_type           TEXT,
    ts                  TEXT NOT NULL,
    tvl                 REAL,
    volume_24h          REAL,
    fees_24h            REAL,
    fee_rate_pct        REAL,
    fee_tvl_ratio       REAL,
    token_x_market_cap  REAL,
    token_y_market_cap  REAL,
    price               REAL,
    price_change_24h    REAL,
    score               REAL,
    data_integrity      TEXT,
    signals             TEXT
);
CREATE INDEX IF NOT EXISTS idx_snap_pool ON snapshots(pool_address, ts);

CREATE TABLE IF NOT EXISTS scan_status (
    pool_type        TEXT PRIMARY KEY,
    pages_scanned    INTEGER DEFAULT 0,
    pools_discovered INTEGER DEFAULT 0,
    pools_processed  INTEGER DEFAULT 0,
    failures         INTEGER DEFAULT 0,
    last_position    INTEGER DEFAULT 0,
    total_available  INTEGER,
    last_scan_time   TEXT,
    state            TEXT,
    note             TEXT
);

CREATE TABLE IF NOT EXISTS api_errors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    pool_type   TEXT,
    endpoint    TEXT,
    attempt     INTEGER,
    message     TEXT
);
CREATE INDEX IF NOT EXISTS idx_err_ts ON api_errors(ts);

CREATE TABLE IF NOT EXISTS watchlist (
    pool_address TEXT PRIMARY KEY,
    pool_type    TEXT,
    added_at     TEXT,
    enabled      INTEGER DEFAULT 1,
    paused       INTEGER DEFAULT 0,
    note         TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    pool_address TEXT,
    pool_type    TEXT,
    event_type   TEXT,
    dedup_key    TEXT,
    message      TEXT,
    status       TEXT,
    error        TEXT
);
CREATE INDEX IF NOT EXISTS idx_alert_dedup ON alerts(dedup_key, ts);

CREATE TABLE IF NOT EXISTS tokens (
    mint             TEXT PRIMARY KEY,
    name             TEXT,
    symbol           TEXT,
    market_cap       REAL,
    fdv              REAL,
    price            REAL,
    price_change_24h REAL,
    market_cap_type  TEXT,
    source           TEXT,
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
            self._conn.executescript(SCHEMA)
            self._conn.commit()
            # 迁移：为现有数据库添加 bin_step 列（如果不存在）
            self._migrate_add_bin_step()
            # 迁移：为现有数据库添加持仓字段（如果不存在）
            self._migrate_add_holder_fields()

    # ------------------------------------------------------------------ 基础
    def _execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return cur.fetchall()

    def _migrate_add_bin_step(self) -> None:
        """为现有数据库添加 bin_step 列（如果不存在）"""
        try:
            # 检查列是否存在
            result = self._query("PRAGMA table_info(pools)")
            columns = [row["name"] for row in result]
            if "bin_step" not in columns:
                self._execute("ALTER TABLE pools ADD COLUMN bin_step INTEGER")
                self._execute("UPDATE pools SET bin_step = NULL WHERE bin_step IS NULL")
                print("数据库迁移：已添加 bin_step 列")
        except Exception as e:  # noqa: BLE001
            print(f"数据库迁移失败: {e}")

    def _migrate_add_holder_fields(self) -> None:
        """为现有数据库添加持仓相关字段（如果不存在）"""
        try:
            # 检查列是否存在
            result = self._query("PRAGMA table_info(pools)")
            columns = [row["name"] for row in result]
            holder_fields = ["token_x_holders", "token_x_top10_holders", "token_x_dev_holders", 
                           "token_x_freeze_auth", "token_x_mint_auth", "open_positions"]
            
            for field in holder_fields:
                if field not in columns:
                    # 根据字段类型创建不同的列
                    if field in ["token_x_holders", "open_positions"]:
                        self._execute(f"ALTER TABLE pools ADD COLUMN {field} INTEGER")
                    elif field in ["token_x_top10_holders", "token_x_dev_holders"]:
                        self._execute(f"ALTER TABLE pools ADD COLUMN {field} REAL")
                    else:  # freeze_auth, mint_auth
                        self._execute(f"ALTER TABLE pools ADD COLUMN {field} TEXT")
                    print(f"数据库迁移：已添加 {field} 列")
        except Exception as e:  # noqa: BLE001
            print(f"数据库迁移失败: {e}")

    # ------------------------------------------------------------------ pools
    def upsert_pool(self, rec: PoolRecord, *, ts: Optional[str] = None) -> bool:
        """写入/更新池子基础信息。返回 True 表示新发现。"""
        ts = ts or _now_iso()
        existing = self._query("SELECT first_seen FROM pools WHERE pool_address=?", (rec.pool_address,))
        is_new = not existing
        first_seen = existing[0]["first_seen"] if existing else (rec.first_seen or ts)

        tx, ty = rec.token_x, rec.token_y
        # Build INSERT statement dynamically
        columns = [
            "pool_address", "pool_type", "name",
            "token_x_mint", "token_x_name", "token_x_symbol", "token_x_market_cap", "token_x_fdv",
            "token_x_price", "token_x_price_chg", "token_x_mcap_type", "token_x_source",
            "token_y_mint", "token_y_name", "token_y_symbol", "token_y_market_cap", "token_y_fdv",
            "token_y_price", "token_y_price_chg", "token_y_mcap_type", "token_y_source",
            "tvl", "volume_24h", "fees_24h", "fee_rate_pct", "fee_tvl_ratio_24h", "price",
            "price_change_24h", "apr", "apy", "bin_step", "created_at", "data_source", "first_seen", "last_updated",
            "token_x_holders", "token_x_top10_holders", "token_x_dev_holders", "token_x_freeze_auth", "token_x_mint_auth", "open_positions"
        ]
        
        placeholders = ["?"] * len(columns)
        update_columns = [f"{col}=excluded.{col}" for col in columns if col != "pool_address"]
        
        sql = f"""
        INSERT INTO pools ({", ".join(columns)})
        VALUES ({", ".join(placeholders)})
        ON CONFLICT(pool_address) DO UPDATE SET
            {", ".join(update_columns)}
        """
        
        self._execute(
            sql,
            (
                rec.pool_address, rec.pool_type, rec.name,
                tx.mint, tx.name, tx.symbol, tx.market_cap, tx.fdv,
                tx.price, tx.price_change_24h, tx.market_cap_type, tx.source,
                ty.mint, ty.name, ty.symbol, ty.market_cap, ty.fdv,
                ty.price, ty.price_change_24h, ty.market_cap_type, ty.source,
                rec.tvl, rec.volume_24h, rec.fees_24h, rec.fee_rate_pct, rec.fee_tvl_ratio_24h,
                rec.price, rec.price_change_24h, rec.apr, rec.apy, rec.bin_step,
                rec.created_at, rec.data_source, first_seen, ts,
                tx.holders, tx.top10_holders, tx.dev_holders,
                "true" if tx.freeze_auth else "false" if tx.freeze_auth is not None else None,
                "true" if tx.mint_auth else "false" if tx.mint_auth is not None else None,
                rec.open_positions,
            ),
        )
        return is_new

    def load_pools(self, pool_types: Optional[list[str]] = None) -> list[dict]:
        sql = "SELECT * FROM pools"
        params: list[Any] = []
        if pool_types:
            placeholders = ",".join("?" for _ in pool_types)
            sql += f" WHERE pool_type IN ({placeholders})"
            params = list(pool_types)
        return [dict(r) for r in self._query(sql, params)]

    def get_pool(self, pool_address: str) -> Optional[dict]:
        rows = self._query("SELECT * FROM pools WHERE pool_address=?", (pool_address,))
        return dict(rows[0]) if rows else None

    def count_pools(self) -> dict[str, int]:
        rows = self._query("SELECT pool_type, COUNT(*) c FROM pools GROUP BY pool_type")
        return {r["pool_type"]: r["c"] for r in rows}

    # ----------------------------------------------------------------- tokens
    def upsert_tokens(self, tokens: dict[str, TokenInfo]) -> None:
        if not tokens:
            return
        ts = _now_iso()
        with self._lock:
            self._conn.executemany(
                """
                INSERT INTO tokens (mint, name, symbol, market_cap, fdv, price,
                                    price_change_24h, market_cap_type, source, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(mint) DO UPDATE SET
                    name=excluded.name, symbol=excluded.symbol, market_cap=excluded.market_cap,
                    fdv=excluded.fdv, price=excluded.price,
                    price_change_24h=excluded.price_change_24h,
                    market_cap_type=excluded.market_cap_type, source=excluded.source,
                    updated_at=excluded.updated_at
                """,
                [
                    (t.mint, t.name, t.symbol, t.market_cap, t.fdv, t.price,
                     t.price_change_24h, t.market_cap_type, t.source, ts)
                    for t in tokens.values() if t.mint
                ],
            )
            self._conn.commit()

    def get_tokens(self, mints: Iterable[str]) -> dict[str, TokenInfo]:
        mints = [m for m in dict.fromkeys(mints) if m]
        if not mints:
            return {}
        out: dict[str, TokenInfo] = {}
        # SQLite 变量上限约 999，分批查询
        for i in range(0, len(mints), 900):
            chunk = mints[i : i + 900]
            ph = ",".join("?" for _ in chunk)
            for r in self._query(f"SELECT * FROM tokens WHERE mint IN ({ph})", chunk):
                out[r["mint"]] = TokenInfo(
                    mint=r["mint"], name=r["name"] or "", symbol=r["symbol"] or "",
                    market_cap=r["market_cap"], fdv=r["fdv"], price=r["price"],
                    price_change_24h=r["price_change_24h"],
                    market_cap_type=r["market_cap_type"] or "", source=r["source"] or "",
                )
        return out

    # -------------------------------------------------------------- snapshots
    def add_snapshot(
        self, pool_address: str, *, pool_type: str = "", tvl: Optional[float] = None,
        volume_24h: Optional[float] = None, fees_24h: Optional[float] = None,
        fee_rate_pct: Optional[float] = None, fee_tvl_ratio: Optional[float] = None,
        token_x_market_cap: Optional[float] = None, token_y_market_cap: Optional[float] = None,
        price: Optional[float] = None, price_change_24h: Optional[float] = None,
        score: Optional[float] = None, data_integrity: str = "", signals: Optional[list] = None,
        ts: Optional[str] = None,
    ) -> None:
        self._execute(
            """
            INSERT INTO snapshots (pool_address, pool_type, ts, tvl, volume_24h, fees_24h,
                fee_rate_pct, fee_tvl_ratio, token_x_market_cap, token_y_market_cap,
                price, price_change_24h, score, data_integrity, signals)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                pool_address, pool_type, ts or _now_iso(), tvl, volume_24h, fees_24h,
                fee_rate_pct, fee_tvl_ratio, token_x_market_cap, token_y_market_cap,
                price, price_change_24h, score, data_integrity,
                json.dumps(signals or [], ensure_ascii=False),
            ),
        )

    def get_snapshots(self, pool_address: str, limit: int = 500) -> list[dict]:
        rows = self._query(
            "SELECT * FROM snapshots WHERE pool_address=? ORDER BY ts DESC LIMIT ?",
            (pool_address, limit),
        )
        return [dict(r) for r in rows]

    def latest_snapshot(self, pool_address: str) -> Optional[dict]:
        rows = self._query(
            "SELECT * FROM snapshots WHERE pool_address=? ORDER BY ts DESC LIMIT 1",
            (pool_address,),
        )
        return dict(rows[0]) if rows else None

    # ------------------------------------------------------------ scan_status
    def update_scan_status(self, pool_type: str, **fields: Any) -> None:
        row = self._query("SELECT pool_type FROM scan_status WHERE pool_type=?", (pool_type,))
        if not row:
            self._execute("INSERT INTO scan_status (pool_type) VALUES (?)", (pool_type,))
        cols = []
        params: list[Any] = []
        allowed = {
            "pages_scanned", "pools_discovered", "pools_processed", "failures",
            "last_position", "total_available", "last_scan_time", "state", "note",
        }
        for k, v in fields.items():
            if k in allowed:
                cols.append(f"{k}=?")
                params.append(v)
        if not cols:
            return
        params.append(pool_type)
        self._execute(f"UPDATE scan_status SET {', '.join(cols)} WHERE pool_type=?", params)

    def get_scan_status(self) -> list[dict]:
        return [dict(r) for r in self._query("SELECT * FROM scan_status ORDER BY pool_type")]

    # -------------------------------------------------------------- api_errors
    def log_api_error(self, *, pool_type: str, endpoint: str, attempt: int, message: str) -> None:
        self._execute(
            "INSERT INTO api_errors (ts, pool_type, endpoint, attempt, message) VALUES (?,?,?,?,?)",
            (_now_iso(), pool_type, endpoint, attempt, message[:1000]),
        )

    def get_api_errors(self, limit: int = 200) -> list[dict]:
        return [dict(r) for r in self._query(
            "SELECT * FROM api_errors ORDER BY id DESC LIMIT ?", (limit,))]

    def clear_api_errors(self) -> None:
        self._execute("DELETE FROM api_errors")

    # --------------------------------------------------------------- watchlist
    def add_watch(self, pool_address: str, pool_type: str = "", note: str = "") -> None:
        self._execute(
            """INSERT INTO watchlist (pool_address, pool_type, added_at, enabled, paused, note)
               VALUES (?,?,?,1,0,?)
               ON CONFLICT(pool_address) DO UPDATE SET enabled=1, paused=0""",
            (pool_address, pool_type, _now_iso(), note),
        )

    def remove_watch(self, pool_address: str) -> None:
        self._execute("DELETE FROM watchlist WHERE pool_address=?", (pool_address,))

    def set_watch_state(self, pool_address: str, *, enabled: Optional[bool] = None,
                        paused: Optional[bool] = None) -> None:
        if enabled is not None:
            self._execute("UPDATE watchlist SET enabled=? WHERE pool_address=?",
                          (1 if enabled else 0, pool_address))
        if paused is not None:
            self._execute("UPDATE watchlist SET paused=? WHERE pool_address=?",
                          (1 if paused else 0, pool_address))

    def list_watch(self) -> list[dict]:
        return [dict(r) for r in self._query("SELECT * FROM watchlist ORDER BY added_at DESC")]

    def is_watched(self, pool_address: str) -> bool:
        return bool(self._query("SELECT 1 FROM watchlist WHERE pool_address=?", (pool_address,)))

    # ------------------------------------------------------------------ alerts
    def log_alert(self, *, pool_address: str, pool_type: str, event_type: str,
                  dedup_key: str, message: str, status: str, error: str = "") -> None:
        self._execute(
            """INSERT INTO alerts (ts, pool_address, pool_type, event_type, dedup_key,
               message, status, error) VALUES (?,?,?,?,?,?,?,?)""",
            (_now_iso(), pool_address, pool_type, event_type, dedup_key, message, status, error[:500]),
        )

    def alert_sent_since(self, dedup_key: str, since_iso: str) -> bool:
        rows = self._query(
            "SELECT 1 FROM alerts WHERE dedup_key=? AND status='ok' AND ts>=? LIMIT 1",
            (dedup_key, since_iso),
        )
        return bool(rows)

    def get_alerts(self, limit: int = 200) -> list[dict]:
        return [dict(r) for r in self._query("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,))]

    # ---------------------------------------------------------------- settings
    def get_setting(self, key: str, default: Any = None) -> Any:
        rows = self._query("SELECT value FROM settings WHERE key=?", (key,))
        if not rows:
            return default
        try:
            return json.loads(rows[0]["value"])
        except (ValueError, TypeError):
            return default

    def set_setting(self, key: str, value: Any) -> None:
        self._execute(
            "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

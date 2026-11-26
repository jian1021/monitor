"""历史池子（watchlist）：本地 SQLite。"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass

SOURCES = ("manual", "query", "turso")


@dataclass
class Pool:
    id: int
    network: str
    address: str
    name: str | None
    note: str | None
    source: str
    last_used: str | None
    use_count: int
    added_at: str

    @property
    def label(self) -> str:
        return self.name or (self.address[:10] + "…")


def normalize_address(address: str) -> str:
    """EVM 地址不区分大小写，统一小写；Solana base58 区分大小写，原样保留。"""
    a = (address or "").strip()
    return a.lower() if a.lower().startswith("0x") else a


def looks_like_address(text: str) -> bool:
    t = (text or "").strip()
    return bool(re.fullmatch(r"0x[0-9a-fA-F]{40,64}", t) or re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", t))


class Watchlist:
    def __init__(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS pools (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                network TEXT NOT NULL,
                address TEXT NOT NULL,
                name TEXT,
                note TEXT,
                source TEXT NOT NULL DEFAULT 'manual',
                last_used TEXT,
                use_count INTEGER NOT NULL DEFAULT 0,
                added_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (network, address)
            )""")
        self.conn.commit()

    # ---- 写 ----
    def upsert(self, network: str, address: str, name: str | None = None, note: str | None = None,
               source: str = "manual", touch: bool = False) -> bool:
        """插入或更新；返回是否为新增。已存在时只补充空字段，不覆盖用户写的名称/备注；
        source 只在新增时写入（手工添加过的池子再被查询，来源仍是 manual）。"""
        address = normalize_address(address)
        network = network.strip().lower()
        existed = self.get(network, address) is not None
        self.conn.execute("""
            INSERT INTO pools (network, address, name, note, source) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(network, address) DO UPDATE SET
                name = COALESCE(pools.name, excluded.name),
                note = COALESCE(pools.note, excluded.note)
        """, (network, address, name or None, note or None, source))
        if touch:
            self.conn.execute("UPDATE pools SET last_used = datetime('now'), use_count = use_count + 1"
                              " WHERE network = ? AND address = ?", (network, address))
        self.conn.commit()
        return not existed

    def set_note(self, pool: Pool, note: str) -> None:
        self.conn.execute("UPDATE pools SET note = ? WHERE id = ?", (note or None, pool.id))
        self.conn.commit()

    def remove(self, pool: Pool) -> None:
        self.conn.execute("DELETE FROM pools WHERE id = ?", (pool.id,))
        self.conn.commit()

    # ---- 读 ----
    def get(self, network: str, address: str) -> Pool | None:
        row = self.conn.execute("SELECT * FROM pools WHERE network = ? AND address = ?",
                                (network.lower(), normalize_address(address))).fetchone()
        return Pool(**dict(row)) if row else None

    def list(self, source: str | None = None, tag: str | None = None) -> list[Pool]:
        """按最近使用倒序（没用过的排在后面，按加入时间倒序）。序号即此列表中的位置（从 1 开始）。"""
        sql, args = "SELECT * FROM pools WHERE 1=1", []
        if source:
            sql += " AND source = ?"
            args.append(source)
        if tag:
            # watchlist 表没有单独的标签列：--tag 匹配备注 / 名称中的关键字
            sql += " AND (LOWER(COALESCE(note,'')) LIKE ? OR LOWER(COALESCE(name,'')) LIKE ?)"
            args += [f"%{tag.lower()}%"] * 2
        sql += " ORDER BY last_used IS NULL, last_used DESC, added_at DESC, id DESC"
        return [Pool(**dict(r)) for r in self.conn.execute(sql, args).fetchall()]

    def match(self, selector: str) -> list[Pool]:
        """序号 / 完整地址 / 名称·地址·备注关键字（不区分大小写）。"""
        sel = (selector or "").strip()
        pools = self.list()
        if sel.isdigit() and len(sel) <= 6:
            i = int(sel)
            return [pools[i - 1]] if 1 <= i <= len(pools) else []
        norm = normalize_address(sel)
        exact = [p for p in pools if p.address == norm]
        if exact:
            return exact
        key = sel.lower()
        return [p for p in pools if key in (p.name or "").lower() or key in p.address.lower()
                or key in (p.note or "").lower()]


def parse_import_line(line: str, default_network: str) -> tuple[str, str] | None:
    """支持：`地址`、`地址 network`、`network 地址`、`network:地址`、逗号分隔；# 开头为注释。"""
    text = line.split("#", 1)[0].strip()
    if not text:
        return None
    parts = [p for p in re.split(r"[\s,;]+|(?<=[a-z]):(?=\S)", text) if p]
    addr = next((p for p in parts if looks_like_address(p)), None)
    if addr is None:
        return None
    others = [p for p in parts if p != addr]
    network = others[0].lower() if others else default_network
    return network, normalize_address(addr)

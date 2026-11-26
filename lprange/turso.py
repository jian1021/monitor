"""从 Turso（libSQL）导入监控池子到本地 watchlist。表结构由 config.toml 指定。"""

from __future__ import annotations

import logging
import re

from .config import Config, TursoSource, turso_credentials
from .watchlist import Watchlist, looks_like_address, normalize_address

log = logging.getLogger("lprange.turso")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class TursoError(Exception):
    pass


def _ident(name: str) -> str:
    if not _IDENT.match(name or ""):
        raise TursoError(f"配置里的表名/列名不合法：{name!r}")
    return f'"{name}"'


def build_query(src: TursoSource) -> str:
    cols = [_ident(src.address_column)]
    cols.append(_ident(src.name_column) if src.name_column else "NULL")
    cols.append(_ident(src.network_column) if src.network_column else "NULL")
    sql = f"SELECT {', '.join(cols)} FROM {_ident(src.table)}"
    if src.where:
        sql += f" WHERE {src.where}"
    return sql


def connect():
    url, token = turso_credentials()
    if not url or not token:
        raise TursoError("未配置 Turso 连接：请设置 TURSO_DATABASE_URL 和 TURSO_AUTH_TOKEN"
                         "（或 monitor 项目 .env 里的 LIBSQL_URL / LIBSQL_TOKEN）")
    try:
        from libsql_client import create_client_sync
    except ModuleNotFoundError as exc:
        raise TursoError("缺少 libsql-client：pip install libsql-client") from exc
    http_url = url.replace("libsql://", "https://")
    if not http_url.startswith(("https://", "http://")):
        http_url = f"https://{http_url}"
    try:
        return create_client_sync(url=http_url, auth_token=token)
    except Exception as exc:  # noqa: BLE001
        raise TursoError(f"连接 Turso 失败：{exc}") from exc


def fetch_rows(cfg: Config, client=None) -> tuple[list[dict], list[str]]:
    """读取所有配置的来源，返回 (待导入行, 跳过/错误说明)。单个来源失败不影响其他来源。"""
    if not cfg.turso_sources:
        raise TursoError("config.toml 里没有配置 [turso] 来源表")
    own = client is None
    client = client or connect()
    rows, notes = [], []
    seen: set[tuple[str, str]] = set()
    try:
        for src in cfg.turso_sources:
            label = src.label or src.table
            try:
                rs = client.execute(build_query(src))
            except TursoError:
                raise
            except Exception as exc:  # noqa: BLE001
                notes.append(f"[{label}] 查询失败：{exc}")
                continue
            for r in rs.rows:
                addr, name, net = (list(r) + [None, None, None])[:3]
                addr = normalize_address(str(addr or ""))
                if not looks_like_address(addr):
                    notes.append(f"[{label}] 跳过非地址值：{str(addr)[:24]}")
                    continue
                raw_net = str(net or src.network).strip().lower()
                network = cfg.network_map.get(raw_net, raw_net)
                key = (network, addr)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({"network": network, "address": addr, "name": name, "from": label})
    finally:
        if own:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
    return rows, notes


def import_turso(cfg: Config, watchlist: Watchlist, dry_run: bool = False,
                 client=None) -> tuple[list[dict], int, list[str]]:
    rows, notes = fetch_rows(cfg, client)
    added = 0
    if not dry_run:
        for row in rows:
            if watchlist.upsert(row["network"], row["address"], name=row["name"], source="turso"):
                added += 1
    return rows, added, notes

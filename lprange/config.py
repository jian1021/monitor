"""配置：config.toml + 环境变量（密钥只走环境变量）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
DEFAULT_CONFIG = PACKAGE_DIR / "config.toml"

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # pragma: no cover - 3.10 需要 tomli
    try:
        import tomli as _toml  # type: ignore[no-redef]
    except ModuleNotFoundError:
        _toml = None


@dataclass
class TursoSource:
    table: str
    address_column: str
    name_column: str | None = None
    network_column: str | None = None
    network: str = "solana"
    where: str | None = None
    label: str | None = None


@dataclass
class Config:
    db_path: str
    network_map: dict[str, str] = field(default_factory=dict)
    turso_sources: list[TursoSource] = field(default_factory=list)
    sync_turso_before_all: bool = False


def _load_env_file() -> None:
    """复用 monitor 项目根目录的 .env（如果装了 python-dotenv）；不覆盖已存在的环境变量。"""
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        return
    env = REPO_ROOT / ".env"
    if env.exists():
        load_dotenv(env, override=False)


def load_config(path: str | None = None, db_override: str | None = None) -> Config:
    _load_env_file()
    cfg_path = Path(path or os.getenv("LPRANGE_CONFIG") or DEFAULT_CONFIG)
    raw: dict = {}
    if cfg_path.exists():
        if _toml is None:
            raise RuntimeError("读取 config.toml 需要 Python 3.11+，或先 pip install tomli")
        raw = _toml.loads(cfg_path.read_text(encoding="utf-8"))
    elif path:
        raise FileNotFoundError(f"找不到配置文件：{cfg_path}")

    db = db_override or os.getenv("LPRANGE_DB") or (raw.get("paths") or {}).get("db") \
        or str(PACKAGE_DIR / "data" / "watchlist.db")
    db = str((cfg_path.parent / db).resolve()) if not os.path.isabs(os.path.expanduser(db)) \
        else os.path.expanduser(db)

    turso = raw.get("turso") or {}
    sources_raw = list(turso.get("sources") or [])
    if turso.get("table"):                       # 也支持单表写法：[turso] table = ...
        sources_raw.insert(0, {k: v for k, v in turso.items() if k not in ("sources", "network_map")})
    sources = [TursoSource(
        table=s["table"], address_column=s["address_column"],
        name_column=s.get("name_column"), network_column=s.get("network_column"),
        network=s.get("network", "solana"), where=s.get("where"), label=s.get("label"),
    ) for s in sources_raw]
    return Config(db_path=db, network_map={str(k).lower(): str(v) for k, v in
                                           (turso.get("network_map") or {}).items()},
                  turso_sources=sources, sync_turso_before_all=bool(turso.get("sync_before_all")))


def turso_credentials() -> tuple[str, str]:
    """TURSO_DATABASE_URL / TURSO_AUTH_TOKEN；未设置时回退 monitor 项目的 LIBSQL_URL / LIBSQL_TOKEN。"""
    url = os.getenv("TURSO_DATABASE_URL") or os.getenv("LIBSQL_URL") or ""
    token = os.getenv("TURSO_AUTH_TOKEN") or os.getenv("LIBSQL_TOKEN") or ""
    return url.strip(), token.strip()

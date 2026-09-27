from app.infrastructure.db.client import get_db_client

MEME_PARAM_DEFAULTS = {
    "min_market_cap": 1_000_000.0,
    "min_tvl_usd": 100_000.0,
    "min_bin_step": 100,
    "min_base_fee_pct": 2.0,
    "rsi_max": 10.0,
}


def ensure_meme_config_schema():
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute("""
            CREATE TABLE IF NOT EXISTS meme_underval_params (
                param TEXT PRIMARY KEY,
                value REAL NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        return True
    except Exception as e:
        print(f"❌ 创建 meme_underval_params 表失败: {e}")
        return False
    finally:
        client.close()


def get_meme_underval_params() -> dict:
    base = dict(MEME_PARAM_DEFAULTS)
    ensure_meme_config_schema()
    client = get_db_client()
    if not client:
        return base
    try:
        rs = client.execute("SELECT param, value FROM meme_underval_params")
        for row in rs.rows:
            name = str(row[0])
            if name not in base:
                continue
            raw = row[1]
            if not isinstance(raw, (int, float, str)):
                continue
            try:
                base[name] = float(raw)
            except (TypeError, ValueError):
                continue
        return base
    except Exception as e:
        print(f"❌ 读取低估参数失败: {e}")
        return base
    finally:
        client.close()


def update_meme_underval_params(params: dict) -> bool:
    known = {k: v for k, v in dict(params or {}).items() if k in MEME_PARAM_DEFAULTS}
    if not known:
        return False
    ensure_meme_config_schema()
    client = get_db_client()
    if not client:
        return False
    try:
        values = []
        for name, raw in known.items():
            try:
                values.append((name, float(raw)))
            except (TypeError, ValueError):
                continue
        if not values:
            return False
        for name, number in values:
            client.execute(
                "INSERT INTO meme_underval_params (param, value, updated_at)"
                " VALUES (?, ?, datetime('now'))"
                " ON CONFLICT(param) DO UPDATE SET"
                " value = ?, updated_at = datetime('now')",
                [name, number, number],
            )
        return True
    except Exception as e:
        print(f"❌ 保存低估参数失败: {e}")
        return False
    finally:
        client.close()

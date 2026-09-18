import pandas as pd
import streamlit as st

from db import get_db_client
import db as _db
import dex_client as _dex

ASSET_TYPE_MAP = {
    "crypto": "🪙 加密货币",
    "meteora": "☄️ Meteora 池",
    "bond": "📈 可转债",
    "etf": "📊 ETF",
    "token": "🔗 链上代币",
}

TOKEN_CHAINS = ["sol", "bsc", "base", "eth", "robinhood", "arc", "stable"]
CHAIN_LABELS = {
    "sol": "Solana (SOL)", "bsc": "BNB Chain (BSC)", "base": "Base",
    "eth": "Ethereum (ETH)", "robinhood": "Robinhood (RH)",
    "arc": "ARC", "stable": "Stable",
}


def looks_like_address(text: str) -> bool:
    t = (text or "").strip()
    if t.startswith("0x") and len(t) == 42:
        return True
    return len(t) >= 32


def ensure_asset_schema():
    """调用 db.ensure_asset_schema；旧版 db 模块（Streamlit 会缓存已导入的模块）缺失时跳过."""
    fn = getattr(_db, "ensure_asset_schema", None)
    return fn() if callable(fn) else False


def stale_module_names() -> list:
    """本模块依赖但当前运行环境里缺失的新函数（说明部署未更新）."""
    missing = []
    if not callable(getattr(_db, "ensure_asset_schema", None)):
        missing.append("db.ensure_asset_schema")
    if not callable(getattr(_dex, "search_tokens", None)):
        missing.append("dex_client.search_tokens")
    return missing


def token_candidates(chain: str, text: str) -> list:
    """地址直通为唯一候选（会补查名称/价格/市值）；名称则返回搜索结果列表。"""
    t = (text or "").strip()
    if looks_like_address(t):
        lookup = getattr(_dex, "lookup_token", None)
        if callable(lookup):
            return [lookup(chain, t)]
        return [{"address": t, "symbol": None, "name": None,
                 "price": None, "market_cap": None, "liquidity": None, "found": False}]
    fn = getattr(_dex, "search_tokens", None)
    return fn(chain, t) if callable(fn) else []


def format_candidate(c: dict) -> str:
    """候选展示文案：符号 · 名称 · 价格 · 市值 · 流动性 · 地址前缀."""
    parts = [f"{c.get('symbol') or '?'} · {c.get('name') or '未知'}"]
    if c.get("price") is not None:
        parts.append(f"价 {c['price']:.10g}")
    if c.get("market_cap"):
        parts.append(f"市值 {c['market_cap']:,.0f}")
    if c.get("liquidity"):
        parts.append(f"流动性 {c['liquidity']:,.0f}")
    parts.append(f"{c['address'][:10]}…")
    return " · ".join(parts)


def fetch_all_assets():
    """读取所有标的资产"""
    client = get_db_client()
    if not client:
        return pd.DataFrame()
    try:
        try:
            rs = client.execute(
                "SELECT id, asset_type, code, name, enabled, created_at, chain, timeframe, "
                "alarm_active, last_alert_at"
                " FROM asset_config ORDER BY id ASC"
            )
            has_chain = True
            has_timeframe = True
        except Exception:
            rs = client.execute(
                "SELECT id, asset_type, code, name, enabled, created_at, chain, timeframe"
                " FROM asset_config ORDER BY id ASC"
            )
            has_chain = True
            has_timeframe = True
        data = []
        for row in rs.rows:
            data.append({
                "id": row[0],
                "asset_type": row[1],
                "code": row[2],
                "name": row[3],
                "enabled": bool(row[4]),
                "created_at": row[5],
                "chain": (row[6] if has_chain and len(row) > 6 else None) or "sol",
                "timeframe": (row[7] if has_timeframe and len(row) > 7 else None) or "1W",
                "alarm_active": bool(row[8]) if len(row) > 8 else False,
                "last_alert_at": row[9] if len(row) > 9 else None,
            })
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"❌ 读取标的列表失败: {e}")
        return pd.DataFrame()
    finally:
        client.close()


def update_asset_status(asset_id: int, enabled: bool):
    """更新单个标的的启用状态"""
    client = get_db_client()
    if not client:
        return False
    try:
        status_val = 1 if enabled else 0
        client.execute(
            "UPDATE asset_config SET enabled = ? WHERE id = ?",
            [status_val, asset_id]
        )
        return True
    except Exception as e:
        st.error(f"❌ 更新状态失败 (ID: {asset_id}): {e}")
        return False
    finally:
        client.close()


def batch_update_status_by_type(asset_type: str, enabled: bool):
    """按分类一键批量修改启用/禁用状态"""
    client = get_db_client()
    if not client:
        return False
    try:
        status_val = 1 if enabled else 0
        client.execute(
            "UPDATE asset_config SET enabled = ? WHERE asset_type = ?",
            [status_val, asset_type]
        )
        return True
    except Exception as e:
        st.error(f"❌ 批量更新分类 [{asset_type}] 失败: {e}")
        return False
    finally:
        client.close()


def add_new_asset(asset_type: str, code: str, name: str, chain: str = None, timeframe: str = None):
    """新增标的"""
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute(
            "INSERT INTO asset_config (asset_type, code, name, enabled, chain, timeframe)"
            " VALUES (?, ?, ?, 1, ?, ?)",
            [asset_type, code.strip(), name.strip() or code.strip(), chain or "sol", timeframe or "1W"]
        )
        return True
    except Exception as e:
        st.error(f"❌ 添加标的失败: {e}")
        return False
    finally:
        client.close()


def delete_asset(asset_id: int):
    """删除标的"""
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute("DELETE FROM asset_config WHERE id = ?", [asset_id])
        return True
    except Exception as e:
        st.error(f"❌ 删除标的失败: {e}")
        return False
    finally:
        client.close()

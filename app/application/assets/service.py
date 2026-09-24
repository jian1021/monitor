"""资产配置应用服务：标的增删改查与候选格式化，不含 UI / streamlit。"""

import pandas as pd

from app.infrastructure.db.client import get_db_client


def looks_like_address(text: str) -> bool:
    t = (text or "").strip()
    if t.startswith("0x") and len(t) == 42:
        return True
    return len(t) >= 32


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
            rs = client.execute('SELECT id, asset_type, code, name, enabled, created_at, chain, timeframe, alarm_active, last_alert_at FROM asset_config ORDER BY id ASC')
            has_chain = True
            has_timeframe = True
        except Exception:
            rs = client.execute('SELECT id, asset_type, code, name, enabled, created_at, chain, timeframe FROM asset_config ORDER BY id ASC')
            has_chain = True
            has_timeframe = True
        data = []
        for row in rs.rows:
            data.append({'id': row[0], 'asset_type': row[1], 'code': row[2], 'name': row[3], 'enabled': bool(row[4]), 'created_at': row[5], 'chain': (row[6] if has_chain and len(row) > 6 else None) or 'sol', 'timeframe': (row[7] if has_timeframe and len(row) > 7 else None) or '1d', 'alarm_active': bool(row[8]) if len(row) > 8 else False, 'last_alert_at': row[9] if len(row) > 9 else None})
        return pd.DataFrame(data)
    finally:
        client.close()


def update_asset_status(asset_id: int, enabled: bool):
    """更新单个标的的启用状态"""
    client = get_db_client()
    if not client:
        return False
    try:
        status_val = 1 if enabled else 0
        client.execute('UPDATE asset_config SET enabled = ? WHERE id = ?', [status_val, asset_id])
        return True
    finally:
        client.close()


def batch_update_status_by_type(asset_type: str, enabled: bool):
    """按分类一键批量修改启用/禁用状态"""
    client = get_db_client()
    if not client:
        return False
    try:
        status_val = 1 if enabled else 0
        client.execute('UPDATE asset_config SET enabled = ? WHERE asset_type = ?', [status_val, asset_type])
        return True
    finally:
        client.close()


def add_new_asset(asset_type: str, code: str, name: str, chain: str=None, timeframe: str=None):
    """新增标的"""
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute('INSERT INTO asset_config (asset_type, code, name, enabled, chain, timeframe) VALUES (?, ?, ?, 1, ?, ?)', [asset_type, code.strip(), name.strip() or code.strip(), chain or 'sol', timeframe or '1W'])
        return True
    finally:
        client.close()


def delete_asset(asset_id: int):
    """删除标的"""
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute('DELETE FROM asset_config WHERE id = ?', [asset_id])
        return True
    finally:
        client.close()

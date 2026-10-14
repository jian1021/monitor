"""标的配置 UI 适配层：常量、报错展示与部署兼容检查；业务逻辑在 app/application/assets/service.py。"""

import pandas as pd
import streamlit as st

from app.application.assets import service as _svc
from app.application.assets.service import format_candidate, looks_like_address
from app.infrastructure.db.assets import ensure_asset_schema as _db_ensure_asset_schema
from app.infrastructure.market_data.dexscreener import lookup_token, search_tokens

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


def ensure_asset_schema():
    """确保标的表存在（实现在 app/infrastructure/db/assets.py）."""
    return _db_ensure_asset_schema()


def stale_module_names() -> list:
    """旧版部署兼容检查：实现已全部改为直接导入，缺函数会在启动时抛 ImportError，
    不再静默缺失。保留接口兼容，恒返回空列表。"""
    return []


def token_candidates(chain: str, text: str) -> list:
    """地址直通为唯一候选（会补查名称/价格/市值）；名称则返回搜索结果列表。"""
    t = (text or "").strip()
    if looks_like_address(t):
        return [lookup_token(chain, t)]
    return search_tokens(chain, t)


def fetch_all_assets():
    """读取所有标的资产"""
    try:
        return _svc.fetch_all_assets()
    except Exception as e:
        st.error(f"❌ 读取标的列表失败: {e}")
        return pd.DataFrame()


def update_asset_status(asset_id: int, enabled: bool):
    """更新单个标的的启用状态"""
    try:
        return _svc.update_asset_status(asset_id, enabled)
    except Exception as e:
        st.error(f"❌ 更新状态失败 (ID: {asset_id}): {e}")
        return False


def batch_update_status_by_type(asset_type: str, enabled: bool):
    """按分类一键批量修改启用/禁用状态"""
    try:
        return _svc.batch_update_status_by_type(asset_type, enabled)
    except Exception as e:
        st.error(f"❌ 批量更新分类 [{asset_type}] 失败: {e}")
        return False


def add_new_asset(asset_type: str, code: str, name: str, chain: str = None, timeframe: str = None):
    """新增标的"""
    try:
        return _svc.add_new_asset(asset_type, code, name, chain, timeframe)
    except Exception as e:
        st.error(f"❌ 添加标的失败: {e}")
        return False


def delete_asset(asset_id: int):
    """删除标的"""
    try:
        return _svc.delete_asset(asset_id)
    except Exception as e:
        st.error(f"❌ 删除标的失败: {e}")
        return False

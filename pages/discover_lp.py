import streamlit as st
import os
import json
import pandas as pd
from libsql_client import create_client_sync
from db import get_db_client


# 1. 动态获取所有已激活的策略列表（用于下拉选择框）
@st.cache_data(ttl=60, show_spinner=False)
def fetch_active_configs():
    client = get_db_client()
    if not client:
        return {}
    
    try:
        res = client.execute("SELECT config_id, config_name, filter_params FROM sys_lp_config WHERE is_active = 1")
        # 返回结构: {'meme_breakout_2m_v1': {'name': '2M市值大池子策略', 'params': {...}}}
        configs = {}
        for row in res.rows:
            configs[row[0]] = {
                "name": row[1],
                "params": json.loads(row[2])
            }
        return configs
    except Exception as e:
        st.error(f"加载策略配置失败: {e}")
        return {}

# 2. 根据选中的策略动态查询符合条件的代币 (加 10秒 缓存)
@st.cache_data(ttl=10, show_spinner=False)
def fetch_tokens_by_config(config_id: str, params: dict):
    client = get_db_client()
    if not client or not params:
        return []

    # libSQL / Turso 绑参查询
    query = """
    SELECT 
        address,
        symbol,
        market_cap,
        liquidity,
        total_fee_sol,
        created_at,
        ROUND((julianday('now') - julianday(created_at)) * 24, 1) AS age_hours
    FROM 
        token_market_data
    WHERE 
        market_cap >= ?
        AND liquidity >= ?
        AND total_fee_sol >= ?
        AND julianday(created_at) <= julianday('now', '-' || ? || ' hours')
        AND julianday(created_at) >= julianday('now', '-' || ? || ' hours')
    ORDER BY 
        created_at DESC;
    """

    try:
        res = client.execute(query, [
            params['min_market_cap'],
            params['min_liquidity'],
            params['min_fee_sol'],
            params['min_age_hours'],
            params['max_age_hours']
        ])
        
        columns = [col[0] for col in res.columns]
        data = [dict(zip(columns, row)) for row in res.rows]
        return data
    except Exception as e:
        st.error(f"数据查询失败: {e}")
        return []

# ----------------- Streamlit UI 展现 -----------------
st.set_page_config(page_title="Meme 币监控平台", layout="wide")
st.title("🚀 Meme 币暴涨机会监控平台")

# 加载数据库中所有的激活策略
all_configs = fetch_active_configs()

if not all_configs:
    st.warning("⚠️ 数据库中没有可用的激活策略配置！")
else:
    # 侧边栏/顶部控制区域
    col1, col2 = st.columns([3, 1])
    
    with col1:
        # 下拉选择框：展示“策略中文名 (config_id)”
        selected_config_id = st.selectbox(
            "🎯 选择筛选策略：",
            options=list(all_configs.keys()),
            format_func=lambda x: f"📌 {all_configs[x]['name']} ({x})"
        )
        
    with col2:
        st.write(" ") # 垂直对齐调整
        st.write(" ")
        if st.button("🔄 刷新数据", use_container_width=True):
            st.cache_data.clear()

    # 获得当前选中策略的参数配置
    current_config = all_configs[selected_config_id]
    p = current_config['params']

    # 展开栏：展示当前策略的具体过滤条件
    with st.expander("🛠️ 查看当前策略参数阀值", expanded=False):
        st.json(p)

    # 执行查询
    tokens = fetch_tokens_by_config(selected_config_id, p)

    if tokens:
        st.success(f"🎯 查找到 **{len(tokens)}** 个符合【{current_config['name']}】的潜在标的")
        df = pd.DataFrame(tokens)
        
        # 为 CA 地址自动附带 GMGN 盘面跳转链接
        df["gmgn_link"] = df["address"].apply(lambda ca: f"https://gmgn.ai/sol/token/{ca}")
        
        st.dataframe(
            df,
            column_config={
                "symbol": "代币名",
                "address": "CA 合约地址",
                "gmgn_link": st.column_config.LinkColumn("GMGN 盘面", display_text="🔗 开盘"),
                "market_cap": st.column_config.NumberColumn("市值 (USD)", format="$%d"),
                "liquidity": st.column_config.NumberColumn("流动池 (USD)", format="$%d"),
                "total_fee_sol": st.column_config.NumberColumn("手续费", format="%.1f SOL"),
                "age_hours": st.column_config.NumberColumn("开盘时长", format="%.1f 小时"),
                "created_at": "创建时间 (UTC)"
            },
            column_order=["symbol", "gmgn_link", "address", "market_cap", "liquidity", "total_fee_sol", "age_hours", "created_at"],
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info(f"💡 策略【{current_config['name']}】下暂无符合条件的标的 "
                f"(市值≥${p['min_market_cap']:,}, 池子≥${p['min_liquidity']:,}, 费>{p['min_fee_sol']} SOL, Age {p['min_age_hours']}-{p['max_age_hours']}h)")
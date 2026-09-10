# -*- coding: utf-8 -*-
"""pages/distribution.py — 池内代币分布图 (Uniswap V3 LP 流动性深度)

输入 Uniswap V3 池子合约地址，按 Tick 还原该池在各价格区间的代币数量，
以双轴柱状图展示 (绿 = 现价上方 Token0 / 红色 = 现价下方 Token1)。

数据源: The Graph 去中心化网络上的 Uniswap V3 子图 (需 THE_GRAPH_API_KEY,
免费申请: https://thegraph.com/studio/apikeys/)，替代已下线的 hosted service。
"""
import math
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams['font.sans-serif'] = ['PingFang SC', 'Hiragino Sans GB', 'STHeiti', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False

import matplotlib.pyplot as plt
import pandas as pd
import requests
import streamlit as st

from config import THE_GRAPH_API_KEY

# ================= 1. 配置参数 =================
# 链 -> (显示名, The Graph 子图 ID)
CHAIN_SUBGRAPHS = {
    "eth": ("Ethereum", "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV"),
    "arbitrum": ("Arbitrum One", "HyW7A86UEdYVt5b9Lrw8W2F98yKecerHKutZTRbSCX27"),
    "base": ("Base", "85Vk687SdpcvbzkFBu222YMtuKjy5iNJqcNjypkDUUw2"),
    "polygon": ("Polygon", "3hCPRGf4z88VC5rsBKU5AA9FBBq5nF3jbKJG7VZCbhjm"),
    "bsc": ("BSC", "GcKPSgHoY42xNYVAkSPDhXSzi6aJDRQSKqBSXezL47gV"),
}

GATEWAY_URL = "https://gateway.thegraph.com/api/{key}/subgraphs/id/{sid}"
MAX_TICKS = 5000


def subgraph_url(chain: str) -> str:
    return GATEWAY_URL.format(key=THE_GRAPH_API_KEY, sid=CHAIN_SUBGRAPHS[chain][1])


# ================= 2. 获取池子基本信息与 Ticks =================
@st.cache_data(ttl=300, show_spinner=False)
def fetch_pool_info(url: str, pool_address: str):
    """查询池子当前状态 (token0, token1, 当前 tick, 精度)"""
    query = f"""
    {{
      pool(id: "{pool_address}") {{
        token0 {{ symbol decimals }}
        token1 {{ symbol decimals }}
        tick
        liquidity
        sqrtPrice
      }}
    }}
    """
    res = requests.post(url, json={"query": query}, timeout=20).json()
    if res.get("errors") or not res.get("data") or not res["data"].get("pool"):
        msg = res.get("errors") or "池子不存在，请检查地址与链是否正确"
        return None, msg
    return res["data"]["pool"], None


@st.cache_data(ttl=300, show_spinner=False)
def fetch_all_ticks(url: str, pool_address: str):
    """分页获取所有初始化的 Ticks (包含 liquidityNet / liquidityGross)"""
    ticks = []
    skip = 0
    while skip < MAX_TICKS:
        query = f"""
        {{
          ticks(where: {{ poolAddress: "{pool_address}" }}, first: 1000, skip: {skip}, orderBy: tickIdx, orderDirection: asc) {{
            tickIdx
            liquidityNet
            liquidityGross
          }}
        }}
        """
        r = requests.post(url, json={"query": query}, timeout=30).json()
        if r.get("errors") or not r.get("data"):
            break
        fetched = r["data"].get("ticks") or []
        if not fetched:
            break
        ticks.extend(fetched)
        skip += len(fetched)
        if len(fetched) < 1000:
            break
    return ticks


# ================= 3. 计算每个 Tick 对应的真实代币数量 =================
def process_liquidity_depth(pool_info, ticks):
    current_tick = int(pool_info["tick"])
    dec0 = int(pool_info["token0"]["decimals"])
    dec1 = int(pool_info["token1"]["decimals"])

    current_liquidity = 0
    data = []

    # 遍历 Tick，根据集中流动性公式还原各个价格区间内的物理代币存量
    for i in range(len(ticks) - 1):
        t_low = int(ticks[i]["tickIdx"])
        t_high = int(ticks[i + 1]["tickIdx"])

        current_liquidity += int(ticks[i]["liquidityNet"])
        if current_liquidity <= 0:
            continue

        p_low = 1.0001 ** t_low
        p_high = 1.0001 ** t_high

        # 中点价格 (Token1 per Token0，需做 decimal 精度矫正)
        price_raw = 1.0001 ** ((t_low + t_high) / 2)
        price_adjusted = price_raw * (10 ** (dec0 - dec1))

        sqrt_p_low = math.sqrt(p_low)
        sqrt_p_high = math.sqrt(p_high)

        if t_high <= current_tick:
            # 当前价格完全高于该区间：里面全都是 Token1
            amount0 = 0
            amount1 = current_liquidity * (sqrt_p_high - sqrt_p_low) / (10 ** dec1)
        elif t_low >= current_tick:
            # 当前价格完全低于该区间：里面全都是 Token0
            amount0 = current_liquidity * (1 / sqrt_p_low - 1 / sqrt_p_high) / (10 ** dec0)
            amount1 = 0
        else:
            # 当前价格处于该区间中间：双边都有
            sqrt_p_curr = math.sqrt(1.0001 ** current_tick)
            amount0 = current_liquidity * (1 / sqrt_p_curr - 1 / sqrt_p_high) / (10 ** dec0)
            amount1 = current_liquidity * (sqrt_p_curr - sqrt_p_low) / (10 ** dec1)

        data.append({
            "price": price_adjusted,
            "tick_low": t_low,
            "tick_high": t_high,
            "amount0": amount0,
            "amount1": amount1,
            "is_above": t_low >= current_tick,
        })

    return pd.DataFrame(data), current_tick


# ================= 4. 绘制柱状图 =================
def build_depth_chart(df, pool_info, current_tick, range_pct=50.0):
    sym0 = pool_info["token0"]["symbol"]
    sym1 = pool_info["token1"]["symbol"]

    current_price = (1.0001 ** current_tick) * (
        10 ** (int(pool_info["token0"]["decimals"]) - int(pool_info["token1"]["decimals"]))
    )

    # 筛选当前价格上下一定比例的范围，避免极端离群值导致图表缩成一条线
    price_min = current_price * (1 - range_pct / 100.0)
    price_max = current_price * (1 + range_pct / 100.0)
    df_filtered = df[(df["price"] >= price_min) & (df["price"] <= price_max)]
    if df_filtered.empty:
        df_filtered = df

    fig, ax1 = plt.subplots(figsize=(13, 6))

    # 当前价格上方：只有 Token0 的代币绝对数量 (绿柱)
    df_above = df_filtered[df_filtered["is_above"]]
    ax1.bar(df_above["price"], df_above["amount0"], width=(price_max - price_min) / 100,
            color="green", alpha=0.6, label=f"{sym0} 数量 (卖出深度)")
    ax1.set_ylabel(f"{sym0} 数量", color="green")

    # 当前价格下方：只有 Token1 的代币绝对数量 (红/褐色柱)
    ax2 = ax1.twinx()
    df_below = df_filtered[~df_filtered["is_above"]]
    ax2.bar(df_below["price"], df_below["amount1"], width=(price_max - price_min) / 100,
            color="red", alpha=0.6, label=f"{sym1} 数量 (买入深度)")
    ax2.set_ylabel(f"{sym1} 数量", color="red")

    # 标注当前价格垂直线
    ax1.axvline(x=current_price, color="black", linestyle="--", linewidth=2,
                label=f"现价 ({current_price:.6g})")

    plt.title(f"{sym0}/{sym1} 池内代币分布")
    ax1.set_xlabel(f"价格 ({sym1} per {sym0})")

    # 合并图例
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper right")

    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    return fig, current_price


# ================= 5. Streamlit 界面 =================
st.set_page_config(page_title="代币分布图", page_icon="📊", layout="wide")
st.title("📊 池内代币分布图")
st.caption(
    "输入 Uniswap V3 池子合约地址，按 Tick 还原各价格区间的代币数量分布。"
    "绿柱 = 现价上方区间内的 Token0，红柱 = 现价下方区间内的 Token1。"
    "数据源：The Graph 去中心化网络上的 Uniswap V3 子图。"
)

with st.sidebar:
    st.header("参数")
    chain = st.selectbox(
        "所属公链",
        options=list(CHAIN_SUBGRAPHS.keys()),
        format_func=lambda c: CHAIN_SUBGRAPHS[c][0],
    )
    pool_address = st.text_input(
        "池子地址",
        placeholder="0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
    )
    range_pct = st.slider("价格显示范围 (%)", min_value=5, max_value=200, value=50, step=5)
    run = st.button("生成分布图", type="primary", use_container_width=True)

if not run:
    st.info("👈 左侧输入 Uniswap V3 池子合约地址，点击「生成分布图」")
    st.stop()

addr = (pool_address or "").strip()
if not addr:
    st.error("请输入池子合约地址")
    st.stop()
if not THE_GRAPH_API_KEY:
    st.error(
        "未配置 THE_GRAPH_API_KEY。请先在 https://thegraph.com/studio/apikeys/ "
        "免费申请，然后写入 .env 的 THE_GRAPH_API_KEY 后重启应用。"
    )
    st.stop()

addr = addr.lower()
url = subgraph_url(chain)

with st.spinner("正在从 The Graph 拉取池子数据..."):
    pool_info, err = fetch_pool_info(url, addr)
    if pool_info is None:
        st.error(f"获取池子数据失败: {err}")
        st.stop()

    ticks = fetch_all_ticks(url, addr)
    if not ticks:
        st.warning("该池子没有已初始化的 Tick 区间（流动性可能已撤出，或地址/链不对）。")
        st.stop()
    if len(ticks) >= MAX_TICKS:
        st.warning(f"Tick 数量达到分页上限 {MAX_TICKS}，图表可能只展示了部分区间。")

df, current_tick = process_liquidity_depth(pool_info, ticks)
if df.empty:
    st.warning("未能还原出有效的代币存量分布，请调整显示范围或确认池子状态。")
    st.stop()

fig, current_price = build_depth_chart(df, pool_info, current_tick, float(range_pct))
st.pyplot(fig)

# 汇总指标
sym0 = pool_info["token0"]["symbol"]
sym1 = pool_info["token1"]["symbol"]
total0 = float(df["amount0"].to_numpy().sum())
total1 = float(df["amount1"].to_numpy().sum())
m1, m2, m3 = st.columns(3)
m1.metric(f"{sym0} 总存量", f"{total0:,.4g}")
m2.metric(f"{sym1} 总存量", f"{total1:,.4g}")
m3.metric("当前价格", f"{current_price:.6g} {sym1}/{sym0}")
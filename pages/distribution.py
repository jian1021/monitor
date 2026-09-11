# -*- coding: utf-8 -*-
"""pages/distribution.py — 池内代币分布图 (V3 LP 流动性深度)

输入 Uniswap V3 风格池子合约地址，按 Tick 还原该池在各价格区间的代币数量，
以双轴柱状图展示 (绿 = 现价上方 Token0 / 红色 = 现价下方 Token1)。

数据源: 直接读取链上池子合约的公开 RPC (无需任何 API Key)。
通过 slot0()/tickSpacing()/liquidity()/ticks() 等合约方法实时取数，
替代已下线的 The Graph hosted service 与需要 API Key 的 gateway。
"""
import math
import os
import sys
import time

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

# ================= 1. 配置参数 =================
# 链 -> (显示名, 公开 RPC 地址)。全部无需 API Key。
CHAIN_RPCS = {
    "eth": ("Ethereum", "https://ethereum.publicnode.com"),
    "arbitrum": ("Arbitrum One", "https://arb1.arbitrum.io/rpc"),
    "base": ("Base", "https://base.publicnode.com"),
    "polygon": ("Polygon", "https://polygon-bor-rpc.publicnode.com"),
    "bsc": ("BSC", "https://bsc-rpc.publicnode.com"),
}

# 不同 RPC 节点的限流策略不同: (每批 eth_call 数, 批次间隔秒数)。
# Arbitrum 官方 RPC 对大批次会直接拒绝，需小批量 + 稍长间隔。
RPC_PACING = {
    "https://arb1.arbitrum.io/rpc": (25, 0.3),
}
DEFAULT_BATCH, DEFAULT_GAP = 100, 0.15

# Uniswap V3 全局有效 tick 范围
MIN_TICK, MAX_TICK = -887272, 887272
# 单次扫描的候选 Tick 上限，防止对超大范围池子的 RPC 请求过多
MAX_TICKS = 5000
TICK_SELECTOR = "0xf30dba93"  # ticks(int24)

# 池子合约与 ERC20 的 method selector
SEL = {
    "slot0": "0x3850c7bd",        # sqrtPriceX96, tick, observationIndex, ...
    "token0": "0x0dfe1681",
    "token1": "0xd21220a7",
    "fee": "0xddca3f43",
    "tickSpacing": "0xd0c93a7c",
    "liquidity": "0x1a686502",
    "decimals": "0x313ce567",
    "symbol": "0x95d89b41",
}


def rpc_url(chain: str) -> str:
    return CHAIN_RPCS[chain][1]


# ================= 2. 链上数据读取 (JSON-RPC 批量 + 限流重试) =================
def rpc_batch(url: str, calls):
    """把一批 (id, to, data) eth_call 组装成 JSON-RPC 批量请求并发起。

    对 429 限流与超时自动退避重试，批次大小与间隔按节点限流策略配置。
    返回 {id: hex_result}；返回 0x 表示调用回退 (地址不是池子合约)。
    """
    batch, gap = RPC_PACING.get(url, (DEFAULT_BATCH, DEFAULT_GAP))
    results = {}
    for i in range(0, len(calls), batch):
        chunk = calls[i:i + batch]
        payload = [
            {"jsonrpc": "2.0", "id": c[0], "method": "eth_call",
             "params": [{"to": c[1], "data": c[2]}, "latest"]}
            for c in chunk
        ]
        for attempt in range(5):
            try:
                r = requests.post(url, json=payload, timeout=30,
                                  headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
                if r.status_code == 429:
                    time.sleep(min(2 ** attempt, 15) + 0.5)
                    continue
                r.raise_for_status()
                for item in r.json():
                    results[item["id"]] = item.get("result")
                break
            except (requests.RequestException, ValueError):
                if attempt == 4:
                    raise
                time.sleep(min(2 ** attempt, 15))
        time.sleep(gap)
    return results


def parse_int24(hexdata: str) -> int:
    """从 ABI 32 字节 word 的最后 3 字节解析 int24 (slot0 中的当前 tick)。"""
    v = int(hexdata[124:130], 16)
    return v - (1 << 24) if v >= (1 << 23) else v


def parse_int128(hexdata: str) -> int:
    """解析 ticks(int24) 返回的第二个 word (int128 liquidityNet)。

    注意必须先按 128 位掩码再符号扩展，直接对 64 位 hex 取补码会出错。
    """
    v = int(hexdata[66:130], 16) & ((1 << 128) - 1)
    return v - (1 << 128) if v >= (1 << 127) else v


@st.cache_data(ttl=300, show_spinner=False)
def fetch_pool_info(rpc_url: str, pool_address: str):
    """读取池子合约状态: token0/token1 符号精度、当前 tick、活跃流动性、fee、tickSpacing。"""
    addr = pool_address.lower()
    res = rpc_batch(rpc_url, [
        ("slot0", addr, SEL["slot0"]),
        ("token0", addr, SEL["token0"]),
        ("token1", addr, SEL["token1"]),
        ("fee", addr, SEL["fee"]),
        ("spacing", addr, SEL["tickSpacing"]),
        ("liquidity", addr, SEL["liquidity"]),
    ])
    slot0 = res.get("slot0")
    t0hex, t1hex = res.get("token0"), res.get("token1")
    if not slot0 or slot0 == "0x" or not t0hex or t0hex == "0x" or not t1hex or t1hex == "0x":
        return None, "地址不是有效的 V3 池合约 (读取失败或已回退)，请检查地址与所选链是否匹配"

    t0 = "0x" + t0hex[26:66].lower()
    t1 = "0x" + t1hex[26:66].lower()
    meta = rpc_batch(rpc_url, [
        ("d0", t0, SEL["decimals"]), ("s0", t0, SEL["symbol"]),
        ("d1", t1, SEL["decimals"]), ("s1", t1, SEL["symbol"]),
    ])

    def token_meta(data, sym, tok_addr):
        dec = int(data, 16) if data and data != "0x" else 18
        symbol = ""
        if sym and sym != "0x":
            body = sym[2:] if sym.startswith("0x") else sym
            # 动态 string 的 ABI 编码: 最后 32 字节是左对齐的字符串数据
            word = body[-64:].rjust(64, "0")
            try:
                symbol = bytes.fromhex(word).decode("utf-8", "replace").rstrip("\x00").strip()
            except Exception:
                symbol = ""
        if not symbol:
            symbol = tok_addr[2:10]
        return {"symbol": symbol, "decimals": dec}

    pool_info = {
        "id": addr,
        "token0": token_meta(meta.get("d0"), meta.get("s0"), t0),
        "token1": token_meta(meta.get("d1"), meta.get("s1"), t1),
        "tick": parse_int24(slot0),
        "liquidity": str(int(res["liquidity"], 16)) if res.get("liquidity") else "0",
        "sqrtPrice": slot0[2:66],
        "fee": int(res["fee"], 16) if res.get("fee") else 0,
        "tickSpacing": int(res["spacing"], 16) if res.get("spacing") else 1,
    }
    return pool_info, None


@st.cache_data(ttl=300, show_spinner=False)
def fetch_all_ticks(rpc_url: str, pool_address: str, current_tick: int, tick_spacing: int, range_pct: float):
    """在当前价格附近的窗口内扫描所有初始化的 Ticks (按 tickSpacing 对齐)。

    返回 (ticks, truncated)。truncated=True 表示候选数量达到上限、窗口被截断。
    """
    half_ticks = int(math.log(1 + range_pct / 100.0) / math.log(1.0001) * 1.5)
    half_ticks = max(half_ticks, tick_spacing * 500)  # 保证扫描范围至少覆盖显示范围的 1.5 倍
    lo = max(MIN_TICK, (current_tick - half_ticks) // tick_spacing * tick_spacing)
    hi = min(MAX_TICK, (current_tick + half_ticks) // tick_spacing * tick_spacing + tick_spacing)
    cands = list(range(lo, hi + 1, tick_spacing))

    truncated = len(cands) > MAX_TICKS
    if truncated:
        cands = cands[:MAX_TICKS]

    ticks = []
    addr = pool_address.lower()
    batch, _ = RPC_PACING.get(rpc_url, (DEFAULT_BATCH, DEFAULT_GAP))
    for i in range(0, len(cands), batch):
        chunk = cands[i:i + batch]
        res = rpc_batch(rpc_url, [
            (f"t{t}", addr, TICK_SELECTOR + format(t % (1 << 256), "064x"))
            for t in chunk
        ])
        for t in chunk:
            r = res.get(f"t{t}")
            if r and r != "0x" and int(r[-1], 16):  # 最后一个字节是 initialized 标志
                ticks.append({
                    "tickIdx": str(t),
                    "liquidityNet": str(parse_int128(r)),
                    "liquidityGross": str(int(r[2:66], 16) & ((1 << 128) - 1)),
                })
    ticks.sort(key=lambda x: int(x["tickIdx"]))
    return ticks, truncated


# ================= 3. 计算每个 Tick 区间对应的真实代币数量 =================
def process_liquidity_depth(pool_info, ticks):
    """以池子合约当前活跃流动性 (liquidity()) 为锚点，向上下两个方向还原代币存量。

    现价下方: 每越过一个 tick 减去其 liquidityNet；现价上方: 每越过一个 tick 加上。
    相比旧的"从 0 开始累加全部 ticks"的做法，锚定真实流动性后不再依赖全局 tick 列表。
    """
    current_tick = int(pool_info["tick"])
    dec0 = int(pool_info["token0"]["decimals"])
    dec1 = int(pool_info["token1"]["decimals"])
    base_liquidity = int(pool_info.get("liquidity") or 0)

    data = []

    # —— 现价下方的区间 (只含 Token1) ——
    Lw = base_liquidity
    prev = current_tick
    below = [t for t in ticks if int(t["tickIdx"]) < current_tick]
    for t in reversed(below):
        tl = int(t["tickIdx"])
        p_l, p_h = 1.0001 ** tl, 1.0001 ** prev
        sp_l, sp_h = math.sqrt(p_l), math.sqrt(p_h)
        if Lw > 0:
            data.append({
                "price": 1.0001 ** ((tl + prev) / 2) * 10 ** (dec0 - dec1),
                "tick_low": tl, "tick_high": prev,
                "amount0": 0.0,
                "amount1": Lw * (sp_h - sp_l) / (10 ** dec1),
                "is_above": False,
            })
        Lw -= int(t["liquidityNet"])
        prev = tl

    # —— 现价上方的区间 (只含 Token0) ——
    Lw = base_liquidity
    prev = current_tick
    above = [t for t in ticks if int(t["tickIdx"]) > current_tick]
    for t in above:
        th = int(t["tickIdx"])
        p_l, p_h = 1.0001 ** prev, 1.0001 ** th
        sp_l, sp_h = math.sqrt(p_l), math.sqrt(p_h)
        if Lw > 0:
            data.append({
                "price": 1.0001 ** ((prev + th) / 2) * 10 ** (dec0 - dec1),
                "tick_low": prev, "tick_high": th,
                "amount0": Lw * (1 / sp_l - 1 / sp_h) / (10 ** dec0),
                "amount1": 0.0,
                "is_above": True,
            })
        Lw += int(t["liquidityNet"])
        prev = th

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
    "输入 V3 池子合约地址，按 Tick 还原各价格区间的代币数量分布。"
    "绿柱 = 现价上方区间内的 Token0，红柱 = 现价下方区间内的 Token1。"
    "数据源：直接读取链上池子合约 (公开 RPC，无需 API Key)。"
)

with st.sidebar:
    st.header("参数")
    chain = st.selectbox(
        "所属公链",
        options=list(CHAIN_RPCS.keys()),
        format_func=lambda c: CHAIN_RPCS[c][0],
    )
    pool_address = st.text_input(
        "池子地址",
        placeholder="0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
    )
    range_pct = st.slider("价格显示范围 (%)", min_value=5, max_value=200, value=50, step=5)
    run = st.button("生成分布图", type="primary", use_container_width=True)

if not run:
    st.info("👈 左侧输入 V3 池子合约地址，点击「生成分布图」")
    st.stop()

addr = (pool_address or "").strip()
if not addr:
    st.error("请输入池子合约地址")
    st.stop()
if not addr.lower().startswith("0x") or len(addr) != 42:
    st.error("地址格式不正确，请输入 0x 开头的 42 位合约地址")
    st.stop()

url = rpc_url(chain)

with st.spinner("正在从链上读取池子数据..."):
    pool_info, err = fetch_pool_info(url, addr)
    if pool_info is None:
        st.error(f"获取池子数据失败: {err}")
        st.stop()

    current_tick = int(pool_info["tick"])
    tick_spacing = int(pool_info["tickSpacing"])
    ticks, truncated = fetch_all_ticks(url, addr, current_tick, tick_spacing, float(range_pct))
    if not ticks:
        st.warning("扫描窗口内没有已初始化的 Tick 区间（流动性可能已撤出，或地址/链不对）。")
        st.stop()
    if truncated:
        st.warning(f"候选 Tick 数量达到上限 {MAX_TICKS}，仅扫描了当前价格附近的部分区间。")

# 检查扫描窗口是否完整覆盖了池子流动性 (残差显著则提示总量可能不完整)
base_liq = int(pool_info["liquidity"] or 0)
if base_liq > 0:
    net_below = sum(int(t["liquidityNet"]) for t in ticks if int(t["tickIdx"]) < current_tick)
    net_above = sum(int(t["liquidityNet"]) for t in ticks if int(t["tickIdx"]) > current_tick)
    miss_below = base_liq - net_below
    miss_above = base_liq + net_above
    if miss_below > base_liq * 0.02 or miss_above > base_liq * 0.02:
        st.warning("池子流动性超出当前扫描窗口，图表总量指标可能只覆盖了窗口内部分。")

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
m1.metric(f"{sym0} 窗口内总存量", f"{total0:,.4g}")
m2.metric(f"{sym1} 窗口内总存量", f"{total1:,.4g}")
m3.metric("当前价格", f"{current_price:.6g} {sym1}/{sym0}")
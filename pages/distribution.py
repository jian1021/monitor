# -*- coding: utf-8 -*-
"""pages/distribution.py — 池内代币分布图 (V3 / V4 LP 流动性深度)

输入 Uniswap V3 风格池子合约地址 (或 V4 的 poolId)，按 Tick 还原该池在各价格区间的
代币数量，以双轴柱状图展示 (绿 = 现价上方 Token0 / 红色 = 现价下方 Token1)。

数据源: 直接读取链上合约的公开 RPC (无需任何 API Key)。
- V3: slot0()/tickSpacing()/liquidity()/ticks() 等池子合约方法。
- V4: 通过每条链的 StateView 透镜合约读取 getSlot0()/getLiquidity()/getTickLiquidity()，
  支持直接粘贴 poolId 自动从 Initialize 事件恢复 PoolKey；
  若 RPC 不支持历史日志查询 (如部分公共节点) 可手动填写 PoolKey 兜底。
"""
import math
import os
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from Crypto.Hash import keccak as _pycrypto_keccak
except Exception:  # V4 poolId 计算需要；不影响 V3 功能
    _pycrypto_keccak = None

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
    "robinhood": ("Robinhood", "https://rpc.mainnet.chain.robinhood.com"),
}

# 不同 RPC 节点的限流策略不同: (每批 eth_call 数, 批次间隔秒数)。
# Arbitrum / Robinhood 官方 RPC 对大批次会直接拒绝，需小批量 + 稍长间隔。
RPC_PACING = {
    "https://arb1.arbitrum.io/rpc": (25, 0.3),
    "https://rpc.mainnet.chain.robinhood.com": (25, 0.3),
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

# ================= Uniswap V4 配置 =================
# 每条链的 V4 PoolManager (池子状态的权威来源) 与 StateView 透镜合约地址。
# StateView 是对 V4 单例存储的只读封装: getSlot0(bytes32) / getLiquidity(bytes32) /
# getTickLiquidity(bytes32,int24)，返回布局与 V3 slot0()/liquidity()/ticks() 前段一致。
V4_POOL_MANAGERS = {
    "eth": "0x000000000004444c5dc75cB358380D2e3dE08A90",
    "arbitrum": "0x360E68faCcca8cA495c1B759Fd9EEe466db9FB32",
    "base": "0x498581fF718922c3f8e6A244956aF099B2652b2b",
    "polygon": "0x67366782805870060151383F4BbFF9daB53e5cD6",
    "bsc": "0x28e2Ea090877bF75740558f6BFB36A5ffeE9e9dF",
    "robinhood": "0x8366a39CC670B4001A1121B8F6A443A643e40951",
}

V4_STATE_VIEWS = {
    "eth": "0x7fFE42C4a5DEeA5b0feC41C94C136Cf115597227",
    "arbitrum": "0x76Fd297e2D437cd7f76d50F01AfE6160f86e9990",
    "base": "0xA3c0c9b65baD0b08107Aa264b0f3dB444b867A71",
    "polygon": "0x5eA1bD7974c8A611cBAB0bDCAFcB1D9CC9b3BA5a",
    "bsc": "0xd13Dd3D6E93f276FAfc9Db9E6BB47C1180aeE0c4",
    "robinhood": "0xF3334192D15450CdD385c8B70e03f9A6bD9E673b",
}

# StateView 方法 selector (6 条链 V4 部署镜像字节码，selector 一致)
V4_SEL = {
    "getSlot0": "0xc815641c",        # (uint160 sqrtPriceX96, int24 tick, uint16 protocolFee, uint8 unlocked)
    "getLiquidity": "0xfa6793d5",    # uint128
    "getTickLiquidity": "0xcaedab54",  # (uint128 liquidityGross, int128 liquidityNet)
}

# PoolManager.Initialize(bytes32 indexed poolId, address indexed currency0,
#   address indexed currency1, uint24 fee, int24 tickSpacing, address hooks,
#   uint160 sqrtPriceX96, int24 tick) 事件的 topic0
V4_INIT_TOPIC = "0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438"

# 每条链 PoolManager 部署区块的保守下界，用于 eth_getLogs 恢复 PoolKey 的 fromBlock。
# 只有 RPC 支持历史日志的链 (Arbitrum/Robinhood) 会真正成功；公共节点会返回存档错误，
# 此时 UI 引导用户手动填写 PoolKey。
V4_LOG_FROM = {
    "eth": 21600000,
    "arbitrum": 496203995,  # 实测部署区块
    "base": 21300000,
    "polygon": 67000000,
    "bsc": 44000000,
    "robinhood": 60342448,
}

V4_ZERO_ADDR = "0x0000000000000000000000000000000000000000"


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


# ================= 2.5 Uniswap V4: poolId / PoolKey / Initialize 事件 =================
def keccak256(data_hex: str) -> str:
    """计算 keccak-256 (以太坊哈希)。data_hex 为无 0x 前缀的十六进制字符串。"""
    if _pycrypto_keccak is None:
        raise RuntimeError("缺少 pycryptodome 依赖，无法计算 V4 poolId。请安装 requirements.txt 后重试。")
    h = _pycrypto_keccak.new(digest_bits=256)
    h.update(bytes.fromhex(data_hex))
    return "0x" + h.hexdigest()


def compute_pool_id(key: dict) -> str:
    """按 V4 规范计算 poolId = keccak256(abi.encode(PoolKey))。

    PoolKey 的 5 个字段均为定长 32 字节 word，按序拼接后整体哈希:
    currency0, currency1 (address 左补零), fee (uint24), tickSpacing (int24),
    hooks (address 左补零)。校验过与链上真实 Initialize 事件哈希一致。
    """
    def word(v):
        return format(int(v) & ((1 << 256) - 1), "064x")

    c0 = key["currency0"].lower()[2:].rjust(64, "0")
    c1 = key["currency1"].lower()[2:].rjust(64, "0")
    hooks = key.get("hooks") or V4_ZERO_ADDR
    hooks = hooks.lower()[2:].rjust(64, "0")
    return keccak256(c0 + c1 + word(key["fee"]) + word(key["tickSpacing"]) + hooks)


def rpc_get_logs(url: str, address: str, topics, from_block: int):
    """查询指定合约/主题的历史日志 (单次 eth_getLogs + 限流重试)。

    返回 (logs, None) 或 (None, 错误信息)。公共节点常拒绝存档区间查询，
    此时返回错误信息而非抛出异常，由调用方决定走 PoolKey 手动兜底。
    """
    for attempt in range(4):
        try:
            r = requests.post(url, json=[{
                "jsonrpc": "2.0", "id": 1, "method": "eth_getLogs",
                "params": [{
                    "address": address,
                    "topics": topics,
                    "fromBlock": hex(from_block),
                    "toBlock": "latest",
                }],
            }], timeout=120, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            if r.status_code == 429:
                time.sleep(min(2 ** attempt, 15) + 0.5)
                continue
            if r.status_code == 403:
                return None, "RPC 拒绝历史日志查询: HTTP 403 (该节点不提供存档/日志查询服务)"
            r.raise_for_status()
            resp = r.json()[0]
            if "error" in resp:
                return None, f"RPC 拒绝历史日志查询: {resp['error'].get('message', '')[:100]}"
            return resp.get("result") or [], None
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 403:
                return None, "RPC 拒绝历史日志查询: HTTP 403 (该节点不提供存档/日志查询服务)"
            if attempt == 3:
                return None, f"历史日志查询失败: {str(e)[:100]}"
            time.sleep(min(2 ** attempt, 15))
        except (requests.RequestException, ValueError) as e:
            if attempt == 3:
                return None, f"历史日志查询失败: {str(e)[:100]}"
            time.sleep(min(2 ** attempt, 15))
    return None, "历史日志查询重试耗尽"


def fetch_v4_pool_key_via_logs(rpc_url: str, chain: str, pool_id: str):
    """从 PoolManager 的 Initialize 事件里恢复出该 poolId 的完整 PoolKey。

    topics = [事件签名, poolId]，事件 data 里依次是
    fee, tickSpacing, hooks, sqrtPriceX96, tick。返回 (key, None) 或 (None, 错误)。
    """
    logs, err = rpc_get_logs(rpc_url, V4_POOL_MANAGERS[chain],
                             [V4_INIT_TOPIC, pool_id.lower()], V4_LOG_FROM.get(chain, 0))
    if err:
        return None, err
    if not logs:
        return None, "该链上找不到此 poolId 的 Initialize 事件 (poolId 与链不匹配，或池子已不存在)"

    log = logs[0]
    key = {
        "currency0": "0x" + log["topics"][2][26:66].lower(),
        "currency1": "0x" + log["topics"][3][26:66].lower(),
        "fee": int(log["data"][2:66], 16),
        "tickSpacing": int(log["data"][66:130], 16),
        "hooks": "0x" + log["data"][130:194][24:64].lower(),
    }
    verified = compute_pool_id(key)
    if verified != pool_id.lower():
        return None, f"事件解码校验失败 (计算的 poolId {verified} != 输入)"
    key["poolId"] = pool_id.lower()
    return key, None


def resolve_v4_key(rpc_url: str, chain: str, pool_id_input: str):
    """把用户输入解析成完整 PoolKey。

    1) 有 poolId → 尝试从 Initialize 事件自动恢复；
    2) 事件查询被 RPC 拒绝 (存档限制) → 返回特殊标记 BLOCKED，UI 引导手动填写。
    返回 (key, None) / (None, "BLOCKED:'...'") / (None, 错误信息)。
    """
    pid = (pool_id_input or "").strip().lower()
    if pid:
        key, err = fetch_v4_pool_key_via_logs(rpc_url, chain, pid)
        if key:
            return key, None
        if err and "拒绝历史日志" in err:
            return None, f"BLOCKED:{err}"
        return None, err
    return None, "请输入 poolId"


def build_v4_key_from_form(form: dict):
    """用手动填写的 PoolKey 字段构造成 key，并校验计算的 poolId (若同时给了 poolId)。"""
    c0 = (form.get("currency0") or "").strip().lower()
    c1 = (form.get("currency1") or "").strip().lower()
    if len(c0) != 42 or len(c1) != 42:
        return None, "currency0 / currency1 格式不正确，应为 0x 开头的 42 位地址"
    hooks = (form.get("hooks") or "").strip().lower() or V4_ZERO_ADDR
    if len(hooks) != 42:
        return None, "hooks 地址格式不正确"
    if int(c0, 16) > int(c1, 16):
        c0, c1 = c1, c0
    key = {
        "currency0": c0,
        "currency1": c1,
        "fee": int(float(form.get("fee") or 3000)),
        "tickSpacing": int(float(form.get("tickSpacing") or 200)),
        "hooks": hooks,
    }
    key["poolId"] = compute_pool_id(key)
    return key, None


# ================= 2.6 Uniswap V4: 池子状态与 Tick 扫描 =================
@st.cache_data(ttl=300, show_spinner=False)
def fetch_v4_pool_state(rpc_url: str, chain: str, key: dict):
    """通过 StateView 读取 V4 池子状态: 当前 sqrtPrice/tick、活跃流动性、token 元信息。

    key 由调用方解析 (事件恢复或手动表单)，token 地址取自 key 的 currency0/currency1。
    不存在的池子 getSlot0 返回空，这里把它统一成明确错误。返回 (pool_info, None) 或 (None, 错误)。
    """
    pool_id = key["poolId"].lower()
    sv = V4_STATE_VIEWS[chain]
    res = rpc_batch(rpc_url, [
        ("slot0", sv, V4_SEL["getSlot0"] + pool_id[2:]),
        ("liq", sv, V4_SEL["getLiquidity"] + pool_id[2:]),
    ])
    slot0 = res.get("slot0")
    if not slot0 or slot0 == "0x" or int(slot0[2:66], 16) == 0:
        return None, "StateView 读取失败: poolId 在所选链上不存在 (或不是有效的 V4 池子)"

    meta = rpc_batch(rpc_url, [
        ("d0", key["currency0"], SEL["decimals"]), ("s0", key["currency0"], SEL["symbol"]),
        ("d1", key["currency1"], SEL["decimals"]), ("s1", key["currency1"], SEL["symbol"]),
    ])

    def token_meta(data, sym, tok_addr):
        dec = int(data, 16) if data and data != "0x" else 18
        symbol = ""
        if sym and sym != "0x":
            body = sym[2:] if sym.startswith("0x") else sym
            word = body[-64:].rjust(64, "0")
            try:
                symbol = bytes.fromhex(word).decode("utf-8", "replace").rstrip("\x00").strip()
            except Exception:
                symbol = ""
        if not symbol:
            symbol = tok_addr[2:10]
        return {"symbol": symbol, "decimals": dec}

    pool_info = {
        "id": pool_id,
        "token0": token_meta(meta.get("d0"), meta.get("s0"), key["currency0"]),
        "token1": token_meta(meta.get("d1"), meta.get("s1"), key["currency1"]),
        "tick": parse_int24(slot0),
        "liquidity": str(int(res["liq"], 16)) if res.get("liq") else "0",
        "sqrtPrice": slot0[2:66],
        "fee": key["fee"],
        "tickSpacing": key["tickSpacing"],
    }
    return pool_info, None


@st.cache_data(ttl=300, show_spinner=False)
def fetch_all_ticks_v4(rpc_url: str, chain: str, pool_id: str, current_tick: int,
                       tick_spacing: int, range_pct: float):
    """V4 版 Tick 扫描: 走 StateView.getTickLiquidity(poolId, tick)。

    与 V3 相同思路，但调用目标从池子合约换成 StateView 透镜合约，
    tick 参数前需要先拼接 bytes32 poolId。返回 (ticks, truncated)。
    """
    half_ticks = int(math.log(1 + range_pct / 100.0) / math.log(1.0001) * 1.5)
    half_ticks = max(half_ticks, tick_spacing * 500)
    lo = max(MIN_TICK, (current_tick - half_ticks) // tick_spacing * tick_spacing)
    hi = min(MAX_TICK, (current_tick + half_ticks) // tick_spacing * tick_spacing + tick_spacing)
    cands = list(range(lo, hi + 1, tick_spacing))

    truncated = len(cands) > MAX_TICKS
    if truncated:
        cands = cands[:MAX_TICKS]

    ticks = []
    sv = V4_STATE_VIEWS[chain]
    batch, _ = RPC_PACING.get(rpc_url, (DEFAULT_BATCH, DEFAULT_GAP))
    for i in range(0, len(cands), batch):
        chunk = cands[i:i + batch]
        res = rpc_batch(rpc_url, [
            (f"t{t}", sv,
             V4_SEL["getTickLiquidity"] + pool_id[2:] + format(t % (1 << 256), "064x"))
            for t in chunk
        ])
        for t in chunk:
            r = res.get(f"t{t}")
            if r and r != "0x":
                gross = int(r[2:66], 16) & ((1 << 128) - 1)
                net = parse_int128(r)
                if gross or net:  # V4 没有独立的 initialized 标志，靠流动性质非零判断
                    ticks.append({
                        "tickIdx": str(t),
                        "liquidityNet": str(net),
                        "liquidityGross": str(gross),
                    })
    ticks.sort(key=lambda x: int(x["tickIdx"]))
    return ticks, truncated


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
    "输入 Uniswap V3 池子合约地址或 V4 poolId，按 Tick 还原各价格区间的代币数量分布。"
    "绿柱 = 现价上方区间内的 Token0，红柱 = 现价下方区间内的 Token1。"
    "数据源：直接读取链上合约 (公开 RPC，无需 API Key)。"
)

with st.sidebar:
    st.header("参数")
    protocol = st.radio(
        "协议版本",
        options=["V3", "V4"],
        horizontal=True,
        help="V3 输入池子合约地址；V4 输入 poolId (可从 Uniswap 池子页面复制)，支持本地计算 PoolKey 验证",
    )
    chain = st.selectbox(
        "所属公链",
        options=list(CHAIN_RPCS.keys()),
        format_func=lambda c: CHAIN_RPCS[c][0],
    )
    pool_address = ""
    pool_id = ""
    c0, c1, hooks = "", "", ""
    fee, tick_spacing = 3000, 200
    if protocol == "V3":
        pool_address = st.text_input(
            "池子地址",
            placeholder="0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
        )
    else:
        pool_id = st.text_input(
            "池子 ID (poolId)",
            placeholder="0x" + "a" * 64,
            help="V4 poolId。输入后自动从链上 Initialize 事件恢复 PoolKey；若 RPC 不支持历史日志，请展开下方表单手动填写",
        )
        with st.expander("无法自动解析？手动填写 PoolKey"):
            st.caption("PoolKey = (currency0, currency1, fee, tickSpacing, hooks)")
            c0 = st.text_input("currency0 (Token0 地址)", placeholder="0x...")
            c1 = st.text_input("currency1 (Token1 地址)", placeholder="0x...")
            fee = st.number_input("fee", min_value=0, value=3000, step=100)
            tick_spacing = st.number_input("tickSpacing", min_value=1, value=200, step=10)
            hooks = st.text_input("hooks (可选，默认空)", placeholder="0x0000...0000")
    range_pct = st.slider("价格显示范围 (%)", min_value=5, max_value=200, value=50, step=5)
    run = st.button("生成分布图", type="primary", use_container_width=True)

if not run:
    st.info("👈 左侧选择协议版本并填写池子信息，点击「生成分布图」")
    st.stop()

url = rpc_url(chain)

with st.spinner("正在从链上读取池子数据..."):
    if protocol == "V3":
        addr = (pool_address or "").strip()
        if not addr:
            st.error("请输入池子合约地址")
            st.stop()
        if not addr.lower().startswith("0x") or len(addr) != 42:
            st.error("地址格式不正确，请输入 0x 开头的 42 位合约地址")
            st.stop()

        pool_info, err = fetch_pool_info(url, addr)
        if pool_info is None:
            st.error(f"获取池子数据失败: {err}")
            st.stop()

        current_tick = int(pool_info["tick"])
        tick_spacing = int(pool_info["tickSpacing"])
        ticks, truncated = fetch_all_ticks(url, addr, current_tick, tick_spacing, float(range_pct))
    else:
        pid = (pool_id or "").strip().lower()
        if pid and (not pid.startswith("0x") or len(pid) != 66):
            st.error("poolId 格式不正确，应为 0x 开头的 64 位十六进制")
            st.stop()

        # 优先: 有 poolId 就从链上 Initialize 事件自动恢复 PoolKey
        key, err = None, None
        blocked = False
        if pid:
            key, err = resolve_v4_key(url, chain, pid)
            blocked = bool(err and err.startswith("BLOCKED"))
            if not key and not blocked:
                st.error(f"poolId 解析失败: {err}")
                st.stop()
            if blocked and not (c0.strip() and c1.strip()):
                assert err is not None
                st.error(f"此 RPC 不支持历史日志查询，无法自动解析 PoolKey。\n{err[len('BLOCKED:'):]}。\n请展开左侧「手动填写 PoolKey」表单补充 currency0/currency1/fee/tickSpacing。")
                st.stop()

        # 兜底: 手动填写 PoolKey (可同时在 RPC 被限制时作为备选)
        if key is None:
            manual_key, err = build_v4_key_from_form({
                "currency0": c0, "currency1": c1,
                "fee": fee, "tickSpacing": tick_spacing, "hooks": hooks,
            })
            if err:
                st.error(f"PoolKey 校验失败: {err}")
                st.stop()
            assert manual_key is not None
            if pid and manual_key["poolId"] != pid:
                st.error(f"手动 PoolKey 计算出的 poolId 与输入不一致\n{manual_key['poolId']}\n≠\n{pid}\n请检查 currency0/currency1/fee/tickSpacing/hooks 是否填写正确。")
                st.stop()
            with st.expander(f"已由手动 PoolKey 计算 poolId"):
                st.code(manual_key["poolId"])
            key = manual_key

        assert key is not None
        pool_info, err = fetch_v4_pool_state(url, chain, key)
        if pool_info is None:
            st.error(f"获取 V4 池子数据失败: {err}")
            st.stop()

        current_tick = int(pool_info["tick"])
        tick_spacing = int(pool_info["tickSpacing"])
        ticks, truncated = fetch_all_ticks_v4(url, chain, key["poolId"], current_tick, tick_spacing, float(range_pct))

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
"""Robinhood Chain / Uniswap v4 链上读取（LP 告警）。"""

import time

from keccak_pure import keccak256

from app.core.settings import ROBINHOOD_RPC
from app.infrastructure.market_data.http import HEADERS, requests


EVM_RPC = ROBINHOOD_RPC or "https://rpc.mainnet.chain.robinhood.com"


V4_POSITION_MANAGER = "0x58daec3116aae6d93017baaea7749052e8a04fa7"


V4_STATE_VIEW = "0xf3334192d15450cdd385c8b70e03f9a6bd9e673b"


USDG_ADDRESS = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"


TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


TICK_BASE = 1.0001


SEL_BALANCE_OF = "0x70a08231"


SEL_OWNER_OF = "0x6352211e"


SEL_POOL_AND_POSITION_INFO = "0x7ba03aad"


SEL_POSITION_LIQUIDITY = "0x1efeed33"


SEL_DECIMALS = "0x313ce567"


SEL_SYMBOL = "0x95d89b41"


SEL_GET_SLOT0 = "0xc815641c"


INFO_TICK_SHIFT = 8


def _token_decimals(address_word):
    address = _hex_address(address_word)
    if int(address, 16) == 0:
        return 18
    result = _evm_call(address, SEL_DECIMALS)
    return int(result, 16) if result and len(result) > 2 else 18


_LAST_EVM_ERROR = ""


_LAST_SCAN_MODE = ""


def _fail(reason):
    global _LAST_EVM_ERROR
    _LAST_EVM_ERROR = reason
    print(f"⚠️ EVM 失败: {reason}")
    return None


def _evm_rpc(method, params, timeout=45, tries=4):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    for attempt in range(tries):
        try:
            resp = requests.post(EVM_RPC, json=body, headers=HEADERS, timeout=timeout)
            if resp.status_code == 429:
                if attempt == tries - 1:
                    return _fail(f"{method}: HTTP 429 请求过于频繁（公共节点限流）")
                time.sleep(2 * (attempt + 1))
                continue
            if resp.status_code != 200:
                return _fail(f"{method}: HTTP {resp.status_code}")
            payload = resp.json()
            if "error" in payload:
                return _fail(f"{method}: {str(payload['error'])[:120]}")
            return payload.get("result")
        except Exception as e:
            if attempt == tries - 1:
                return _fail(f"{method}: {type(e).__name__} {str(e)[:100]}")
            time.sleep(1.5)
    return None


def _evm_call(to, data):
    return _evm_rpc("eth_call", [{"to": to, "data": data}, "latest"])


def _word(hexstr, index):
    return hexstr[2 + index * 64:2 + (index + 1) * 64]


def _signed24(value):
    if isinstance(value, str):
        value = int(value, 16)
    value &= 0xFFFFFF
    return value - (1 << 24) if value >= (1 << 23) else value


def _pad_uint(value):
    return format(int(value), "064x")


def _hex_address(word_hex):
    return "0x" + word_hex[24:]


def _pool_id(c0_word, c1_word, fee, spacing, hooks_word):
    packed = (bytes.fromhex(c0_word) + bytes.fromhex(c1_word)
              + int(fee).to_bytes(32, "big") + int(spacing).to_bytes(32, "big")
              + bytes.fromhex(hooks_word))
    return keccak256(packed).hex()


def _decode_abi_string(hexstr):
    if not hexstr or len(hexstr) <= 2:
        return None
    try:
        body = hexstr[2:]
        offset = int(body[0:64], 16) * 2
        length = int(body[offset:offset + 64], 16) * 2
        return bytes.fromhex(body[offset + 64:offset + 64 + length]).decode("utf-8", "replace")
    except Exception:
        return None


def _token_meta(address_word, cache=None):
    address = _hex_address(address_word)
    if int(address, 16) == 0:
        return "ETH", 18
    if cache is not None and address in cache:
        return cache[address]
    decimals = _evm_call(address, SEL_DECIMALS)
    dec = int(decimals, 16) if decimals and len(decimals) > 2 else 18
    symbol = _decode_abi_string(_evm_call(address, SEL_SYMBOL))
    result = ((symbol or address[:10]), dec)
    if cache is not None:
        cache[address] = result
    return result


def quote_price(tick, c0_word, c1_word, dec0, dec1):
    """把 tick 换算成「以 USDG 计价」的价格.

    返回 (price, tick_sign, basis)：tick_sign 为 +1 表示价格随 tick 上升，
    -1 表示随 tick 下降（此时 USDG 是 currency0，价格是它的倒数）。
    池子两侧都没有 USDG 时退回 token1 计价，basis 为 None。
    """
    c0, c1 = _hex_address(c0_word), _hex_address(c1_word)
    if c1.lower() == USDG_ADDRESS:
        return (TICK_BASE ** tick) * (10 ** (dec0 - dec1)), 1, "USDG"
    if c0.lower() == USDG_ADDRESS:
        return (TICK_BASE ** (-tick)) * (10 ** (dec1 - dec0)), -1, "USDG"
    return (TICK_BASE ** tick) * (10 ** (dec0 - dec1)), 1, None


MULTICALL3 = "0xca11bde05977b3631167028862be2a173976ca11"


SEL_AGGREGATE3 = "0x82ad56cb"


def _pad32(data):
    return data + b"\x00" * ((32 - len(data) % 32) % 32)


def encode_aggregate3(calls):
    """calls: [(目标地址, calldata bytes)] -> Multicall3.aggregate3 的 eth_call data."""
    blocks = []
    for target, data in calls:
        blocks.append(int(target, 16).to_bytes(32, "big")
                      + (1).to_bytes(32, "big")
                      + (96).to_bytes(32, "big")
                      + len(data).to_bytes(32, "big")
                      + _pad32(data))
    offsets, cursor = [], 32 * len(blocks)
    for block in blocks:
        offsets.append(cursor)
        cursor += len(block)
    array = len(blocks).to_bytes(32, "big")
    array += b"".join(offset.to_bytes(32, "big") for offset in offsets)
    array += b"".join(blocks)
    return SEL_AGGREGATE3 + (32).to_bytes(32, "big").hex() + array.hex()


def decode_aggregate3(result_hex):
    """(bool,bytes)[] -> [(success, bytes)]；返回长度不足的条目视为失败."""
    body = bytes.fromhex(result_hex[2:] if result_hex.startswith("0x") else result_hex)
    arg_offset = int.from_bytes(body[0:32], "big")
    count = int.from_bytes(body[arg_offset:arg_offset + 32], "big")
    table = arg_offset + 32
    out = []
    for i in range(count):
        rel = int.from_bytes(body[table + i * 32:table + (i + 1) * 32], "big")
        pos = table + rel
        success = int.from_bytes(body[pos:pos + 32], "big") == 1
        bytes_rel = int.from_bytes(body[pos + 32:pos + 64], "big")
        bpos = pos + bytes_rel
        length = int.from_bytes(body[bpos:bpos + 32], "big")
        out.append((success, body[bpos + 32:bpos + 32 + length]))
    return out


def call_data(selector_hex, *values):
    data = bytes.fromhex(selector_hex[2:])
    for value in values:
        if isinstance(value, str):
            raw = value[2:] if value.startswith("0x") else value
            data += bytes.fromhex(raw.rjust(64, "0"))
        else:
            data += int(value).to_bytes(32, "big")
    return data


def _multicall(calls):
    if not calls:
        return []
    result = _evm_call(MULTICALL3, encode_aggregate3(calls))
    if not result:
        return None
    return decode_aggregate3(result)


def _uint_from(entry):
    success, ret = entry
    if not success or len(ret) < 32:
        return None
    return int.from_bytes(ret[:32], "big")


RECENT_BLOCK_WINDOW = 5000


def _scan_transfer_token_ids(wallet):
    """扫描 PositionManager 的 Transfer 事件，取出转入过该钱包的 tokenId.

    返回 (token_ids, 扫描模式)。公共 Robinhood 节点对 eth_getLogs 的区块跨度
    有硬限制（实测 5000 块可、10000 块即拒），故全历史扫描失败时降级为最近窗口，
    模式标记为 "recent"，由界面提示用户改用手填 tokenId。
    """
    topic = "0x" + "0" * 24 + wallet[2:].lower()
    common = {"address": V4_POSITION_MANAGER, "topics": [TRANSFER_TOPIC, None, topic],
              "toBlock": "latest"}
    global _LAST_SCAN_MODE
    logs = _evm_rpc("eth_getLogs", [dict(common, fromBlock="0x0")], timeout=90)
    if logs is not None:
        _LAST_SCAN_MODE = "full"
        return sorted({int(entry["topics"][3], 16) for entry in logs}), "full"
    head = _evm_rpc("eth_blockNumber", [])
    if isinstance(head, str) and head.startswith("0x"):
        start = hex(max(int(head, 16) - RECENT_BLOCK_WINDOW, 0))
        logs = _evm_rpc("eth_getLogs", [dict(common, fromBlock=start)], timeout=90)
        if logs is not None:
            _LAST_SCAN_MODE = "recent"
            return sorted({int(entry["topics"][3], 16) for entry in logs}), "recent"
    _LAST_SCAN_MODE = ""
    return None, None


def fetch_evm_v4_positions(wallet, token_ids=None):
    wallet = (wallet or "").strip()
    if not wallet.lower().startswith("0x") or len(wallet) != 42:
        return None
    if token_ids is None:
        token_ids, _mode = _scan_transfer_token_ids(wallet)
        if token_ids is None:
            return None
    else:
        token_ids = sorted({int(t) for t in token_ids})
    if not token_ids:
        return []

    probe = []
    for token_id in token_ids:
        probe.append((V4_POSITION_MANAGER, call_data(SEL_OWNER_OF, token_id)))
        probe.append((V4_POSITION_MANAGER, call_data(SEL_POSITION_LIQUIDITY, token_id)))
    decoded = _multicall(probe)
    if decoded is None or len(decoded) != len(probe):
        return None

    alive = []
    for index, token_id in enumerate(token_ids):
        success, raw = decoded[2 * index]
        owner = "0x" + raw[12:32].hex() if success and len(raw) >= 32 else None
        if not owner or owner.lower() != wallet.lower():
            continue
        liquidity = _uint_from(decoded[2 * index + 1])
        if not liquidity:
            continue
        alive.append((token_id, liquidity))
    if not alive:
        return []

    infos = _multicall([(V4_POSITION_MANAGER, call_data(SEL_POOL_AND_POSITION_INFO, tid))
                        for tid, _ in alive])
    if infos is None or len(infos) != len(alive):
        return None

    parsed = []
    for (token_id, liquidity), entry in zip(alive, infos):
        success, raw = entry
        if not success or len(raw) < 192:
            continue
        hexstr = "0x" + raw.hex()
        c0, c1 = _word(hexstr, 0), _word(hexstr, 1)
        fee, spacing, hooks = (int(_word(hexstr, 2), 16), int(_word(hexstr, 3), 16),
                               _word(hexstr, 4))
        packed = int(_word(hexstr, 5), 16)
        tick_lower = _signed24((packed >> INFO_TICK_SHIFT) & 0xFFFFFF)
        tick_upper = _signed24((packed >> (INFO_TICK_SHIFT + 24)) & 0xFFFFFF)
        if tick_lower >= tick_upper:
            continue
        parsed.append((token_id, liquidity, c0, c1, fee, spacing, hooks,
                       tick_lower, tick_upper))
    if not parsed:
        return []

    pool_ids = []
    for item in parsed:
        pid = _pool_id(item[2], item[3], item[4], item[5], item[6])
        if pid not in pool_ids:
            pool_ids.append(pid)
    slots = _multicall([(V4_STATE_VIEW, call_data(SEL_GET_SLOT0, pid)) for pid in pool_ids])
    if slots is None or len(slots) != len(pool_ids):
        return None
    active_ticks = {}
    for pid, entry in zip(pool_ids, slots):
        success, raw = entry
        active_ticks[pid] = _signed24("0x" + raw[32:64].hex()) if success and len(raw) >= 64 else None

    token_addresses = []
    for item in parsed:
        for word in (item[2], item[3]):
            address = _hex_address(word)
            if address not in token_addresses:
                token_addresses.append(address)
    meta = {}
    queried = [a for a in token_addresses if int(a, 16) != 0]
    meta_calls = []
    for address in queried:
        meta_calls.append((address, call_data(SEL_DECIMALS)))
        meta_calls.append((address, call_data(SEL_SYMBOL)))
    if meta_calls:
        results = _multicall(meta_calls)
        if results is None or len(results) != len(meta_calls):
            return None
        for index, address in enumerate(queried):
            decimals = _uint_from(results[2 * index]) or 18
            symbol_ok, symbol_raw = results[2 * index + 1]
            symbol = (_decode_abi_string("0x" + symbol_raw.hex())
                      if symbol_ok and symbol_raw else None)
            meta[address] = (symbol or address[:10], decimals)
    for address in token_addresses:
        if int(address, 16) == 0:
            meta[address] = ("ETH", 18)

    positions = []
    for token_id, liquidity, c0, c1, fee, spacing, hooks, tick_lower, tick_upper in parsed:
        symbol0, dec0 = meta[_hex_address(c0)]
        symbol1, dec1 = meta[_hex_address(c1)]
        active_tick = active_ticks.get(_pool_id(c0, c1, fee, spacing, hooks))

        p_lower, sign, basis = quote_price(tick_lower, c0, c1, dec0, dec1)
        p_upper, _, _ = quote_price(tick_upper, c0, c1, dec0, dec1)
        if sign > 0:
            tick_min, tick_max = tick_lower, tick_upper
        else:
            tick_min, tick_max = tick_upper, tick_lower
        positions.append({
            "token_id": token_id,
            "pool_id": "0x" + _pool_id(c0, c1, fee, spacing, hooks),
            "pool_name": f"{symbol0}/{symbol1}",
            "token_x_symbol": symbol0,
            "token_y_symbol": symbol1,
            "price_basis": basis or symbol1,
            "fee": fee,
            "tick_spacing": spacing,
            "tick_lower": tick_lower,
            "tick_upper": tick_upper,
            "tick_min": tick_min,
            "tick_max": tick_max,
            "lower_price": min(p_lower, p_upper),
            "upper_price": max(p_lower, p_upper),
            "active_tick": active_tick,
            "current_price": (quote_price(active_tick, c0, c1, dec0, dec1)[0]
                              if active_tick is not None else None),
            "in_range": (active_tick is not None and tick_lower <= active_tick <= tick_upper),
            "liquidity": liquidity,
        })
    return positions


def preview_evm_wallet(wallet, token_ids_text=""):
    wallet = (wallet or "").strip()
    if not wallet.lower().startswith("0x") or len(wallet) != 42:
        return {"ok": False, "error": "⚠️ 请填写 Robinhood 链的 EVM 钱包地址（0x 开头、42 位）。",
                "positions": []}
    manual = [int(t) for t in str(token_ids_text or "").replace(",", " ").split()
              if t.strip().isdigit()]
    positions = fetch_evm_v4_positions(wallet, manual or None)
    if positions is None:
        detail = f"（原因：{_LAST_EVM_ERROR}）" if _LAST_EVM_ERROR else ""
        hint = ("" if manual else
                "　公共 RPC 不允许全历史日志查询，可在下方手动填入 tokenId"
                "（从 Uniswap 界面复制），或配置 ROBINHOOD_RPC 换用付费节点。")
        return {"ok": False,
                "error": f"❌ 连接失败：读取链上仓位失败{detail}{hint}",
                "positions": []}
    if not positions:
        if _LAST_SCAN_MODE == "recent":
            return {"ok": False,
                    "error": f"⚠️ 只扫描了最近 {RECENT_BLOCK_WINDOW} 个区块"
                             "（公共 RPC 不允许全历史日志查询），未发现仓位。"
                             "请在下方手动填入 tokenId（从 Uniswap 界面复制），"
                             "或配置 ROBINHOOD_RPC 换用付费节点。",
                    "positions": []}
        return {"ok": False,
                "error": "⚠️ 没有找到 Uniswap v4 仓位。若你确实有仓位，"
                         "请在下方手动填入 tokenId。",
                "positions": []}
    return {"ok": True, "error": None, "positions": positions}

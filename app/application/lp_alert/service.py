"""LP 告警应用服务：取当前值、判定、消息与单轮执行。"""

import traceback

from send_feishu_msg import send_feishu_msg

from app.core.settings import FEISHU_WEBHOOK
from app.domain.lp_alert.evaluator import (
    _current_price,
    evaluate,
    needs_rearm,
    needs_rearm_target,
    units_plausible,
)
from app.domain.lp_alert.models import STATUS_CLOSED, STATUS_ERROR, STATUS_OPEN
from app.infrastructure.db.lp_alert import (
    clear_alert_flag,
    load_rules,
    mark_alerted,
    set_enabled,
    set_status,
    update_runtime,
)
from app.infrastructure.market_data.evm_rpc import (
    SEL_GET_SLOT0,
    SEL_POOL_AND_POSITION_INFO,
    V4_POSITION_MANAGER,
    V4_STATE_VIEW,
    _evm_call,
    _pad_uint,
    _pool_id,
    _signed24,
    _token_decimals,
    _word,
    quote_price,
)
from app.infrastructure.market_data.meteora import (
    _dexscreener_chain,
    fetch_dexscreener_pair,
    fetch_meteora_pool,
    fetch_meteora_positions,
    fetch_open_portfolio,
)


def _cur_dlmm(rule):
    positions = fetch_meteora_positions(rule["pool_address"], rule["wallet"])
    if positions is None:
        return None, STATUS_ERROR
    for pos in positions:
        if pos.get("position_address") == rule.get("position_address"):
            if pos.get("is_closed"):
                return None, STATUS_CLOSED
            return pos, STATUS_OPEN
    return None, STATUS_CLOSED


def _cur_pool_price(rule):
    pair = fetch_dexscreener_pair(rule["chain"], rule["pool_address"])
    if pair is None or pair.get("price") is None:
        return None, STATUS_ERROR
    return pair, STATUS_OPEN


def _cur_evm_v4(rule):
    token_id = rule.get("token_id")
    if token_id is None:
        return None, STATUS_ERROR
    info = _evm_call(V4_POSITION_MANAGER, SEL_POOL_AND_POSITION_INFO + _pad_uint(token_id))
    if not info:
        return None, STATUS_ERROR
    c0, c1 = _word(info, 0), _word(info, 1)
    fee, spacing, hooks = int(_word(info, 2), 16), int(_word(info, 3), 16), _word(info, 4)
    pool_id = _pool_id(c0, c1, fee, spacing, hooks)
    if not pool_id:
        return None, STATUS_ERROR
    slot = _evm_call(V4_STATE_VIEW, SEL_GET_SLOT0 + pool_id)
    if not slot:
        return None, STATUS_ERROR
    active_tick = _signed24(_word(slot, 1))
    price, _, _ = quote_price(active_tick, c0, c1,
                              _token_decimals(c0), _token_decimals(c1))
    return {"active_tick": active_tick, "active_price": price,
            "pnl_pct": None, "price": None}, STATUS_OPEN


def build_message(rule, cur, fired, pool_info):
    name = (pool_info or {}).get("name") or rule.get("pool_name") or rule["pool_address"]
    lines = []
    if rule.get("kind") == "evm_v4":
        basis = rule.get("price_basis") or rule.get("token_y_symbol") or ""
        price = cur.get("active_price")
        if "target" in fired:
            base = float(rule.get("entry_price") or 0)
            pct = (price / base - 1) * 100 if base else 0.0
            lines.append(f"🎯 【盈利达标】现价 {price:.10g}，较建立规则时 {base:.10g} "
                         f"涨 {pct:.2f}%（目标 {float(rule['target_pct']):.2f}%）")
        if "floor" in fired:
            lines.append(f"🚨 【跌穿区间下界】现价 {price:.10g} < 下界 "
                         f"{float(rule['floor_price']):.10g}")
        lines.append("")
        lines.append(f"池子: {name}（robinhood / uniswap v4，{basis} 计价）")
        lines.append(f"仓位 tokenId: {rule.get('token_id')}｜当前 tick {cur.get('active_tick')}")
        lines.append(f"区间: {float(rule.get('lower_price') or 0):.10g} ~ "
                     f"{float(rule.get('max_price') or 0):.10g}")
        lines.append(f"poolId: {rule['pool_address']}")
        return "\n".join(lines)
    if "target" in fired:
        if rule.get("target_mode") == "pnl_pct":
            lines.append(f"🎯 【盈利达标】真实持仓盈亏 {cur.get('pnl_pct'):.2f}% "
                         f"（目标 {float(rule['target_pct']):.2f}%）")
        else:
            base = float(rule["entry_price"])
            pct = (cur["price"] / base - 1) * 100 if base else 0.0
            lines.append(f"🎯 【盈利达标】现价 {cur['price']:.10g}，较登记价 {base:.10g} "
                         f"涨 {pct:.2f}%（目标 {float(rule['target_pct']):.2f}%）")
    if "floor" in fired:
        price = _current_price(rule, cur)
        lines.append(f"🚨 【价格跌穿】现价 {price:.10g} ≤ 阈值 {float(rule['floor_price']):.10g}")
    lines.append("")
    lines.append(f"池子: {name} ({rule['chain']})")
    lines.append(f"池子地址: {rule['pool_address']}")
    if rule.get("position_address"):
        lines.append(f"仓位地址: {rule['position_address']}")
    if rule.get("position_address"):
        lines.append(f"仓位区间: bin {rule.get('lower_bin_id')} ~ {rule.get('upper_bin_id')}")
    if rule["kind"] == "dlmm":
        lines.append(f"链接: https://app.meteora.ag/dlmm/{rule['pool_address']}")
    else:
        lines.append(f"链接: https://dexscreener.com/{_dexscreener_chain(rule['chain'])}/{rule['pool_address']}")
    return "\n".join(lines)


def check_rule(rule):
    pool_info = None
    if rule["kind"] == "dlmm":
        pool_info = fetch_meteora_pool(rule["pool_address"])
        cur, status = _cur_dlmm(rule)
        if status == STATUS_CLOSED:
            name = rule.get("pool_name") or rule["pool_address"]
            return {
                "status": STATUS_CLOSED,
                "fired": ["closed"],
                "cur": None,
                "message": (f"ℹ️ 【仓位已关闭】{name}\n"
                            f"仓位地址: {rule.get('position_address')}\n"
                            f"该仓位已不在开放列表中，规则自动停用。"),
            }
    elif rule["kind"] == "evm_v4":
        cur, status = _cur_evm_v4(rule)
        pool_info = {"name": rule.get("pool_name") or rule["pool_address"]}
    else:
        cur, status = _cur_pool_price(rule)
        if cur is not None:
            pool_info = {"name": " / ".join(
                filter(None, [cur.get("base_symbol"), cur.get("quote_symbol")]))}

    if cur is None:
        return {"status": status, "fired": [], "cur": None, "message": None}

    cur = dict(cur)
    if rule["kind"] == "dlmm":
        cur["price"] = None
        if not units_plausible(cur.get("active_price"),
                               (pool_info or {}).get("current_price")):
            print(f"⚠️ 规则 {rule['id']} 单位校验未通过: poolActivePrice="
                  f"{cur.get('active_price')} vs pool current_price="
                  f"{(pool_info or {}).get('current_price')}；"
                  f"本轮跳过跌穿判定（不告警），请人工核对 minPrice 单位。")
            rule = dict(rule)
            rule["enable_floor_alert"] = 0
    elif rule["kind"] != "evm_v4":
        cur["pnl_pct"] = None
        cur["active_price"] = None

    fired = [k for k, v in evaluate(rule, cur).items() if v]
    if rule.get("target_alerted") and "target" in fired:
        fired.remove("target")
    if rule.get("floor_alerted") and "floor" in fired:
        fired.remove("floor")

    message = None
    if fired:
        message = build_message(rule, cur, fired, pool_info)

    if needs_rearm(rule, cur):
        clear_alert_flag(rule["id"], "floor_alerted")
    if needs_rearm_target(rule, cur):
        clear_alert_flag(rule["id"], "target_alerted")

    return {"status": STATUS_OPEN, "fired": fired, "cur": cur, "message": message}


RUN_TIME_FIELDS = ("min_price", "max_price", "last_pnl_pct", "last_active_price")


def run_once():
    rules = load_rules(enabled_only=True)
    if not rules:
        print("ℹ️ 没有启用的 LP 告警规则")
        return

    print(f"🔍 本轮检查 {len(rules)} 条 LP 告警规则 ...")
    ok = fail = 0
    for rule in rules:
        try:
            result = check_rule(rule)
        except Exception:
            print(f"❌ 规则 {rule['id']} 检查异常")
            traceback.print_exc()
            fail += 1
            continue

        cur = result["cur"]
        if result["status"] == STATUS_ERROR:
            print(f"⚠️ 规则 {rule['id']} 取数失败，跳过（不告警）")
            set_status(rule["id"], STATUS_ERROR)
            fail += 1
            continue

        ok += 1
        values = {"status": result["status"]}
        if cur:
            if cur.get("min_price") is not None:
                values["min_price"] = cur["min_price"]
            if cur.get("max_price") is not None:
                values["max_price"] = cur["max_price"]
            values["last_pnl_pct"] = cur.get("pnl_pct")
            values["last_active_price"] = _current_price(rule, cur)
            if cur.get("is_out_of_range") is not None:
                values["is_out_of_range"] = 1 if cur["is_out_of_range"] else 0
        update_runtime(rule["id"], values)

        if result["fired"]:
            print(f"🚨 规则 {rule['id']} 触发: {result['fired']}")
            text = result["message"]
            if text:
                send_feishu_msg(FEISHU_WEBHOOK, text)
            for name in ("target", "floor"):
                if name in result["fired"]:
                    mark_alerted(rule["id"], f"{name}_alerted")
            if "closed" in result["fired"]:
                set_status(rule["id"], STATUS_CLOSED)
                set_enabled(rule["id"], False)

    print(f"📊 本轮完成: 成功 {ok}，失败 {fail}")


CHAIN_OPTIONS = ["sol"]


POOL_PRICE_CHAINS = ["robinhood", "bsc", "base", "eth"]


def preview_dlmm(pool_address, wallet):
    pool = fetch_meteora_pool(pool_address)
    if not pool:
        return {"ok": False, "error": "❌ 连接失败：Meteora 未找到该池子地址，请核对池子地址是否正确。",
                "pool": None, "positions": []}
    positions = fetch_meteora_positions(pool_address, wallet)
    if positions is None:
        return {"ok": False, "error": "❌ 连接成功，但读取仓位失败（接口异常），请稍后重试。",
                "pool": pool, "positions": []}
    if not positions:
        return {"ok": False, "error": "⚠️ 该钱包在此池没有开放仓位。请确认钱包地址，或该仓位是否已关闭。",
                "pool": pool, "positions": []}
    return {"ok": True, "error": None, "pool": pool, "positions": positions}


def preview_pool_price(chain, pool_address):
    pair = fetch_dexscreener_pair(chain, pool_address)
    if not pair or pair.get("price") is None:
        return {"ok": False,
                "error": "❌ 连接失败：Dexscreener 未找到该池子地址，请核对地址与所属链是否正确。",
                "pair": None, "floor": None}
    return {"ok": True, "error": None, "pair": pair, "floor": None}


def preview_wallet(wallet):
    pools = fetch_open_portfolio(wallet)
    if pools is None:
        return {"ok": False,
                "error": "❌ 连接失败：读取钱包组合失败，请核对钱包地址或稍后重试。",
                "pools": []}
    if not pools:
        return {"ok": False,
                "error": "⚠️ 该钱包没有开放的 LP 仓位（建仓后请稍等片刻再试）。",
                "pools": []}
    return {"ok": True, "error": None, "pools": pools}

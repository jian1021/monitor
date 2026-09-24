"""LP 告警判定：纯逻辑，无 IO。"""


def _current_price(rule, cur):
    if rule.get("kind") == "dlmm":
        return cur.get("active_price")
    if rule.get("kind") == "evm_v4":
        return cur.get("active_price")
    return cur.get("price")


def evaluate(rule, cur):
    fires = {"target": False, "floor": False}

    if rule.get("kind") == "evm_v4":
        price = cur.get("active_price")
        if (rule.get("enable_floor_alert") and rule.get("floor_price") is not None
                and price is not None):
            if price < float(rule["floor_price"]):
                fires["floor"] = True
        if (rule.get("enable_target_alert") and rule.get("target_pct") is not None
                and price is not None and rule.get("entry_price")):
            if price >= float(rule["entry_price"]) * (100.0 + float(rule["target_pct"])) / 100.0:
                fires["target"] = True
        return fires

    if rule.get("enable_target_alert") and rule.get("target_pct") is not None:
        tgt = float(rule["target_pct"])
        if rule.get("target_mode") == "pnl_pct":
            value = cur.get("pnl_pct")
            if value is not None and value >= tgt:
                fires["target"] = True
        else:
            value = cur.get("price")
            base = rule.get("entry_price")
            if value is not None and base is not None and float(base) > 0:
                # 必须用 (100+tgt)/100 而不是 (1+tgt/100)：后者对 base=100、tgt=10
                # 会算出 110.00000000000001，导致「价格正好 +10%」判定为未达标。
                target_price = float(base) * (100.0 + tgt) / 100.0
                if value >= target_price:
                    fires["target"] = True

    if rule.get("enable_floor_alert") and rule.get("floor_price") is not None:
        value = _current_price(rule, cur)
        if value is not None and value <= float(rule["floor_price"]):
            fires["floor"] = True

    return fires


def needs_rearm(rule, cur):
    if not rule.get("rearm") or not rule.get("floor_alerted"):
        return False
    if rule.get("kind") == "evm_v4":
        price = cur.get("active_price")
        floor = rule.get("floor_price")
        return price is not None and floor is not None and price > float(floor)
    if rule.get("floor_price") is None:
        return False
    value = _current_price(rule, cur)
    if value is None:
        return False
    return value > float(rule["floor_price"])


def _target_price(rule):
    """目标价门槛 = entry_price * (100 + target_pct) / 100。"""
    entry = rule.get("entry_price")
    tgt = rule.get("target_pct")
    if entry is None or tgt is None:
        return None
    try:
        return float(entry) * (100.0 + float(tgt)) / 100.0
    except (TypeError, ValueError):
        return None


def needs_rearm_target(rule, cur):
    """目标回落至门槛以下时解除 target_alerted，使下次达标重新告警。"""
    if not rule.get("rearm") or not rule.get("target_alerted"):
        return False
    if rule.get("target_mode") == "pnl_pct":
        value = cur.get("pnl_pct")
        tgt = rule.get("target_pct")
        if value is None or tgt is None:
            return False
        return value < float(tgt)
    tp = _target_price(rule)
    if tp is None:
        return False
    if rule.get("kind") == "evm_v4":
        price = cur.get("active_price")
        return price is not None and price < tp
    price = cur.get("price")
    if price is None or float(rule.get("entry_price")) <= 0:
        return False
    return price < tp


def units_plausible(active_price, pool_current_price, tolerance=100.0):
    """校验 poolActivePrice 与池子 current_price 同尺度.

    /pools 的 current_price 已实测为「token Y per token X」，而 poolActivePrice
    同为池子活跃 bin 的价格，两者本应相等。若相差超过 tolerance 倍，说明字段单位
    不一致或解析出错 —— 此时静默比较会让跌穿告警永久失效，必须拒绝判定并报警日志。
    任一侧缺失时返回 True（信息不足，不阻断）。
    """
    if active_price is None or pool_current_price is None:
        return True
    if active_price <= 0 or pool_current_price <= 0:
        return False
    ratio = active_price / pool_current_price
    return (1.0 / tolerance) <= ratio <= tolerance

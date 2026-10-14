"""
筛选逻辑（文档 3. 默认筛选参数 + 3.1 市值筛选规则）。

核心规则：
  - 市值：代币 X / Y 分别判断；缺市值 → 标记“市值数据不足”，默认不通过。
  - 市值来源优先流通市值，其次 FDV（并标注口径）。
  - 支持“任一代币满足”(any) 与“两个代币均满足”(both) 两种模式，默认 any。
  - 市值筛选在展示前执行；另提供“显示未通过筛选的池子”开关用于排查。
"""

from __future__ import annotations

from typing import Any, Optional

# 数据完整性：关键指标集合
KEY_METRICS = ("tvl", "volume_24h", "fees_24h", "fee_tvl_ratio_24h")


def _fnum(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def to_bool(v: Any) -> Optional[bool]:
    """把 SQLite 中以 TEXT('true'/'false') 或 0/1 存储的布尔值规范化。

    返回 None 表示数据缺失（未知）。
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "1", "yes", "y"):
            return True
        if s in ("false", "0", "no", "n"):
            return False
    return None


def _auth_pass(actual: Optional[bool], expected: str) -> Optional[bool]:
    """授权条件判断：'any' 始终通过；数据缺失返回 None（未知，跳过淘汰）。"""
    if expected == "any":
        return True
    if actual is None:
        return None
    return actual == (expected == "yes")


def _best_mcap(row: dict, side: str) -> Optional[float]:
    """取某侧代币可用于筛选的市值：优先 market_cap，其次 fdv。"""
    mc = _fnum(row.get(f"token_{side}_market_cap"))
    if mc is not None and mc > 0:
        return mc
    fdv = _fnum(row.get(f"token_{side}_fdv"))
    if fdv is not None and fdv > 0:
        return fdv
    return None


def check_integrity(row: dict) -> tuple[bool, list[str]]:
    """校验关键指标是否完整。返回 (是否完整, 缺失字段列表)。"""
    missing: list[str] = []
    for m in KEY_METRICS:
        if _fnum(row.get(m)) is None:
            missing.append(m)
    if _best_mcap(row, "x") is None:
        missing.append("token_x_market_cap")
    if _best_mcap(row, "y") is None:
        missing.append("token_y_market_cap")
    return (len(missing) == 0), missing


def evaluate_pool(row: dict, filters: dict) -> dict:
    """对单个池子执行全部筛选条件，返回详细评估结果。"""
    result: dict[str, Any] = {
        "type_pass": True,
        "mcap_pass": False,
        "volume_pass": False,
        "tvl_pass": False,
        "fee_tvl_pass": False,
        "bin_step_pass": True,
        "fee_rate_pass": True,
        "holders_pass": True,
        "open_positions_pass": True,
        "integrity_ok": True,
        "passed": False,
        "mcap_which": "none",
        "reasons": [],
    }

    # 池类型
    allowed_types = filters.get("pool_types") or ["DLMM"]
    if row.get("pool_type") != "DLMM" or row.get("pool_type") not in allowed_types:
        result["type_pass"] = False
        result["reasons"].append("池类型不在筛选范围")

    # 数据完整性
    complete, missing = check_integrity(row)
    result["integrity_ok"] = complete
    result["missing"] = missing

    # 市值
    min_mcap = float(filters.get("min_market_cap", 0.0))
    mode = filters.get("market_cap_mode", "any")
    mx = _best_mcap(row, "x")
    my = _best_mcap(row, "y")
    x_ok = mx is not None and mx > min_mcap
    y_ok = my is not None and my > min_mcap

    if mx is None and my is None:
        result["mcap_pass"] = False
        result["mcap_which"] = "none"
        result["reasons"].append("市值数据不足")
    elif mode == "both":
        result["mcap_pass"] = x_ok and y_ok
        if x_ok and y_ok:
            result["mcap_which"] = "both"
        elif x_ok:
            result["mcap_which"] = "x"
        elif y_ok:
            result["mcap_which"] = "y"
        else:
            result["mcap_which"] = "none"
    else:  # any
        result["mcap_pass"] = x_ok or y_ok
        if x_ok and y_ok:
            result["mcap_which"] = "both"
        elif x_ok:
            result["mcap_which"] = "x"
        elif y_ok:
            result["mcap_which"] = "y"
        else:
            result["mcap_which"] = "none"

    if not result["mcap_pass"] and result["mcap_which"] != "none":
        result["reasons"].append("市值未达阈值")

    # 成交量
    vol = _fnum(row.get("volume_24h"))
    min_vol = float(filters.get("min_volume_24h", 0.0))
    result["volume_pass"] = vol is not None and vol >= min_vol
    if vol is None:
        result["reasons"].append("成交量数据不足")
    elif not result["volume_pass"]:
        result["reasons"].append("24h 成交量低于阈值")

    # TVL
    tvl = _fnum(row.get("tvl"))
    min_tvl = float(filters.get("min_tvl", 0.0))
    result["tvl_pass"] = tvl is not None and tvl >= min_tvl
    if tvl is None:
        result["reasons"].append("TVL 数据不足")
    elif not result["tvl_pass"]:
        result["reasons"].append("TVL 低于阈值")

    # Fees/TVL
    ftr = _fnum(row.get("fee_tvl_ratio_24h"))
    min_ftr = float(filters.get("min_fee_tvl_ratio", 0.0))
    result["fee_tvl_pass"] = ftr is not None and ftr >= min_ftr
    if ftr is None:
        result["reasons"].append("Fees/TVL 数据不足")
    elif not result["fee_tvl_pass"]:
        result["reasons"].append("Fees/TVL 低于阈值")

    # bin step (仅对有 bin_step 的池生效，如 DLMM)
    bs = _fnum(row.get("bin_step"))
    bs_min = float(filters.get("bin_step_min", 0.0))
    bs_max = float(filters.get("bin_step_max", 0.0))
    bs_active = bs_min > 0 or bs_max > 0
    if not bs_active:
        result["bin_step_pass"] = True
    elif bs is None:
        result["bin_step_pass"] = False
        result["reasons"].append("DLMM bin step 数据不足")
    else:
        ok = (bs_min <= 0 or bs >= bs_min) and (bs_max <= 0 or bs <= bs_max)
        result["bin_step_pass"] = ok
        if not ok:
            result["reasons"].append("bin step 不在范围")

    # 费率 (pool config fee rate)
    fr = _fnum(row.get("fee_rate_pct"))
    fr_min = float(filters.get("min_fee_rate_pct", 0.0))
    fr_max = float(filters.get("max_fee_rate_pct", 0.0))
    fr_active = fr_min > 0 or fr_max > 0
    if not fr_active:
        result["fee_rate_pass"] = True
    elif fr is None:
        result["fee_rate_pass"] = False
        result["reasons"].append("费率数据不足")
    else:
        # 0.0 表示不限制
        ok = (fr_min <= 0 or fr >= fr_min) and (fr_max <= 0 or fr <= fr_max)
        result["fee_rate_pass"] = ok
        if not ok:
            result["reasons"].append("费率不在范围")

    # 持仓地址数（仅有真实数据时才参与淘汰；缺失则标注并跳过）
    hx = _fnum(row.get("token_x_holders"))
    min_holders = float(filters.get("min_token_x_holders", 0))
    if min_holders <= 0:
        pass
    elif hx is None:
        result["reasons"].append("代币X持仓数数据不足（已跳过）")
    elif hx < min_holders:
        result["holders_pass"] = False
        result["reasons"].append("代币X持仓数不足")

    # 前10持仓占比（缺失则标注并跳过）
    top10 = _fnum(row.get("token_x_top10_holders"))
    max_top10 = float(filters.get("max_token_x_top10_holders", 0))
    if max_top10 <= 0:
        pass
    elif top10 is None:
        result["reasons"].append("前10持仓占比数据不足（已跳过）")
    elif top10 > max_top10:
        result["holders_pass"] = False
        result["reasons"].append("前10持仓占比过高")

    # 开发方持仓占比（缺失则标注并跳过）
    # 与展示精度（2 位小数）保持一致：<= 阈值 + 0.01 视为未超过，
    # 避免如 0.00002% 这类“显示为 0%”的真实值被误判超限。
    dev = _fnum(row.get("token_x_dev_holders"))
    max_dev = float(filters.get("max_token_x_dev_holders", 0))
    if "max_token_x_dev_holders" not in filters:
        pass
    elif dev is None:
        result["reasons"].append("开发方持仓占比数据不足（已跳过）")
    elif dev > max_dev + 0.01:
        result["holders_pass"] = False
        result["reasons"].append("开发方持仓占比过高")

    # 授权检查（冻结/铸币）：数据缺失则标注并跳过，不与真实值误判
    for label, col, fkey in (
        ("冻结授权", "token_x_freeze_auth", "token_x_freeze_auth"),
        ("铸币授权", "token_x_mint_auth", "token_x_mint_auth"),
    ):
        actual = to_bool(row.get(col))
        expected = str(filters.get(fkey, "any"))
        ok = _auth_pass(actual, expected)
        if ok is None:
            result["reasons"].append(f"{label}数据不足（已跳过）")
        elif not ok:
            result["holders_pass"] = False
            result["reasons"].append(f"{label}不匹配（期望: {expected}）")

    # 开仓人数：pool-discovery 接口按池提供真实值（open_positions）。
    # 缺失则标注跳过，不误判。
    actual_open = row.get("open_positions")
    if actual_open is None or str(actual_open) == "":
        result["open_positions_pass"] = True
        result["open_positions_available"] = False
        result["reasons"].append("开仓人数数据不足（已跳过）")
    else:
        result["open_positions_available"] = True
        try:
            ok = float(actual_open) >= float(filters.get("min_open_positions", 0) or 0)
        except (TypeError, ValueError):
            ok = True
        result["open_positions_pass"] = ok
        if not ok:
            result["reasons"].append(f"开仓人数不足（{int(float(actual_open))} < {filters.get('min_open_positions')}）")

    # 数据完整性开关
    require_complete = bool(filters.get("require_complete", True))
    integrity_gate = result["integrity_ok"] or not require_complete

    result["passed"] = bool(
        result["type_pass"]
        and result["mcap_pass"]
        and result["volume_pass"]
        and result["tvl_pass"]
        and result["fee_tvl_pass"]
        and result["bin_step_pass"]
        and result["fee_rate_pass"]
        and result["holders_pass"]
        and result["open_positions_pass"]
        and integrity_gate
    )
    if require_complete and not result["integrity_ok"] and result["passed"] is False:
        result["reasons"].append("关键指标不完整")
    return result


def apply_filters(rows: list[dict], filters: dict) -> list[dict]:
    """对多行执行筛选，返回附加评估字段后的全部行（调用方据 passed 取舍）。"""
    out = []
    for row in rows:
        r = dict(row)
        r.update(evaluate_pool(row, filters))
        out.append(r)
    return out

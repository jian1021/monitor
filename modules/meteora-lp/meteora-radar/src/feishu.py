"""飞书告警：消息格式化与推送。"""

from __future__ import annotations

import requests


def send(cfg, text, logger=None) -> bool:
    """推送一条文本消息；未配置 Webhook 或 dry_run 时只打印/记日志。"""
    webhook = str(getattr(cfg, "feishu_webhook", "") or "")
    if getattr(cfg, "dry_run", False):
        _log(logger, f"[dry-run] 不发送飞书，消息预览:\n{text}")
        return False
    if not webhook:
        _log(logger, f"未配置 FEISHU_WEBHOOK，仅输出消息:\n{text}")
        return False
    try:
        resp = requests.post(
            webhook,
            json={"msg_type": "text", "content": {"text": text}},
            timeout=10,
        )
        if resp.status_code == 200:
            _log(logger, "飞书推送成功")
            return True
        _log(logger, f"飞书推送失败 status={resp.status_code} body={resp.text[:200]}")
        return False
    except Exception as exc:  # noqa: BLE001
        _log(logger, f"飞书推送异常: {exc}")
        return False


# ===================== 消息格式化 =====================
def _fmt_price(value):
    if value is None:
        return "未知"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "未知"
    if value >= 1:
        return f"${value:,.4f}"
    return f"${value:.6g}"


def _fmt_usd(value):
    if value is None:
        return "未知"
    try:
        return f"${float(value):,.0f}"
    except (TypeError, ValueError):
        return "未知"


def _links(pool_address, token_address) -> str:
    lines = []
    if pool_address:
        lines.append(f"  🌊 Meteora: https://app.meteora.ag/dlmm/{pool_address}")
    if token_address:
        lines.append(f"  🔗 GMGN: https://gmgn.ai/sol/token/{token_address}")
    return "\n".join(lines)


def build_rsi_alert_message(hits, cfg) -> str:
    lines = [
        f"🚨【Meteora 超跌候选】{len(hits)} 个 "
        f"(RSI({int(cfg.rsi_period)},{cfg.rsi_timeframe}) < {cfg.rsi_1h_max:g})",
    ]
    for hit in hits[: cfg.max_alert_items]:
        ratio = hit.get("volume_tvl_ratio")
        ratio_text = f"{ratio:.2f}" if isinstance(ratio, (int, float)) else "未知"
        lines.append(
            f"• {hit.get('symbol') or hit.get('token_address', '')[:8]} | "
            f"RSI: {hit.get('rsi'):.1f} | 现价: {_fmt_price(hit.get('price'))}\n"
            f"  TVL: {_fmt_usd(hit.get('tvl'))} | 24h量: {_fmt_usd(hit.get('volume_24h_usd'))} | "
            f"量/TVL: {ratio_text} | 市值: {_fmt_usd(hit.get('market_cap'))}\n"
            f"{_links(hit.get('pool_address'), hit.get('token_address'))}"
            + _unknown_suffix(hit.get("unknowns"))
        )
    if len(hits) > cfg.max_alert_items:
        lines.append(f"（仅列出前 {cfg.max_alert_items} 个）")
    lines.append("⚠️ RSI<10 仅为超跌条件，不代表价格一定反弹，请人工复核。")
    return "\n".join(lines)


def build_rebound_alert_message(hits, cfg) -> str:
    lines = [f"🚀【5m 止跌 + 突破确认】{len(hits)} 个"]
    for hit in hits[: cfg.max_alert_items]:
        volume_ratio = hit.get("volume_ratio")
        volume_text = (f"{volume_ratio:.2f}x" + ("(放量)" if hit.get("volume_confirmed") else "(未放量)")
                       if isinstance(volume_ratio, (int, float)) else "未知")
        lines.append(
            f"• {hit.get('symbol') or hit.get('token_address', '')[:8]} | "
            f"突破价: {_fmt_price(hit.get('breakout_close'))} | "
            f"前高: {_fmt_price(hit.get('prior_high'))} | 量比: {volume_text} | "
            f"现价: {_fmt_price(hit.get('price'))}\n"
            f"{_links(hit.get('pool_address'), hit.get('token_address'))}"
        )
    if len(hits) > cfg.max_alert_items:
        lines.append(f"（仅列出前 {cfg.max_alert_items} 个）")
    lines.append("⚠️ 规则为待验证假设，可能出现假突破，请人工复核。")
    return "\n".join(lines)


def _unknown_suffix(unknowns) -> str:
    items = [str(u) for u in (unknowns or []) if u]
    if not items:
        return ""
    return "\n  ❔ 未知项: " + "、".join(items)


def _log(logger, message):
    if logger:
        logger.info(message)
    else:
        print(message)

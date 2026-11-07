"""监控用例：把「一轮扫描」编排成可被调度器调用的函数。

main.py 只负责装配（读配置、注册 spec、循环判定到期），具体扫什么、
命中怎么告警都在本模块。每个函数对应一个调度模块（interval_key）：
  run_rsi_monitor          -> rsi（Meteora 池 / 可转债）
  run_etf_monitor          -> etf_rsi（ETF 周线，独立群）
  run_crypto_monitor       -> crypto（OKX 主流币）
  run_onchain_token_monitor-> onchain_token（链上代币）
"""

from app.application.monitors import rsi as monitor_rsi
from app.application.monitors.rsi_alerts import (
    AlertBatch,
    baostock_login,
    baostock_logout,
    scan_a_share,
    scan_meteora_pools,
    scan_okx_symbols,
    scan_onchain_tokens,
    webhook_for,
)


def _enabled(items) -> list:
    return [item for item in items if item.get("enabled", True)]


def _scan_a_share(items, settings, tag, batch: AlertBatch) -> None:
    """A 股类扫描统一承担 baostock 登录/登出，空列表直接跳过登录。"""
    if not items:
        return
    login_ok = baostock_login()
    try:
        scan_a_share(items, settings, tag, batch, login_ok)
    finally:
        if login_ok:
            baostock_logout()


def run_rsi_monitor(config: dict) -> None:
    """Meteora 池 + 可转债；ETF 由 run_etf_monitor 独立按周执行。"""
    batch = AlertBatch(empty_note="所有标的 RSI 均处于正常区间，不触发推送。")
    m_set = monitor_rsi.DEFAULT_SETTINGS["meteora"]
    b_set = monitor_rsi.DEFAULT_SETTINGS["bond"]

    scan_meteora_pools(config.get("meteora", []), m_set, batch)
    _scan_a_share(_enabled(config.get("convertible_bonds", [])), b_set, "可转债", batch)

    batch.flush()


def run_etf_monitor(config: dict) -> None:
    """ETF 周线 RSI：独立模块、独立间隔（默认 7 天）、独立飞书群。"""
    e_set = monitor_rsi.DEFAULT_SETTINGS["etf"]
    batch = AlertBatch(webhook=webhook_for("etf_rsi"),
                       empty_note="所有 ETF RSI 均处于正常区间，不触发推送。")
    _scan_a_share(_enabled(config.get("etfs", [])), e_set, "ETF", batch)
    batch.flush()


def run_crypto_monitor(config: dict) -> None:
    """OKX 主流加密货币 RSI。"""
    c_set = monitor_rsi.DEFAULT_SETTINGS["crypto"]
    batch = AlertBatch(empty_note="所有加密货币 RSI 均处于正常区间，不触发推送。")
    scan_okx_symbols(config.get("crypto_okx", []), c_set, batch)
    batch.flush()


def run_onchain_token_monitor(config: dict) -> None:
    """链上代币 RSI；每只标的的 chain / timeframe 各自生效。"""
    t_set = monitor_rsi.DEFAULT_SETTINGS["token"]
    batch = AlertBatch(empty_note="所有链上代币 RSI 均处于正常区间，不触发推送。")
    scan_onchain_tokens(config.get("tokens", []), t_set, batch)
    batch.flush()

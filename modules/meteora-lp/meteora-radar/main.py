"""meteora-radar 主程序：Meteora meme 代币分层筛选 + 飞书提醒。

流程（方案第 2 节）::

    池子数据 → 第一层基本属性筛选 → 第二层 1H RSI<10 → 第三层 5m 止跌/突破 → 飞书提醒

只做筛选与提醒，不自动下单。默认运行一轮后退出（适合 cron / GitHub Actions），
加 ``--loop`` 则常驻按间隔轮询。
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config import LOG_DIR, LOG_FILE, load_config  # noqa: E402
from src import feishu  # noqa: E402
from src.candidate_store import CandidateStore  # noqa: E402
from src.indicators import analyze_5m, rsi  # noqa: E402
from src.market_data import fetch_ohlcv, meme_usd_price  # noqa: E402
from src.meteora_api import fetch_pools, sleep_between_requests, to_float  # noqa: E402
from src.token_filter import PASS, dedupe_by_meme_mint, screen_pools  # noqa: E402

LOGGER_NAME = "radar"


def setup_logging(verbose: bool = False, to_file: bool = True) -> logging.Logger:
    """控制台 + logs/radar.log 双写。"""
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                                  datefmt="%Y-%m-%d %H:%M:%S")

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    if to_file:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception as exc:  # noqa: BLE001
            logger.warning("无法写入日志文件 %s: %s", LOG_FILE, exc)
    return logger


# ===================== 信号后价格表现 =====================
def run_followups(cfg, store: CandidateStore, now: int, logger) -> int:
    """为到达 15m/1h/4h 回看时点的候选补记价格表现。"""
    pending = store.pending_followups(now=now, horizons=cfg.followup_horizons_minutes)
    for token, horizon, _signal_price in pending:
        record = store.get(token) or {}
        pool_address = record.get("pool_address")
        price = meme_usd_price(cfg, pool_address, logger=logger) if pool_address else None
        if price is not None:
            store.record_followup(token, horizon, price, now=now)
        sleep_between_requests(cfg)
    if pending:
        logger.info("回看价格表现：处理 %d 条", len(pending))
    return len(pending)


# ===================== 第二层：1H RSI =====================
def run_rsi_scan(cfg, passed_results, store: CandidateStore, now: int, logger) -> list[dict]:
    ranked = sorted(
        passed_results,
        key=lambda r: to_float((r.metrics or {}).get("volume_24h_usd")),
        reverse=True,
    )[: cfg.rsi_top_n]
    bars_needed = max(int(cfg.rsi_period) + 7, 30)
    offset = 2 if cfg.use_closed_bar else 1

    hits: list[dict] = []
    total = len(ranked)
    for index, result in enumerate(ranked, start=1):
        metrics = result.metrics or {}
        token = str(metrics.get("meme_mint") or "")
        pool_address = result.pool_address
        if not token or not pool_address:
            continue
        store.upsert_from_screen(result, now=now)

        closes = [c["close"] for c in fetch_ohlcv(
            cfg, pool_address, cfg.rsi_timeframe, bars_needed, logger=logger)]
        rsi_value = rsi(closes, cfg.rsi_period, offset=offset)
        price = metrics.get("meme_price_usd")
        is_hit = rsi_value is not None and rsi_value < cfg.rsi_1h_max
        store.mark_rsi(token, rsi_value, price, hit=is_hit, now=now)
        logger.info("[RSI %d/%d] %s %s=%s", index, total,
                    metrics.get("symbol") or pool_address[:8], cfg.rsi_timeframe,
                    "未知" if rsi_value is None else f"{rsi_value:.1f}")

        if is_hit:
            hits.append({
                "symbol": metrics.get("symbol"),
                "token_address": token,
                "pool_address": pool_address,
                "rsi": float(rsi_value),
                "price": price,
                "tvl": metrics.get("tvl"),
                "volume_24h_usd": metrics.get("volume_24h_usd"),
                "volume_tvl_ratio": metrics.get("volume_tvl_ratio"),
                "market_cap": metrics.get("market_cap"),
                "unknowns": list(result.unknowns or []),
            })
        sleep_between_requests(cfg)
    return hits


# ===================== 第三层：5m 止跌/反弹 =====================
def run_signal_scan(cfg, store: CandidateStore, now: int, logger) -> list[dict]:
    active = [
        record for record in store.all()
        if record.get("rsi_trigger_ts")
        and now - int(record.get("rsi_trigger_ts") or now) <= cfg.candidate_ttl_hours * 3600
        and record.get("pool_address")
    ]
    bars_needed = int(cfg.rebound_lookback_bars) + 6

    hits: list[dict] = []
    for record in active:
        token = record.get("token_address")
        pool_address = record.get("pool_address")
        candles = fetch_ohlcv(cfg, pool_address, cfg.signal_timeframe, bars_needed, logger=logger)
        analysis = analyze_5m(candles, cfg)
        store.mark_stabilized(token, analysis["stabilized"], now=now)

        rebound = analysis.get("rebound")
        logger.info("[5m] %s 止跌=%s 反弹=%s",
                    record.get("symbol") or str(token)[:8],
                    analysis["stabilized"], bool(rebound))
        if rebound:
            price = meme_usd_price(cfg, pool_address, logger=logger)
            store.mark_rebound(token, rebound, price, now=now)
            hits.append({
                "symbol": record.get("symbol"),
                "token_address": token,
                "pool_address": pool_address,
                "price": price,
                "breakout_close": rebound.get("breakout_close"),
                "prior_high": rebound.get("prior_high"),
                "volume_ratio": rebound.get("volume_ratio"),
                "volume_confirmed": rebound.get("volume_confirmed"),
            })
        sleep_between_requests(cfg)
    return hits


# ===================== 提醒分发（去重 + 冷却） =====================
def dispatch_alerts(cfg, store: CandidateStore, now: int,
                    rsi_hits: list[dict], rebound_hits: list[dict], logger) -> dict:
    result = {"rsi": 0, "rebound": 0}

    fresh_rsi = []
    for hit in rsi_hits:
        token = hit["token_address"]
        if not store.should_alert(token, "rsi", now=now,
                                  cooldown_minutes=cfg.rsi_alert_cooldown_minutes):
            continue
        # 首个信号出现时，记录后验表现的基准价
        store.mark_signal_baseline(token, hit.get("price"), now=now)
        store.mark_alert(token, "rsi", now=now)
        fresh_rsi.append(hit)
    if fresh_rsi:
        feishu.send(cfg, feishu.build_rsi_alert_message(fresh_rsi, cfg), logger=logger)
        result["rsi"] = len(fresh_rsi)

    fresh_rebound = []
    for hit in rebound_hits:
        token = hit["token_address"]
        if not store.should_alert(token, "rebound", now=now,
                                  cooldown_minutes=cfg.rebound_alert_cooldown_minutes):
            continue
        store.mark_signal_baseline(token, hit.get("price"), now=now)
        store.mark_alert(token, "rebound", now=now)
        fresh_rebound.append(hit)
    if fresh_rebound:
        feishu.send(cfg, feishu.build_rebound_alert_message(fresh_rebound, cfg), logger=logger)
        result["rebound"] = len(fresh_rebound)

    return result


# ===================== 单轮流程 =====================
def run_once(cfg, logger=None) -> dict:
    logger = logger or setup_logging()
    now = int(time.time())
    store = CandidateStore(cfg.candidates_file, logger=logger).load()

    summary = {"pools": 0, "pass": 0, "reject": 0, "review": 0,
               "rsi_hits": 0, "rebound_hits": 0, "pushed_rsi": 0, "pushed_rebound": 0}

    # 0) 先补记上一轮信号后的价格表现
    run_followups(cfg, store, now, logger)

    # 1) 拉取池子宇宙并按 meme 代币去重
    pools = fetch_pools(cfg, logger=logger)
    summary["pools"] = len(pools)
    if not pools:
        logger.warning("未获取到池子数据，本轮结束。")
        store.save()
        return summary
    pools = dedupe_by_meme_mint(pools)

    # 2) 第一层筛选
    results = screen_pools(pools, cfg, tvl_history=store.tvl_snapshot(), logger=logger, now=now)
    for status in ("pass", "reject", "review"):
        summary[status] = sum(1 for r in results if r.status.lower() == status)
    store.update_tvl_history(
        {r.pool_address: (r.metrics or {}).get("tvl")
         for r in results if (r.metrics or {}).get("tvl") is not None},
        now=now,
    )

    passed = [r for r in results if r.status == PASS]
    if not passed:
        logger.info("第一层无 PASS 池子，本轮结束。")
        store.prune(now=now, ttl_hours=cfg.candidate_ttl_hours)
        store.save()
        return summary

    # 3) 第二层 RSI
    rsi_hits = run_rsi_scan(cfg, passed, store, now, logger)
    summary["rsi_hits"] = len(rsi_hits)

    # 4) 第三层 5m 信号
    rebound_hits = run_signal_scan(cfg, store, now, logger)
    summary["rebound_hits"] = len(rebound_hits)

    # 5) 推送 + 持久化
    pushed = dispatch_alerts(cfg, store, now, rsi_hits, rebound_hits, logger)
    summary["pushed_rsi"] = pushed["rsi"]
    summary["pushed_rebound"] = pushed["rebound"]

    store.prune(now=now, ttl_hours=cfg.candidate_ttl_hours)
    store.save()
    logger.info(
        "本轮完成：池子 %(pools)d | PASS %(pass)d/REJECT %(reject)d/REVIEW %(review)d | "
        "RSI命中 %(rsi_hits)d(推送 %(pushed_rsi)d) | 反弹 %(rebound_hits)d(推送 %(pushed_rebound)d)"
        % summary,
    )
    return summary


# ===================== CLI =====================
def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Meteora meme 代币筛选与反弹监控")
    parser.add_argument("--loop", action="store_true", help="常驻轮询（默认只跑一轮后退出）")
    parser.add_argument("--interval", type=int, default=None, help="轮询间隔秒数（覆盖配置）")
    parser.add_argument("--dry-run", action="store_true", help="不发送飞书，仅打印")
    parser.add_argument("--verbose", action="store_true", help="输出 DEBUG 日志")
    parser.add_argument("--pool-pages", type=int, default=None, help="拉取池子的页数（覆盖配置）")
    parser.add_argument("--rsi-max", type=float, default=None, help="1H RSI 阈值（默认 10）")
    parser.add_argument("--rsi-top-n", type=int, default=None, help="只对成交量前 N 个池算 RSI")
    parser.add_argument("--require-volume", action="store_true", help="突破必须放量才提醒")
    parser.add_argument("--min-tvl", type=float, default=None, help="最低池子 TVL(USD)")
    parser.add_argument("--min-volume", type=float, default=None, help="最低 24h 成交量(USD)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    cfg = load_config(
        dry_run=args.dry_run or None,
        poll_interval_seconds=args.interval,
        pool_max_pages=args.pool_pages,
        rsi_1h_max=args.rsi_max,
        rsi_top_n=args.rsi_top_n,
        require_volume_confirmation=args.require_volume or None,
        min_pool_tvl_usd=args.min_tvl,
        min_volume_24h_usd=args.min_volume,
    )
    logger = setup_logging(verbose=args.verbose)
    logger.info("meteora-radar 启动 | RSI(%d,%s)<%g | TVL≥%s | 24h量≥%s | dry_run=%s",
                cfg.rsi_period, cfg.rsi_timeframe, cfg.rsi_1h_max,
                cfg.min_pool_tvl_usd, cfg.min_volume_24h_usd, cfg.dry_run)

    if not args.loop:
        run_once(cfg, logger)
        return 0

    logger.info("常驻模式：每 %d 秒运行一轮，Ctrl+C 退出。", cfg.poll_interval_seconds)
    while True:
        try:
            run_once(cfg, logger)
        except Exception as exc:  # noqa: BLE001 - 单轮异常不应终止常驻循环
            logger.exception("本轮执行异常: %s", exc)
        time.sleep(cfg.poll_interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())

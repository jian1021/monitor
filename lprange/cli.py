"""命令行入口：python -m lprange …"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from .config import Config, load_config
from . import backtest as bt
from .data import (GeckoClient, LPRangeError, OhlcvCache, fee_from_pool_name, fetch_meteora_bin_step,
                   fetch_meteora_pool_params, load_backtest_series, load_csv, load_ohlcv,
                   prepare_hourly, resolve_pool)
from .report import (BacktestReport, RunResult, backtest_rows, backtest_to_dict, batch_row, export,
                     render_backtest, render_batch, render_single, single_csv_rows, single_to_dict,
                     sort_rows, table)
from .stats import (DEFAULT_HOLDS, DEFAULT_MIN_HISTORY_MULT, DEFAULT_TARGETS, STATUS_INSUFFICIENT,
                    analyze, hold_label)
from .turso import TursoError, import_turso
from .watchlist import Pool, Watchlist, looks_like_address, parse_import_line

log = logging.getLogger("lprange")


# ================= 参数 =================
def _csv_list(cast):
    def parse(text: str):
        try:
            return [cast(x) for x in str(text).split(",") if x.strip()]
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"无法解析：{text}") from exc
    return parse


def _analysis_options() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    g = p.add_argument_group("统计参数")
    g.add_argument("--network", default="solana", help="GeckoTerminal network id（默认 solana）")
    g.add_argument("--holds", type=_csv_list(int), default=list(DEFAULT_HOLDS),
                   help="持有周期（小时），默认 168,336,504,720")
    g.add_argument("--targets", type=_csv_list(float), default=list(DEFAULT_TARGETS),
                   help="时间占比目标，默认 0.7,0.8,0.9")
    g.add_argument("--days", type=float, default=180, help="回看天数（默认 180）")
    g.add_argument("--currency", choices=["usd", "quote"], default="quote",
                   help="价格口径：quote = 池子自身比价（默认；接口转为 currency=token）；usd = 美元价")
    g.add_argument("--token", default="base",
                   help="base / quote / 代币地址：quote 口径下的比价方向（base = base/quote 比价）")
    g.add_argument("--bin-step", type=int, default=None, help="DLMM binStep；不传时 solana 池子尝试自动读取")
    g.add_argument("--no-auto-bin-step", action="store_true", help="不自动读取 Meteora binStep")
    g.add_argument("--max-bins", type=int, default=69, help="单个 position 的 bin 上限（默认 69）")
    g.add_argument("--min-history-mult", type=float, default=DEFAULT_MIN_HISTORY_MULT,
                   help="history_days < 持有天数 × 该倍数时判为样本不足（默认 3；"
                        "改回 1.5 即第 7 节原规则，此时会出现「仅供参考」）")
    g.add_argument("--refresh", action="store_true", help="强制重拉，忽略已有缓存")
    g.add_argument("--no-cache", action="store_true", help="不读写本地 K 线缓存")
    g.add_argument("--interval", type=float, default=2.5, help="请求间隔秒数（默认 2.5）")
    g.add_argument("--out", help="导出到 .csv 或 .json")
    return p


def _common(suppress: bool) -> argparse.ArgumentParser:
    """全局参数既能写在子命令前也能写在后面；子命令里用 SUPPRESS，避免覆盖前面已给的值。"""
    kw = {"default": argparse.SUPPRESS} if suppress else {}
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("-v", "--verbose", action="store_true", help="DEBUG 日志", **kw)
    p.add_argument("--config", help="config.toml 路径（默认 lprange/config.toml）", **kw)
    p.add_argument("--db", help="watchlist / 缓存 SQLite 路径（覆盖配置）", **kw)
    return p


def build_parser() -> argparse.ArgumentParser:
    top = _common(suppress=False)
    common = _common(suppress=True)

    parser = argparse.ArgumentParser(
        prog="lprange", parents=[top],
        description="DEX 流动性池区间宽度统计：持有 H 小时、区间不调整时，价格在区间内的时间占比。")
    sub = parser.add_subparsers(dest="cmd")
    opts = _analysis_options()

    p = sub.add_parser("run", parents=[common, opts], help="分析单个池子")
    p.add_argument("target", nargs="?", help="池子/代币地址、watchlist 序号或名称关键字")
    p.add_argument("--csv", help="用现成 K 线 CSV（需含 timestamp/high/low/close）")

    p = sub.add_parser("backtest", parents=[common], help="策略回测：(宽度 × 调仓规则) 网格逐小时模拟")
    p.add_argument("target", nargs="?", help="池子/代币地址、watchlist 序号或名称关键字")
    p.add_argument("--csv", help="现成 K 线 CSV（需含 timestamp/close/volume，价格须为池子比价）")
    p.add_argument("--network", default="solana")
    p.add_argument("--days", type=float, default=180)
    p.add_argument("--holds", type=_csv_list(int), default=[168], help="H（小时），默认 168")
    p.add_argument("--widths", type=_csv_list(float), default=list(bt.DEFAULT_FIXED_WIDTHS),
                   help="固定半宽网格，默认 0.02,0.03,0.05,0.08,0.12,0.18,0.25,0.35,0.5")
    p.add_argument("--ks", type=_csv_list(float), default=list(bt.DEFAULT_KS),
                   help="波动率定宽 k 网格，默认 1.0~2.5 每 0.25")
    p.add_argument("--rules", type=_csv_list(str), default=list(bt.DEFAULT_RULES),
                   help="调仓规则：hold（到期调仓）/ out:N（连续出界 N 小时调仓），默认 hold,out:1,out:6,out:24")
    p.add_argument("--fee", type=float, help="池子费率（小数，0.003 = 0.3%%）；不传时自动读取")
    p.add_argument("--tvl", type=float, help="池子 TVL（美元）；不传时用 GeckoTerminal 当前值")
    p.add_argument("--liq-width", type=float, default=bt.DEFAULT_LIQ_WIDTH,
                   help="其他 LP 的流动性等效铺开的对数宽度（决定手续费绝对水平），默认 0.5")
    p.add_argument("--rebalance-extra", type=float, default=bt.DEFAULT_REBALANCE_EXTRA,
                   help="每次调仓的额外成本（滑点 / gas，占仓位价值），默认 0.002；另加 0.5 × 费率")
    p.add_argument("--halflife", type=float, default=bt.DEFAULT_HALFLIFE, help="σ_EWMA 半衰期（小时），默认 72")
    p.add_argument("--token", default="base", help="base / quote：价格方向")
    p.add_argument("--top", type=int, default=15, help="终端显示前 N 个组合（导出为全部）")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--interval", type=float, default=2.5)
    p.add_argument("--out", help="导出到 .csv 或 .json（全部组合）")

    p = sub.add_parser("all", parents=[common, opts], help="对 watchlist 全部池子批量运行")
    p.add_argument("--sort", help="按列排序，如 1m、history_days、1w_bins（降序加 --desc，或写成 --sort=-1m）")
    p.add_argument("--desc", action="store_true", help="降序排序")
    p.add_argument("--tag", help="只跑名称/备注包含该关键字的池子")
    p.add_argument("--source", choices=["manual", "query", "turso"])
    p.add_argument("--sync-turso", action="store_true", help="运行前先从 Turso 同步一次")

    sub.add_parser("list", parents=[common], help="列出 watchlist")
    p = sub.add_parser("add", parents=[common], help="手动加入池子")
    p.add_argument("address")
    p.add_argument("--network", default="solana")
    p.add_argument("--name")
    p.add_argument("--note")
    p = sub.add_parser("rm", parents=[common], help="删除（序号/地址/关键字）")
    p.add_argument("selector")
    p = sub.add_parser("note", parents=[common], help="设置备注")
    p.add_argument("selector")
    p.add_argument("text", nargs="?", default="")
    p = sub.add_parser("import-file", parents=[common], help="从文本文件批量导入（每行一个地址，可带 network）")
    p.add_argument("path")
    p.add_argument("--network", default="solana")
    p = sub.add_parser("import-turso", parents=[common], help="从 Turso 导入监控池子")
    p.add_argument("--dry-run", action="store_true", help="只打印将导入的行")
    return parser


# ================= 核心：单个池子 =================
def _pick(matches: list[Pool], interactive: bool, selector: str) -> Pool:
    if not matches:
        raise LPRangeError(f"watchlist 里没有匹配「{selector}」的池子（用 list 查看，或直接传地址）")
    if len(matches) == 1:
        return matches[0]
    lines = [f"  {i}. {p.label}  [{p.network}] {p.address}" for i, p in enumerate(matches, 1)]
    if not interactive:
        raise LPRangeError("匹配到多个池子，请写得更具体：\n" + "\n".join(lines))
    print("匹配到多个池子：\n" + "\n".join(lines))
    choice = input("输入序号：").strip()
    if not choice.isdigit() or not 1 <= int(choice) <= len(matches):
        raise LPRangeError("已取消")
    return matches[int(choice) - 1]


def run_one(args, cfg: Config, wl: Watchlist, target: str | None, network: str | None = None,
            interactive: bool = False, client: GeckoClient | None = None) -> RunResult:
    targets = sorted({float(t) for t in args.targets})
    holds = [int(h) for h in args.holds]
    now = int(time.time())
    warnings: list[str] = []
    bin_step = args.bin_step

    if getattr(args, "csv", None):
        raw = load_csv(args.csv)
        prep = prepare_hourly(raw)
        pool = {"name": os.path.basename(args.csv), "network": "csv", "address": os.path.abspath(args.csv)}
        fetch_note = f"数据来源：CSV（{len(raw)} 行）"
        requested = None
    else:
        if not target:
            raise LPRangeError("请提供池子/代币地址、watchlist 序号或名称关键字")
        net = network or args.network
        if looks_like_address(target):
            address = target.strip()
        else:
            picked = _pick(wl.match(target), interactive, target)
            address, net = picked.address, picked.network
        client = client or GeckoClient(interval=args.interval)
        info, from_token = resolve_pool(client, net, address)
        pool = {**info, "network": net, "from_token": from_token}
        address = info["address"] or address

        cache = None if args.no_cache else OhlcvCache(cfg.db_path)
        try:
            raw, rep = load_ohlcv(client, net, address, args.days, currency=args.currency, token=args.token,
                                  cache=cache, refresh=args.refresh, now=now)
        except LPRangeError as exc:
            hint = "（已拉取的部分已写入缓存，下次只补缺失部分）" if cache is not None else ""
            raise LPRangeError(f"{exc}{hint}") from exc
        fetch_note = (f"数据：本次请求 {rep.requests} 次，接口返回 {rep.new_rows} 根"
                      + (f"，缓存命中 {rep.from_cache} 根" if cache is not None else "，未使用缓存"))
        prep = prepare_hourly(raw, extend_to=now)
        requested = args.days
        if prep.history_days < args.days - 1:
            reason = "接口已无更早数据（池子较新或超出免费接口回溯范围）" if rep.exhausted else "接口未返回更多数据"
            warnings.append(f"⚠️ 回溯不足：请求 {args.days:g} 天，实际只拿到 {prep.history_days:.1f} 天，{reason}。")
        if bin_step is None and not args.no_auto_bin_step and net == "solana":
            bin_step = fetch_meteora_bin_step(address)
            if bin_step:
                log.info("自动读取 Meteora binStep=%s", bin_step)
        wl.upsert(net, address, name=info.get("name"), source="query", touch=True)

    if prep.dropped_bad:
        warnings.append(f"已剔除 {prep.dropped_bad} 根价格为 0 / NaN 的 K 线（计入 gap）。")
    if prep.gap_ratio > 0.2:
        warnings.append(f"⚠️ gap_ratio={prep.gap_ratio:.0%}：成交稀疏，补齐的小时价格不动，结果会偏窄。")
    results = analyze(prep.close, holds=holds, targets=targets, history_days=prep.history_days,
                      gap_ratio=prep.gap_ratio, min_history_mult=args.min_history_mult)
    if any(h.status == STATUS_INSUFFICIENT for h in results):
        bad = "、".join(h.label for h in results if h.status == STATUS_INSUFFICIENT)
        warnings.append(f"{bad}：history_days < 持有天数 × {args.min_history_mult:g}，样本不足，不给推荐宽度。")
    return RunResult(pool=pool, holds=results, targets=targets, history_days=prep.history_days,
                     gap_ratio=prep.gap_ratio, requested_days=requested,
                     current_price=float(prep.close[-1]) if prep.close.size else None,
                     bin_step=bin_step, max_bins=args.max_bins, fetch_note=fetch_note, warnings=warnings)


# ================= 回测 =================
def _resolve_fee(args, info: dict, network: str, address: str) -> tuple[float, str]:
    if args.fee is not None:
        return float(args.fee), "参数 --fee"
    if network == "solana":
        fee_pct = fetch_meteora_pool_params(address).get("base_fee_pct")
        if fee_pct:
            return float(fee_pct) / 100, "Meteora 基础费率（不含动态费，偏保守）"
    fee = fee_from_pool_name(info.get("name"))
    if fee:
        return fee, "池子名称里的费率档"
    raise LPRangeError("读不到池子费率，请用 --fee 指定（小数，如 0.003 表示 0.3%）")


def run_backtest(args, cfg: Config, wl: Watchlist, target: str | None, network: str | None = None,
                 interactive: bool = False, client: GeckoClient | None = None) -> BacktestReport:
    notes: list[str] = []
    if getattr(args, "csv", None):
        raw = load_csv(args.csv)
        if raw["volume"].isna().all():
            raise LPRangeError("回测需要成交量：CSV 里缺少 volume 列")
        if args.fee is None or args.tvl is None:
            raise LPRangeError("CSV 回测需要同时指定 --fee 和 --tvl")
        prep = prepare_hourly(raw)
        ts, close = prep.ts, prep.close
        volume = prep.frame["volume"].to_numpy(float)
        gap, hist = prep.gap_ratio, prep.history_days
        pool = {"name": os.path.basename(args.csv), "network": "csv", "address": os.path.abspath(args.csv)}
        fee, fee_src, tvl, tvl_src = float(args.fee), "参数 --fee", float(args.tvl), "参数 --tvl"
        notes.append("CSV 模式：请确认 close 是池子自身比价（不是美元价），volume 是美元口径。")
    else:
        if not target:
            raise LPRangeError("请提供池子/代币地址、watchlist 序号或名称关键字")
        net = network or args.network
        if looks_like_address(target):
            address = target.strip()
        else:
            picked = _pick(wl.match(target), interactive, target)
            address, net = picked.address, picked.network
        client = client or GeckoClient(interval=args.interval)
        info, from_token = resolve_pool(client, net, address)
        address = info["address"] or address
        pool = {**info, "network": net, "from_token": from_token}
        cache = None if args.no_cache else OhlcvCache(cfg.db_path)
        data = load_backtest_series(client, net, address, args.days, token=args.token, cache=cache,
                                    refresh=args.refresh)
        ts, close, volume = data.ts, data.close, data.volume_usd
        gap, hist = data.gap_ratio, data.history_days
        fee, fee_src = _resolve_fee(args, info, net, address)
        if args.tvl is not None:
            tvl, tvl_src = float(args.tvl), "参数 --tvl"
        elif info.get("reserve_usd"):
            tvl, tvl_src = float(info["reserve_usd"]), "GeckoTerminal 当前 TVL（整段历史都按当前值，偏差见说明）"
        else:
            raise LPRangeError("读不到池子 TVL，请用 --tvl 指定（美元）")
        wl.upsert(net, address, name=info.get("name"), source="query", touch=True)
        if hist < args.days - 1:
            notes.append(f"⚠️ 回溯不足：请求 {args.days:g} 天，实际 {hist:.1f} 天。")
    if gap > 0.2:
        notes.append(f"⚠️ gap_ratio={gap:.0%}：成交稀疏，补齐的小时没有成交量也没有手续费。")
    res = bt.run_grid(close, volume, fee_rate=fee, tvl_usd=tvl, holds=args.holds,
                      fixed_widths=args.widths, ks=args.ks, rules=args.rules, halflife=args.halflife,
                      liq_width=args.liq_width, rebalance_extra=args.rebalance_extra, ts=ts)
    for h in args.holds:
        windows = res.hours / int(h)
        if windows < 3:
            notes.append(f"⚠️ H={h}h 只有 {windows:.1f} 个独立窗口，排名主要由少数几段行情决定。")
    return BacktestReport(pool=pool, result=res, fee_source=fee_src, tvl_source=tvl_src,
                          history_days=hist, gap_ratio=gap, notes=notes)


def cmd_backtest(args, cfg, wl) -> int:
    if not args.target and not args.csv:
        raise LPRangeError("请提供池子地址 / 序号 / 关键字，或 --csv")
    rep = run_backtest(args, cfg, wl, args.target, interactive=sys.stdin.isatty())
    print(render_backtest(rep, top=args.top))
    if args.out:
        export(args.out, backtest_to_dict(rep), backtest_rows(rep))
        print(f"\n已导出：{args.out}")
    return 0


# ================= 子命令 =================
def cmd_run(args, cfg, wl, interactive=False) -> int:
    result = run_one(args, cfg, wl, args.target, interactive=interactive or sys.stdin.isatty())
    print(render_single(result))
    if args.out:
        export(args.out, single_to_dict(result), single_csv_rows(result))
        print(f"\n已导出：{args.out}")
    return 0


def _sync_turso(cfg, wl, dry_run=False) -> None:
    try:
        rows, added, notes = import_turso(cfg, wl, dry_run=dry_run)
    except TursoError as exc:
        print(f"❌ Turso 导入失败：{exc}（本地 watchlist 不受影响）")
        return
    for n in notes:
        print(f"  · {n}")
    if dry_run:
        print(f"[dry-run] 将导入 {len(rows)} 个池子：")
        for r in rows:
            print(f"  [{r['network']}] {r['address']}  {r['name'] or ''}  ← {r['from']}")
    else:
        print(f"Turso 同步完成：读取 {len(rows)} 个，新增 {added} 个。")


def cmd_all(args, cfg, wl) -> int:
    if args.sync_turso or cfg.sync_turso_before_all:
        _sync_turso(cfg, wl)
    pools = wl.list(source=args.source, tag=args.tag)
    if not pools:
        print("watchlist 为空（或筛选后没有池子）。")
        return 1
    client = GeckoClient(interval=args.interval)
    rows, payloads, failures = [], [], []
    for i, p in enumerate(pools, 1):
        print(f"[{i}/{len(pools)}] {p.label} [{p.network}] …", file=sys.stderr)
        try:
            r = run_one(args, cfg, wl, p.address, network=p.network, client=client)
        except LPRangeError as exc:
            failures.append((p.label, str(exc)))
            continue
        except Exception as exc:  # noqa: BLE001  单个池子失败不影响其他池子
            log.debug("池子 %s 失败", p.address, exc_info=True)
            failures.append((p.label, f"{type(exc).__name__}: {exc}"))
            continue
        row = batch_row(r)
        row["地址"] = p.address
        rows.append(row)
        payloads.append(single_to_dict(r))
    try:
        sort = ("-" + args.sort.lstrip("-")) if (args.sort and args.desc) else args.sort
        rows = sort_rows(rows, sort)
    except ValueError as exc:
        print(f"⚠️ {exc}")
    print(render_batch(rows, [hold_label(h) for h in args.holds], failures))
    if args.out:
        export(args.out, {"pools": payloads, "failures": failures}, rows)
        print(f"\n已导出：{args.out}")
    return 0 if rows else 1


def _print_list(wl: Watchlist) -> list[Pool]:
    pools = wl.list()
    if not pools:
        print("watchlist 为空。用 `add <地址>`、`import-file`、`import-turso`，或直接 `run <地址>` 自动记录。")
        return pools
    print(table(["#", "名称", "network", "地址", "来源", "使用次数", "最近使用", "备注"],
                [[i, p.name or "—", p.network, p.address, p.source, p.use_count, p.last_used or "—",
                  p.note or ""] for i, p in enumerate(pools, 1)]))
    return pools


def cmd_interactive(cfg, wl, parser) -> int:
    pools = _print_list(wl)
    prompt = "输入序号 / 关键字 / 地址运行（回车退出）：" if pools else "输入池子或代币地址运行（回车退出）："
    try:
        text = input(prompt).strip()
    except EOFError:
        return 0
    if not text:
        return 0
    args = parser.parse_args(["run", text])
    return cmd_run(args, cfg, wl, interactive=True)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s" if args.verbose else "%(message)s")
    try:
        cfg = load_config(args.config, args.db)
        wl = Watchlist(cfg.db_path)
        if args.cmd is None:
            return cmd_interactive(cfg, wl, parser)
        if args.cmd == "run":
            if not args.target and not args.csv:
                return cmd_interactive(cfg, wl, parser)
            return cmd_run(args, cfg, wl)
        if args.cmd == "all":
            return cmd_all(args, cfg, wl)
        if args.cmd == "backtest":
            return cmd_backtest(args, cfg, wl)
        if args.cmd == "list":
            _print_list(wl)
            return 0
        if args.cmd == "add":
            if not looks_like_address(args.address):
                raise LPRangeError(f"不像合法地址：{args.address}")
            new = wl.upsert(args.network, args.address, name=args.name, note=args.note, source="manual")
            print("已添加" if new else "已存在（未覆盖已有名称/备注）")
            return 0
        if args.cmd in ("rm", "note"):
            pool = _pick(wl.match(args.selector), sys.stdin.isatty(), args.selector)
            if args.cmd == "rm":
                wl.remove(pool)
                print(f"已删除：{pool.label} [{pool.network}] {pool.address}")
            else:
                wl.set_note(pool, args.text)
                print(f"已更新备注：{pool.label} → {args.text or '（清空）'}")
            return 0
        if args.cmd == "import-file":
            try:
                lines = open(args.path, encoding="utf-8").read().splitlines()
            except FileNotFoundError as exc:
                raise LPRangeError(f"找不到文件：{args.path}") from exc
            added = skipped = 0
            for line in lines:
                parsed = parse_import_line(line, args.network)
                if parsed is None:
                    skipped += bool(line.split("#", 1)[0].strip())
                    continue
                added += wl.upsert(parsed[0], parsed[1], source="manual")
            print(f"导入完成：新增 {added} 个，无法识别 {skipped} 行。")
            return 0
        if args.cmd == "import-turso":
            _sync_turso(cfg, wl, dry_run=args.dry_run)
            return 0
    except LPRangeError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    parser.print_help()
    return 0

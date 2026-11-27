"""终端表格输出与 CSV / JSON 导出。"""

from __future__ import annotations

import csv
import json
import math
import unicodedata
from dataclasses import dataclass, field

from .stats import (STATUS_INSUFFICIENT, STATUS_REFERENCE, HoldResult, bin_counts,
                    log_width, tilt_label)

DISCLAIMER = "⚠️ 以上均为历史统计（假设开仓后区间不调整），不代表未来走势，也不含手续费与无常损失。"


@dataclass
class RunResult:
    pool: dict
    holds: list[HoldResult]
    targets: list[float]
    history_days: float
    gap_ratio: float
    requested_days: float | None
    current_price: float | None
    bin_step: int | None = None
    max_bins: int = 69
    fetch_note: str = ""
    warnings: list[str] = field(default_factory=list)


# ================= 格式化工具 =================
def pct(v: float | None, digits: int = 3) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{v * 100:.{digits}g}%"


def _w(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in str(text))


def table(headers: list[str], rows: list[list], title: str | None = None) -> str:
    cells = [[str(h) for h in headers]] + [["" if c is None else str(c) for c in r] for r in rows]
    widths = [max(_w(row[i]) for row in cells) for i in range(len(headers))]

    def line(row):
        out = []
        for i, c in enumerate(row):
            pad = widths[i] - _w(c)
            out.append(c + " " * pad if i == 0 else " " * pad + c)
        return "│ " + " │ ".join(out) + " │"

    sep = lambda l, m, r: l + m.join("─" * (w + 2) for w in widths) + r  # noqa: E731
    parts = []
    if title:
        parts.append(f"\n{title}")
    parts += [sep("┌", "┬", "┐"), line(cells[0]), sep("├", "┼", "┤")]
    parts += [line(r) for r in cells[1:]]
    parts.append(sep("└", "┴", "┘"))
    return "\n".join(parts)


def _skew_text(du: tuple[float, float] | None) -> str:
    if not du:
        return ">80%"
    d, u = du
    ratio = f"{u / d:.2g}" if d > 0 else "∞"
    return f"−{pct(d)} / +{pct(u)}（上:下 {ratio}）"


def _bins_text(w: float | None, bin_step: int, max_bins: int) -> str:
    if w is None:
        return "—"
    b = bin_counts(w, w, bin_step, max_bins)
    s = f"{b['bins_total']}（↓{b['bins_down']} ↑{b['bins_up']}）"
    return s + (f" ⚠拆{b['positions']}个" if b["positions"] > 1 else "")


def _status_of(h: HoldResult) -> str:
    return h.status if h.status in (STATUS_INSUFFICIENT, STATUS_REFERENCE) else ""


# ================= 单池子报告 =================
def render_single(r: RunResult) -> str:
    p = r.pool
    out = []
    head = f"池子：{p.get('name') or '—'}   network={p.get('network')}   地址={p.get('address')}"
    if p.get("dex"):
        head += f"   dex={p['dex']}"
    out.append(head)
    out.append(f"当前价：{r.current_price:.10g}（最近 1h 收盘）" if r.current_price else "当前价：—")
    if p.get("from_token"):
        out.append(f"（输入的是代币地址，已自动选用流动性最大的池子 {p.get('address')}）")
    if r.fetch_note:
        out.append(r.fetch_note)

    holds = r.holds
    # ---- 表一 ----
    headers = ["宽度 ±"] + [f"{h.label} 中位/p10" for h in holds]
    widths = holds[0].widths
    rows = []
    for i, w in enumerate(widths):
        row = [pct(w)]
        for h in holds:
            row.append("—" if h.summary is None else
                       f"{pct(h.summary['median'][i])} / {pct(h.summary['p10'][i])}")
        rows.append(row)
    out.append(table(headers, rows, "表一 · 对称宽度 → 价格在区间内的时间占比（中位数 / p10）"))

    # ---- 表二 ----
    headers = ["周期"] + [f"T={pct(t)} 典型/保守" for t in r.targets]
    rows = []
    for h in holds:
        if not h.has_recommendation:
            rows.append([h.label] + [STATUS_INSUFFICIENT] * len(r.targets))
            continue
        row = [h.label + (f"（{h.status}）" if h.status == STATUS_REFERENCE else "")]
        for t in r.targets:
            rec = h.recommend.get(t) or {}
            row.append(f"±{pct(rec.get('typical'))} / ±{pct(rec.get('conservative'))}"
                       .replace("±—", ">80%"))
        rows.append(row)
    out.append(table(headers, rows, "表二 · 推荐对称宽度（典型 = 中位数达标；保守 = p10 达标，即 90% 历史样本达标）"))

    headers = ["周期", "档"] + [f"T={pct(t)}" for t in r.targets]
    rows = []
    for h in holds:
        if not h.has_recommendation:
            rows.append([h.label, "—"] + [STATUS_INSUFFICIENT] * len(r.targets))
            continue
        for kind, name in (("typical", "典型"), ("conservative", "保守")):
            rows.append([h.label if kind == "typical" else "", name]
                        + [_skew_text((h.skew.get(t) or {}).get(kind)) for t in r.targets])
    out.append(table(headers, rows, "表二-B · 偏斜区间（总宽度 ln(1+u) − ln(1−d) 最小）"))

    # ---- 表三 ----
    if r.bin_step:
        headers = ["周期"] + [f"T={pct(t)} 典型" for t in r.targets] + [f"T={pct(t)} 保守" for t in r.targets]
        rows = []
        for h in holds:
            if not h.has_recommendation:
                rows.append([h.label] + [STATUS_INSUFFICIENT] * (2 * len(r.targets)))
                continue
            rows.append([h.label]
                        + [_bins_text((h.recommend.get(t) or {}).get("typical"), r.bin_step, r.max_bins)
                           for t in r.targets]
                        + [_bins_text((h.recommend.get(t) or {}).get("conservative"), r.bin_step, r.max_bins)
                           for t in r.targets])
        out.append(table(headers, rows, f"表三 · bin 数（binStep={r.bin_step}，单个 position 上限 "
                                        f"{r.max_bins} bins；超出需拆成多个 position 或换更大的 binStep）"))

    # ---- 表四 ----
    headers = ["周期", "宽度", "向上出界", "向下出界", "平均出界次数", "平均回归(h)", "永不回归", "出过界的样本"]
    rows = []
    for h in holds:
        e = h.exits
        if not e:
            rows.append([h.label, "—" if h.has_recommendation else STATUS_INSUFFICIENT] + ["—"] * 6)
            continue
        rows.append([h.label, f"±{pct(h.exit_width)}", pct(e["up_share"]), pct(e["down_share"]),
                     f"{e['avg_exits']:.2f}",
                     "—" if e["avg_return_hours"] is None else f"{e['avg_return_hours']:.1f}",
                     pct(e["never_return_ratio"]), pct(e["exited_sample_ratio"])])
    out.append(table(headers, rows, "表四 · 出界行为（用 T=80% 典型档对称宽度）"))

    # ---- 数据质量 ----
    headers = ["周期", "history_days", "独立窗口", "样本数(重叠)", "gap_ratio", "可信度"]
    rows = [[h.label, f"{h.history_days:.1f}", f"{h.independent_windows:.1f}", h.n_samples,
             pct(h.gap_ratio), h.status if h.status != "ok" else "正常"] for h in holds]
    out.append(table(headers, rows, "数据质量"))
    for w in r.warnings:
        out.append(w)
    out.append(DISCLAIMER)
    return "\n".join(out)


# ================= 监控列表摘要 =================
def list_summary(r: RunResult, target: float = 0.8) -> dict:
    """一次分析的摘要（存进 watchlist.results，供监控列表展示）。"""
    if target not in r.targets and r.targets:
        target = r.targets[len(r.targets) // 2]
    holds = {}
    for h in r.holds:
        rec = (h.recommend.get(target) or {}) if h.has_recommendation else {}
        typical, conservative = rec.get("typical"), rec.get("conservative")
        bins = bin_counts(typical, typical, r.bin_step, r.max_bins) if (r.bin_step and typical is not None) else None
        e = h.exits or {}
        holds[h.label] = {
            "status": h.status, "typical": typical, "conservative": conservative,
            "bins": bins["bins_total"] if bins else None, "positions": bins["positions"] if bins else None,
            "tilt": tilt_label(e.get("up_share")), "never_return": e.get("never_return_ratio"),
            "independent_windows": round(h.independent_windows, 1),
        }
    return {"target": target, "name": r.pool.get("name"), "price": r.current_price,
            "history_days": round(r.history_days, 1), "gap_ratio": round(r.gap_ratio, 4),
            "bin_step": r.bin_step, "holds": holds, "warnings": r.warnings}


# ================= 批量 =================
def batch_row(r: RunResult, target: float = 0.8) -> dict:
    row = {"名称": r.pool.get("name") or "—", "network": r.pool.get("network"),
           "地址": r.pool.get("address")}
    tilt_source = None
    for h in r.holds:
        w = (h.recommend.get(target) or {}).get("typical") if h.has_recommendation else None
        row[h.label] = w if h.has_recommendation else STATUS_INSUFFICIENT
        if r.bin_step:
            row[f"{h.label}_bins"] = (bin_counts(w, w, r.bin_step, r.max_bins)["bins_total"]
                                      if w is not None else None)
        if tilt_source is None and h.exits:
            tilt_source = h.exits
    longest = r.holds[-1] if r.holds else None
    row["history_days"] = round(r.history_days, 1)
    row["独立窗口"] = round(longest.independent_windows, 1) if longest else None
    row["出界倾向"] = tilt_label((tilt_source or {}).get("up_share"))
    row["binStep"] = r.bin_step
    return row


def _sort_key(value):
    if isinstance(value, (int, float)) and not (isinstance(value, float) and math.isnan(value)):
        return (0, value)
    if value in (None, "—", STATUS_INSUFFICIENT):
        return (2, 0)
    return (1, str(value))


def sort_rows(rows: list[dict], sort: str | None) -> list[dict]:
    if not sort:
        return rows
    desc = sort.startswith("-")
    key = sort.lstrip("-")
    aliases = {"history": "history_days", "name": "名称", "窗口": "独立窗口", "tilt": "出界倾向"}
    key = aliases.get(key, key)
    if rows and key not in rows[0]:
        raise ValueError(f"无法按 {sort} 排序，可选列：{', '.join(rows[0].keys())}")
    present = [r for r in rows if _sort_key(r.get(key))[0] < 2]
    missing = [r for r in rows if _sort_key(r.get(key))[0] == 2]
    present.sort(key=lambda r: _sort_key(r.get(key)), reverse=desc)
    return present + missing


def render_batch(rows: list[dict], hold_labels: list[str], failures: list[tuple[str, str]]) -> str:
    if not rows:
        body = "（没有成功的池子）"
    else:
        cols = list(rows[0].keys())
        show = [c for c in cols if c not in ("地址", "binStep")]
        fmt = []
        for r in rows:
            line = []
            for c in show:
                v = r.get(c)
                if c in hold_labels and isinstance(v, float):
                    line.append(f"±{pct(v)}")
                elif c in hold_labels and v is None:
                    line.append(">80%")
                elif v is None:
                    line.append("—")
                else:
                    line.append(v)
            fmt.append(line)
        body = table([("80%典型 " + c) if c in hold_labels else c for c in show], fmt,
                     "批量汇总 · 各周期 T=80% 典型档所需对称宽度")
    out = [body]
    if failures:
        out.append(f"\n失败 {len(failures)} 个：")
        out += [f"  - {name}：{reason}" for name, reason in failures]
    out.append(DISCLAIMER)
    return "\n".join(out)


# ================= 导出 =================
def _clean(v):
    if isinstance(v, float) and math.isnan(v):
        return None
    if hasattr(v, "item"):
        return v.item()
    return v


def single_to_dict(r: RunResult) -> dict:
    holds = []
    for h in r.holds:
        table_rows = []
        if h.summary is not None:
            for i, w in enumerate(h.widths):
                item = {"width": float(w), "mean": _clean(h.summary["mean"][i]),
                        "median": _clean(h.summary["median"][i]), "p10": _clean(h.summary["p10"][i])}
                for t, arr in h.summary["frac_ge"].items():
                    item[f"frac_ge_{int(round(t * 100))}"] = _clean(arr[i])
                table_rows.append(item)
        holds.append({
            "hold_hours": h.hold, "label": h.label, "status": h.status, "n_samples": h.n_samples,
            "history_days": round(h.history_days, 2), "independent_windows": round(h.independent_windows, 2),
            "gap_ratio": round(h.gap_ratio, 4),
            "recommend": {str(t): v for t, v in h.recommend.items()},
            "skew": {str(t): {k: (None if du is None else {"d": du[0], "u": du[1],
                                                           "log_width": log_width(*du)})
                              for k, du in v.items()} for t, v in h.skew.items()},
            "bins": ({str(t): {k: (bin_counts(w, w, r.bin_step, r.max_bins) if w is not None else None)
                               for k, w in v.items()} for t, v in h.recommend.items()}
                     if r.bin_step else None),
            "exit_width": h.exit_width, "exits": h.exits, "width_table": table_rows,
        })
    return {"pool": r.pool, "current_price": r.current_price, "history_days": round(r.history_days, 2),
            "requested_days": r.requested_days, "gap_ratio": round(r.gap_ratio, 4), "bin_step": r.bin_step,
            "max_bins": r.max_bins, "targets": r.targets, "warnings": r.warnings, "holds": holds,
            "disclaimer": DISCLAIMER}


def export(path: str, payload, rows_for_csv: list[dict]) -> None:
    if path.lower().endswith(".json"):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=_clean)
        return
    if not path.lower().endswith(".csv"):
        raise ValueError("--out 只支持 .csv 或 .json")
    keys: list[str] = []
    for row in rows_for_csv:
        keys += [k for k in row if k not in keys]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for row in rows_for_csv:
            w.writerow({k: _clean(v) for k, v in row.items()})


def single_csv_rows(r: RunResult) -> list[dict]:
    rows = []
    for h in single_to_dict(r)["holds"]:
        for item in h["width_table"]:
            rows.append({"hold": h["label"], "status": h["status"], **item})
    return rows

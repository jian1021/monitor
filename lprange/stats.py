"""核心统计：纯函数，只依赖 numpy，便于单元测试。

口径（见 README「统计口径」）：
- 每根 K 线 i 都是一个开仓点，开仓价 P = close[i]，持有窗口为 close[i+1 … i+H]；
- 区间 [P×(1−d), P×(1+u)]，边界含；
- time_in_range = 窗口内 close 落在区间内的 K 线占比（出界后回来，回来之后的时间继续计入）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

DEFAULT_HOLDS: tuple[int, ...] = (168, 336, 504, 720)
DEFAULT_TARGETS: tuple[float, ...] = (0.7, 0.8, 0.9)
HOLD_LABELS = {168: "1w", 336: "2w", 504: "3w", 720: "1m"}

# 边界比较的相对容差：避免 P×1.01 与 1.01×P 的浮点误差把「正好在边界上」判成出界
EPS = 1e-9

STATUS_OK = "ok"
STATUS_REFERENCE = "仅供参考"
STATUS_INSUFFICIENT = "样本不足"

# history_days < 持有天数 × 该倍数 → 样本不足。第 7 节原写 1.5；按验收第 7 条（不足 90 天时
# 1 个月一档必须显示样本不足）改为 3。注意：倍数 ≥ 3 时独立窗口必然 ≥ 3，「仅供参考」不会再出现。
DEFAULT_MIN_HISTORY_MULT = 3.0


def hold_label(hours: int) -> str:
    return HOLD_LABELS.get(int(hours), f"{int(hours)}h")


def width_grid(n: int = 40, lo: float = 0.01, hi: float = 0.80) -> np.ndarray:
    """候选宽度：1%~80% 几何间距约 n 档，按 0.01% 取整。"""
    raw = np.geomspace(lo, hi, n)
    return np.round(np.unique(np.round(raw * 100, 2)) / 100, 6)


# ================= 样本构造 =================
def build_ratios(close: np.ndarray, hold: int) -> np.ndarray:
    """返回 (样本数, H) 的价格比矩阵：R[i, j] = close[i+1+j] / close[i]。

    窗口不足 H 根的开仓点丢弃；样本数 = n − H（n ≤ H 时为 0）。
    """
    close = np.asarray(close, dtype=float)
    n = close.size
    if n <= hold:
        return np.empty((0, hold))
    windows = sliding_window_view(close[1:], hold)  # 第 i 行 = close[i+1 .. i+H]
    return windows / close[: n - hold, None]


def _bounds(d: float, u: float) -> tuple[float, float]:
    return (1.0 - d) * (1.0 - EPS), (1.0 + u) * (1.0 + EPS)


def time_in_range(ratios: np.ndarray, d: float, u: float) -> np.ndarray:
    """每个样本的 time_in_range（朴素实现，供测试和出界统计核对）。"""
    lo, hi = _bounds(d, u)
    if ratios.size == 0:
        return np.empty(0)
    return ((ratios >= lo) & (ratios <= hi)).mean(axis=1)


def _cum_counts(ratios: np.ndarray, thresholds: np.ndarray, strict: bool) -> np.ndarray:
    """(S, K)：每个样本里 R < t_k（strict）或 R ≤ t_k 的个数。thresholds 须升序。

    用 searchsorted + bincount + cumsum 一次算完所有阈值，O(S·H) 而不是 O(S·H·K)。
    """
    s, _ = ratios.shape
    k = thresholds.size
    # R ≤ t_k ⇔ 「严格小于 R 的阈值个数」≤ k → side="left"
    # R <  t_k ⇔ 「小于等于 R 的阈值个数」≤ k → side="right"
    idx = np.searchsorted(thresholds, ratios, side="right" if strict else "left")
    flat = (idx + np.arange(s)[:, None] * (k + 1)).ravel()
    counts = np.bincount(flat, minlength=s * (k + 1)).reshape(s, k + 1)
    return np.cumsum(counts, axis=1)[:, :k]


def range_counts(ratios: np.ndarray, downs: np.ndarray, ups: np.ndarray) -> np.ndarray:
    """(S, len(downs), len(ups)) 的区间内 K 线个数（int32）。"""
    downs, ups = np.asarray(downs, float), np.asarray(ups, float)
    hi = (1.0 + ups) * (1.0 + EPS)                       # 随 u 升序
    lo = (1.0 - downs) * (1.0 - EPS)                     # 随 d 降序
    order = np.argsort(lo)
    le_hi = _cum_counts(ratios, hi, strict=False)        # (S, U)
    lt_lo_sorted = _cum_counts(ratios, lo[order], strict=True)
    lt_lo = np.empty_like(lt_lo_sorted)
    lt_lo[:, order] = lt_lo_sorted                       # 还原为 downs 的顺序
    return (le_hi[:, None, :] - lt_lo[:, :, None]).astype(np.int32)


def symmetric_tir(ratios: np.ndarray, widths: np.ndarray) -> np.ndarray:
    """(S, W)：对称宽度 d = u = w 时每个样本的 time_in_range。"""
    widths = np.asarray(widths, float)
    hold = ratios.shape[1]
    hi = (1.0 + widths) * (1.0 + EPS)
    lo = (1.0 - widths) * (1.0 - EPS)
    order = np.argsort(lo)
    le_hi = _cum_counts(ratios, hi, strict=False)
    lt_sorted = _cum_counts(ratios, lo[order], strict=True)
    lt_lo = np.empty_like(lt_sorted)
    lt_lo[:, order] = lt_sorted
    return (le_hi - lt_lo) / hold


# ================= 汇总与推荐 =================
def p10(values: np.ndarray, axis: int = 0) -> np.ndarray:
    """10 分位取「不插值的下侧样本值」：至少 90% 的样本 ≥ 该值，与「保守档」定义一致。"""
    return np.quantile(values, 0.10, axis=axis, method="lower")


def summarize(tir: np.ndarray, targets) -> dict:
    """tir: (S, W)。返回每档宽度的 mean / median / p10 / 各目标达标样本比例。"""
    return {
        "mean": tir.mean(axis=0),
        "median": np.median(tir, axis=0),
        "p10": p10(tir, axis=0),
        "frac_ge": {float(t): (tir >= t - EPS).mean(axis=0) for t in targets},
    }


def min_width(widths: np.ndarray, stat: np.ndarray, target: float) -> float | None:
    """stat ≥ target 的最小宽度；网格内都达不到返回 None。"""
    ok = np.nonzero(stat >= target - EPS)[0]
    return float(widths[ok[0]]) if ok.size else None


def log_width(d: float, u: float) -> float:
    """总宽度 ln(1+u) − ln(1−d)。"""
    return math.log1p(u) - math.log1p(-d)


def best_skew(counts: np.ndarray, hold: int, downs: np.ndarray, ups: np.ndarray,
              target: float, conservative: bool) -> tuple[float, float] | None:
    """在 (d, u) 网格里找满足目标、且 ln(1+u) − ln(1−d) 最小的组合。"""
    if counts.shape[0] == 0:
        return None
    stat = (p10(counts, axis=0) if conservative else np.median(counts, axis=0)) / hold
    total = np.log1p(np.asarray(ups))[None, :] - np.log1p(-np.asarray(downs))[:, None]
    total = np.where(stat >= target - EPS, total, np.inf)
    m, k = np.unravel_index(np.argmin(total), total.shape)
    if not np.isfinite(total[m, k]):
        return None
    return float(downs[m]), float(ups[k])


# ================= 出界行为 =================
def exit_stats(ratios: np.ndarray, d: float, u: float) -> dict:
    """出界行为：单边占比、平均出界次数、平均回归耗时、永不回归比例。

    - 开仓时刻（价格 = P）视为在区间内，因此第一根就出界也算一次「出界」；
    - 回归耗时 = 一段出界持续的小时数（之后回到区间内）；没回来的那段不计入；
    - 永不回归比例 = 出过界的样本里，持有期结束时仍在区间外的比例。
    """
    s, h = ratios.shape
    if s == 0:
        return {}
    lo, hi = _bounds(d, u)
    above, below = ratios > hi, ratios < lo
    out = above | below
    inside = ~out
    prev_in = np.concatenate([np.ones((s, 1), bool), inside[:, :-1]], axis=1)
    exits = (prev_in & out).sum(axis=1)
    returns = (~prev_in & inside).sum(axis=1)

    # 结尾连续出界的长度：最后一次在区间内的位置之后全是出界
    any_in = inside.any(axis=1)
    last_in = np.where(any_in, h - 1 - np.argmax(inside[:, ::-1], axis=1), -1)
    trailing = (h - 1) - last_in
    returned_hours = out.sum(axis=1) - trailing

    out_total = int(out.sum())
    exited = exits > 0
    n_returns = int(returns.sum())
    return {
        "up_share": float(above.sum() / out_total) if out_total else None,
        "down_share": float(below.sum() / out_total) if out_total else None,
        "avg_exits": float(exits.mean()),
        "avg_return_hours": float(returned_hours.sum() / n_returns) if n_returns else None,
        "never_return_ratio": float(out[exited, -1].mean()) if exited.any() else None,
        "exited_sample_ratio": float(exited.mean()),
    }


def tilt_label(up_share: float | None) -> str:
    if up_share is None:
        return "—"
    if up_share >= 0.6:
        return "偏上"
    if up_share <= 0.4:
        return "偏下"
    return "均衡"


# ================= bin 换算 =================
def bin_counts(d: float, u: float, bin_step: int, max_bins: int = 69) -> dict:
    """DLMM bin 数：下侧 / 上侧 / 合计（含活跃 bin）与需要的 position 数。"""
    step = math.log1p(bin_step / 10000)
    down = math.ceil(math.log(1 / (1 - d)) / step - 1e-9)
    up = math.ceil(math.log1p(u) / step - 1e-9)
    total = down + up + 1
    return {"bins_down": down, "bins_up": up, "bins_total": total,
            "positions": math.ceil(total / max_bins)}


# ================= 单周期分析 =================
@dataclass
class HoldResult:
    hold: int
    label: str
    n_samples: int
    history_days: float
    independent_windows: float
    gap_ratio: float
    status: str
    widths: np.ndarray
    summary: dict | None = None
    recommend: dict = field(default_factory=dict)   # {T: {"typical": w, "conservative": w}}
    skew: dict = field(default_factory=dict)        # {T: {"typical": (d,u), "conservative": (d,u)}}
    exits: dict | None = None
    exit_width: float | None = None

    @property
    def has_recommendation(self) -> bool:
        return self.status != STATUS_INSUFFICIENT


def quality_status(history_days: float, hold: int, min_history_mult: float = DEFAULT_MIN_HISTORY_MULT) -> str:
    hold_days = hold / 24
    if history_days < hold_days * min_history_mult:
        return STATUS_INSUFFICIENT
    if history_days / hold_days < 3:
        return STATUS_REFERENCE
    return STATUS_OK


def analyze_hold(close: np.ndarray, hold: int, targets, widths: np.ndarray,
                 history_days: float, gap_ratio: float, min_history_mult: float = DEFAULT_MIN_HISTORY_MULT,
                 skew_grid: np.ndarray | None = None, exit_target: float = 0.8) -> HoldResult:
    ratios = build_ratios(close, hold)
    status = quality_status(history_days, hold, min_history_mult)
    res = HoldResult(hold=hold, label=hold_label(hold), n_samples=ratios.shape[0],
                     history_days=history_days, independent_windows=history_days / (hold / 24),
                     gap_ratio=gap_ratio, status=status, widths=widths)
    if ratios.shape[0] == 0:
        res.status = STATUS_INSUFFICIENT
        return res

    tir = symmetric_tir(ratios, widths)
    res.summary = summarize(tir, targets)
    if res.status == STATUS_INSUFFICIENT:
        return res

    grid = widths if skew_grid is None else skew_grid
    counts = range_counts(ratios, grid, grid)
    for t in targets:
        t = float(t)
        res.recommend[t] = {
            "typical": min_width(widths, res.summary["median"], t),
            "conservative": min_width(widths, res.summary["p10"], t),
        }
        res.skew[t] = {
            "typical": best_skew(counts, hold, grid, grid, t, conservative=False),
            "conservative": best_skew(counts, hold, grid, grid, t, conservative=True),
        }

    exit_w = min_width(widths, res.summary["median"], exit_target)
    if exit_w is not None:
        res.exit_width = exit_w
        res.exits = exit_stats(ratios, exit_w, exit_w)
    return res


def analyze(close: np.ndarray, holds=DEFAULT_HOLDS, targets=DEFAULT_TARGETS,
            widths: np.ndarray | None = None, history_days: float | None = None,
            gap_ratio: float = 0.0, min_history_mult: float = DEFAULT_MIN_HISTORY_MULT) -> list[HoldResult]:
    close = np.asarray(close, dtype=float)
    widths = width_grid() if widths is None else np.asarray(widths, float)
    if history_days is None:
        history_days = close.size / 24
    return [analyze_hold(close, int(h), targets, widths, history_days, gap_ratio, min_history_mult)
            for h in holds]

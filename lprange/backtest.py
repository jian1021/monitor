"""策略回测：(区间宽度 × 调仓规则) 网格的逐小时模拟。

价格必须是池子自身的比价（base 以 quote 计价，GeckoTerminal currency=quote），不是美元价；
成交量用美元口径，和 TVL（美元）相除得到无量纲的手续费收益率。

记账口径（全部相对「持有建仓时那一篮子代币」= HODL）：
  - 每次开仓 / 调仓都在当前价 P 以对称区间 [P(1−w), P(1+w)] 重新建仓；
  - 无常损失：用集中流动性公式（Uniswap v3）算仓位价值 / HODL 价值，每段仓位结束时结算，
    多段连乘；DLMM 的均匀 bin 分布与它略有差异，这里作为近似；
  - 手续费：该小时收盘价在区间内时计入
        fee_t = 成交量_t × 费率 × liq_width ÷ (区间宽度 × TVL)
    即需求里的「成交量 × 费率 ÷ 区间宽度」再除以 TVL 归一化。liq_width 是「池子其他 LP 的流动性
    相当于 TVL 均匀铺在多宽的对数区间里」——它决定手续费的绝对水平，是最关键的假设；
    区间宽度用对数宽度 ln((1+w)/(1−w))；
  - 调仓成本：每次调仓按仓位价值的 (0.5 × 池子费率 + 额外成本) 扣除
    （出界时仓位几乎全是一种币，要换掉约一半；额外成本含滑点 / gas / 优先费）；
  - 净收益 = 手续费 × 无常损失 × 调仓成本 三项连乘 − 1（相对 HODL 的超额收益，
    不含代币本身的涨跌）；年化 = (1 + 净收益)^(8760 / 模拟小时数) − 1。

调仓规则：
  hold   到期调仓：每 H 小时重新居中，不管是否出界；
  out:N  出界调仓：价格连续 N 小时在区间外才调仓（N=1 即出界立刻调）。
宽度规则：
  fixed:w   固定半宽 w；
  vol:k     波动率定宽：半宽 = k × σ_EWMA × √H（σ 为小时对数收益率的 EWMA 标准差，
            只用开仓时刻之前的数据，没有未来函数）。

样本独立窗口数 = 模拟小时数 ÷ H。另外按不重叠的 H 小时窗口切分净值，给出每个窗口净收益的
中位数与 p10（最差 10% 的持有期），比单一的全段收益更能说明稳定性。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

HOURS_PER_YEAR = 8760
DEFAULT_FIXED_WIDTHS = (0.02, 0.03, 0.05, 0.08, 0.12, 0.18, 0.25, 0.35, 0.5)
DEFAULT_KS = (1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5)
DEFAULT_RULES = ("hold", "out:1", "out:6", "out:24")
DEFAULT_HALFLIFE = 72.0          # EWMA 半衰期（小时）
DEFAULT_LIQ_WIDTH = 0.5          # 其他 LP 流动性等效铺开的对数宽度（约 ±25%）
DEFAULT_REBALANCE_EXTRA = 0.002  # 每次调仓的额外成本（滑点 / gas），占仓位价值
MIN_HALF, MAX_HALF = 0.005, 0.95
EPS = 1e-9

RULE_LABELS = {"hold": "到期调仓"}


def rule_label(rule: str) -> str:
    if rule in RULE_LABELS:
        return RULE_LABELS[rule]
    if rule.startswith("out:"):
        n = int(rule.split(":", 1)[1])
        return "出界即调" if n == 1 else f"出界{n}h调"
    return rule


def width_label(mode: str, value: float) -> str:
    return f"±{value * 100:.3g}%" if mode == "fixed" else f"{value:g}σ√H"


# ================= 纯函数 =================
def ewma_sigma(close: np.ndarray, halflife: float = DEFAULT_HALFLIFE, warmup: int = 24) -> np.ndarray:
    """sigma[t] = 截至 t（含）的小时对数收益率 EWMA 标准差；sigma[0] 用前 warmup 根的样本方差初始化。"""
    close = np.asarray(close, float)
    r = np.zeros(close.size)
    r[1:] = np.diff(np.log(close))
    lam = 0.5 ** (1.0 / halflife)
    init = float(np.var(r[1:warmup + 1])) if close.size > 2 else 0.0
    var = np.empty(close.size)
    v = init
    for t in range(close.size):
        if t > 0:
            v = lam * v + (1 - lam) * r[t] ** 2
        var[t] = v
    return np.sqrt(var)


def lp_hodl_ratio(p: np.ndarray, p0: float, w: float) -> np.ndarray:
    """集中流动性仓位价值 ÷ 持有建仓时代币篮子的价值（≤ 1，1 − 它就是无常损失）。

    区间 [p0(1−w), p0(1+w)]；流动性 L 在比值里约掉，取 1。
    """
    sa, sb, s0 = math.sqrt(p0 * (1 - w)), math.sqrt(p0 * (1 + w)), math.sqrt(p0)
    x0, y0 = 1 / s0 - 1 / sb, s0 - sa
    p = np.asarray(p, float)
    s = np.clip(np.sqrt(p), sa, sb)
    value = (1 / s - 1 / sb) * p + (s - sa)
    hodl = x0 * p + y0
    return value / hodl


def _rebalance_index(out_mask: np.ndarray, start: int, rule: str, hold: int) -> int:
    """从开仓点 start 起，返回触发调仓的下标（最晚到序列末尾 n−1）。"""
    n = out_mask.size
    if rule == "hold":
        return min(start + hold, n - 1)
    need = int(rule.split(":", 1)[1])
    seg = out_mask[start + 1:]
    if seg.size < need:
        return n - 1
    if need == 1:
        hit = np.flatnonzero(seg)
        return start + 1 + int(hit[0]) if hit.size else n - 1
    run = np.convolve(seg.astype(np.int32), np.ones(need, np.int32), "valid")
    hit = np.flatnonzero(run == need)
    return start + int(hit[0]) + need if hit.size else n - 1


@dataclass
class ComboResult:
    width_mode: str
    width_value: float
    rule: str
    hold: int
    hours: int
    fee_log: float
    il_log: float
    cost_log: float
    rebalances: int
    time_share: float
    volume_share: float
    avg_half_width: float
    window_net: np.ndarray = field(repr=False, default_factory=lambda: np.empty(0))

    @property
    def independent_windows(self) -> float:
        return self.hours / self.hold

    def _ann(self, log_value: float) -> float:
        return math.expm1(log_value * HOURS_PER_YEAR / self.hours) if self.hours else float("nan")

    @property
    def net_log(self) -> float:
        return self.fee_log + self.il_log + self.cost_log

    @property
    def net_total(self) -> float:
        return math.expm1(self.net_log)

    @property
    def net_annual(self) -> float:
        return self._ann(self.net_log)

    @property
    def fee_annual(self) -> float:
        return self._ann(self.fee_log)

    @property
    def il_annual(self) -> float:
        return self._ann(self.il_log)

    @property
    def cost_annual(self) -> float:
        return self._ann(self.cost_log)

    @property
    def rebalances_per_week(self) -> float:
        return self.rebalances / (self.hours / 168) if self.hours else float("nan")

    @property
    def window_median(self) -> float | None:
        return float(np.median(self.window_net)) if self.window_net.size else None

    @property
    def window_p10(self) -> float | None:
        return float(np.quantile(self.window_net, 0.10, method="lower")) if self.window_net.size else None

    @property
    def label(self) -> str:
        return f"{width_label(self.width_mode, self.width_value)} · {rule_label(self.rule)}"


def simulate(close: np.ndarray, volume_usd: np.ndarray, *, fee_rate: float, tvl_usd: float,
             width_mode: str, width_value: float, rule: str, hold: int, sigma: np.ndarray | None = None,
             start: int = 0, liq_width: float = DEFAULT_LIQ_WIDTH,
             rebalance_extra: float = DEFAULT_REBALANCE_EXTRA) -> ComboResult:
    """单个 (宽度, 调仓规则) 组合的逐小时模拟（按仓位段向量化）。"""
    close = np.asarray(close, float)
    vol = np.nan_to_num(np.asarray(volume_usd, float), nan=0.0)
    n = close.size
    if n - start < 2:
        raise ValueError("模拟区间不足 2 根 K 线")
    if tvl_usd <= 0:
        raise ValueError("TVL 必须大于 0")
    cost = 0.5 * fee_rate + rebalance_extra
    log_wealth = np.zeros(n)          # 相对 HODL 的累计对数净值（start 处为 0）
    fee_log = il_log = cost_log = 0.0
    in_hours = 0
    in_volume = 0.0
    halves = []
    rebalances = 0
    base = 0.0
    i = start
    while i < n - 1:
        if width_mode == "fixed":
            w = float(width_value)
        else:
            w = float(width_value) * float(sigma[i]) * math.sqrt(hold)
        w = min(max(w, MIN_HALF), MAX_HALF)
        halves.append(w)
        p0 = close[i]
        lo, hi = p0 * (1 - w) * (1 - EPS), p0 * (1 + w) * (1 + EPS)
        seg_p = close[i + 1:]
        out = np.zeros(n, bool)
        out[i + 1:] = (seg_p < lo) | (seg_p > hi)
        j = _rebalance_index(out, i, rule, hold)
        sl = slice(i + 1, j + 1)
        inside = ~out[sl]
        width_log = math.log((1 + w) / (1 - w))
        fee_t = np.where(inside, vol[sl] * fee_rate * liq_width / (width_log * tvl_usd), 0.0)
        fee_cum = np.cumsum(np.log1p(fee_t))
        ratio = lp_hodl_ratio(close[sl], p0, w)
        log_wealth[sl] = base + fee_cum + np.log(ratio)
        fee_log += float(fee_cum[-1])
        il_log += float(np.log(ratio[-1]))
        in_hours += int(inside.sum())
        in_volume += float(vol[sl][inside].sum())
        base = float(log_wealth[j])
        if j < n - 1:                      # 触发调仓（到末尾就只是结算，不算调仓）
            base += math.log1p(-cost)
            log_wealth[j] = base
            cost_log += math.log1p(-cost)
            rebalances += 1
        i = j
    hours = n - 1 - start
    total_vol = float(vol[start + 1:].sum())
    # 不重叠的 H 小时窗口净收益（含窗口内发生的调仓成本）
    edges = np.arange(start, n, hold)
    window_net = np.expm1(np.diff(log_wealth[edges])) if edges.size > 1 else np.empty(0)
    return ComboResult(
        width_mode=width_mode, width_value=float(width_value), rule=rule, hold=int(hold), hours=hours,
        fee_log=fee_log, il_log=il_log, cost_log=cost_log, rebalances=rebalances,
        time_share=in_hours / hours, volume_share=(in_volume / total_vol) if total_vol > 0 else float("nan"),
        avg_half_width=float(np.mean(halves)), window_net=window_net)


@dataclass
class BacktestResult:
    combos: list[ComboResult]
    hours: int
    start_ts: int | None
    warmup: int
    fee_rate: float
    tvl_usd: float
    liq_width: float
    rebalance_cost: float
    halflife: float

    def sorted(self) -> list[ComboResult]:
        return sorted(self.combos, key=lambda c: c.net_annual, reverse=True)


def run_grid(close: np.ndarray, volume_usd: np.ndarray, *, fee_rate: float, tvl_usd: float,
             holds=(168,), fixed_widths=DEFAULT_FIXED_WIDTHS, ks=DEFAULT_KS, rules=DEFAULT_RULES,
             halflife: float = DEFAULT_HALFLIFE, liq_width: float = DEFAULT_LIQ_WIDTH,
             rebalance_extra: float = DEFAULT_REBALANCE_EXTRA, warmup: int | None = None,
             ts: np.ndarray | None = None) -> BacktestResult:
    """全部组合共用同一段模拟区间：前 warmup 小时只用来估计 σ，不计收益。"""
    close = np.asarray(close, float)
    if warmup is None:
        warmup = int(min(max(24, halflife), close.size // 4))
    sigma = ewma_sigma(close, halflife)
    combos = []
    for h in holds:
        for w in fixed_widths:
            for rule in rules:
                combos.append(simulate(close, volume_usd, fee_rate=fee_rate, tvl_usd=tvl_usd,
                                       width_mode="fixed", width_value=w, rule=rule, hold=int(h),
                                       start=warmup, liq_width=liq_width, rebalance_extra=rebalance_extra))
        for k in ks:
            for rule in rules:
                combos.append(simulate(close, volume_usd, fee_rate=fee_rate, tvl_usd=tvl_usd,
                                       width_mode="vol", width_value=k, rule=rule, hold=int(h),
                                       sigma=sigma, start=warmup, liq_width=liq_width,
                                       rebalance_extra=rebalance_extra))
    return BacktestResult(combos=combos, hours=close.size - 1 - warmup,
                          start_ts=int(ts[warmup]) if ts is not None and ts.size > warmup else None,
                          warmup=warmup, fee_rate=fee_rate, tvl_usd=tvl_usd, liq_width=liq_width,
                          rebalance_cost=0.5 * fee_rate + rebalance_extra, halflife=halflife)


def combo_row(c: ComboResult) -> dict:
    return {
        "组合": c.label, "宽度规则": "固定" if c.width_mode == "fixed" else "波动率",
        "宽度参数": c.width_value, "调仓规则": rule_label(c.rule), "H": c.hold,
        "平均半宽": c.avg_half_width,
        "净收益年化": c.net_annual, "手续费年化": c.fee_annual, "无常损失年化": c.il_annual,
        "调仓成本年化": c.cost_annual, "调仓次数": c.rebalances, "每周调仓": c.rebalances_per_week,
        "时间占比": c.time_share, "成交量占比": c.volume_share,
        "窗口净收益中位": c.window_median, "窗口净收益p10": c.window_p10,
        "独立窗口": round(c.independent_windows, 1), "模拟小时": c.hours,
    }

# -*- coding: utf-8 -*-
"""LP 区间宽度监控列表（lprange 的页面版）。

问题：开仓后持有一段时间、区间不调整，想让价格在区间内的「时间占比」达到 70% / 80% / 90%，
区间至少要多宽？页面是一张监控列表：可一键分析全部，也可勾选单独分析；每个池子最近一次的
结果保存在本地 watchlist.db。统计、取数、缓存全部复用 lprange 包。

页面默认：持有 1 周、历史门槛 1 倍（history_days ≥ 持有天数即给结果，适合历史短的新池子）；
命令行默认仍是 4 个周期 + 3 倍门槛。
"""
import json

import numpy as np
import pandas as pd
import streamlit as st

from lprange.cli import build_parser, run_backtest, run_one
from lprange.config import load_config
from lprange.data import LPRangeError
from app.interfaces.timefmt import to_local_display
from lprange.report import (BACKTEST_DISCLAIMER, BACKTEST_NOTE, DISCLAIMER, backtest_rows, list_summary, pct,
                            single_csv_rows, single_to_dict)
from lprange.stats import STATUS_INSUFFICIENT, STATUS_REFERENCE, bin_counts, hold_label, tilt_label
from lprange.turso import TursoError, import_turso
from lprange.watchlist import Watchlist, parse_import_line

# GeckoTerminal 的 network id（与 monitor 的链代码 sol/eth… 不是一套命名）
NETWORKS = {"solana": "Solana", "robinhood": "Robinhood Chain", "eth": "Ethereum", "base": "Base",
            "bsc": "BNB Chain", "arbitrum": "Arbitrum"}
HOLD_OPTIONS = [168, 336, 504, 720]
TARGET_OPTIONS = [0.7, 0.8, 0.9]
DEFAULT_HOLDS = [168]          # 默认只看持有 1 周
DEFAULT_HISTORY_MULT = 1.0     # 默认历史门槛 1 倍


# ============================================================
# 工具
# ============================================================
@st.cache_resource(show_spinner=False)
def _cfg():
    return load_config()


def _wl() -> Watchlist:
    return Watchlist(_cfg().db_path)


def _args(target: str, network: str, bin_step: int, days: int, holds, targets, token: str,
          refresh: bool, mult: float = DEFAULT_HISTORY_MULT):
    argv = ["run", target, "--network", network, "--days", str(days),
            "--holds", ",".join(str(h) for h in holds),
            "--targets", ",".join(str(t) for t in targets), "--token", token,
            "--min-history-mult", str(mult)]
    if bin_step:
        argv += ["--bin-step", str(bin_step)]
    if refresh:
        argv.append("--refresh")
    return build_parser().parse_args(argv)


def _analyze(target: str, network: str | None, opts: dict):
    args = _args(target, network or opts["network"], opts["bin_step"], opts["days"], opts["holds"],
                 opts["targets"], opts["token"], opts["refresh"], opts["mult"])
    return run_one(args, _cfg(), _wl(), target, network=network)


def _w(v) -> str:
    return ">80%" if v is None else f"±{pct(v)}"


def _skew(du) -> str:
    if not du:
        return ">80%"
    d, u = du
    return f"−{pct(d)} / +{pct(u)}"


# ============================================================
# 结果渲染
# ============================================================
def _render_result(r) -> None:
    p = r.pool
    head = f"**{p.get('name') or '—'}** · {NETWORKS.get(p.get('network'), p.get('network'))}"
    if r.current_price:
        head += f" · 现价 **{r.current_price:.8g}**"
    if r.bin_step:
        head += f" · binStep {r.bin_step}"
    st.markdown(head)
    st.caption(f"`{p.get('address')}`　{r.fetch_note}"
               + ("　（输入的是代币地址，已选用流动性最大的池子）" if p.get("from_token") else ""))
    for w in r.warnings:
        st.warning(w, icon="⚠️")

    # ---- 一眼结论：各周期 80% 典型档 ----
    t_main = 0.8 if 0.8 in r.targets else r.targets[len(r.targets) // 2]
    cols = st.columns(len(r.holds))
    for col, h in zip(cols, r.holds):
        if not h.has_recommendation:
            col.metric(f"持有 {h.label}", STATUS_INSUFFICIENT,
                       help=f"history_days {h.history_days:.0f} 天不够")
            continue
        rec = h.recommend.get(t_main) or {}
        w = rec.get("typical")
        extra = ""
        if r.bin_step and w is not None:
            b = bin_counts(w, w, r.bin_step, r.max_bins)
            extra = f"{b['bins_total']} bins" + (f" · 拆 {b['positions']} 个" if b["positions"] > 1 else "")
        col.metric(f"持有 {h.label} · {pct(t_main)} 时间在区间内", _w(w),
                   delta=extra or None, delta_color="off",
                   help=f"保守档（90% 历史样本达标）：{_w(rec.get('conservative'))}")

    tabs = st.tabs(["推荐宽度", "偏斜区间", "宽度 → 时间占比", "出界行为", "数据质量"])

    with tabs[0]:
        rows = []
        for h in r.holds:
            row = {"周期": h.label + (f"（{h.status}）" if h.status == STATUS_REFERENCE else "")}
            for t in r.targets:
                rec = h.recommend.get(t) or {}
                ok = h.has_recommendation
                row[f"{pct(t)} 典型"] = _w(rec.get("typical")) if ok else STATUS_INSUFFICIENT
                row[f"{pct(t)} 保守"] = _w(rec.get("conservative")) if ok else STATUS_INSUFFICIENT
                if r.bin_step and ok and rec.get("typical") is not None:
                    row[f"{pct(t)} bins"] = bin_counts(rec["typical"], rec["typical"],
                                                       r.bin_step, r.max_bins)["bins_total"]
            rows.append(row)
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption("典型 = 中位数样本达标；保守 = 90% 的历史样本都达标。"
                   + (f" bins 按 binStep {r.bin_step}、单 position 上限 {r.max_bins} 计算。" if r.bin_step else ""))

    with tabs[1]:
        rows = []
        for h in r.holds:
            for kind, name in (("typical", "典型"), ("conservative", "保守")):
                row = {"周期": h.label, "档": name}
                for t in r.targets:
                    row[pct(t)] = (_skew((h.skew.get(t) or {}).get(kind))
                                   if h.has_recommendation else STATUS_INSUFFICIENT)
                rows.append(row)
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption("下沿 −d / 上沿 +u：满足目标且总宽度 ln(1+u) − ln(1−d) 最小的组合。")

    with tabs[2]:
        widths = r.holds[0].widths
        chart = pd.DataFrame({h.label: h.summary["median"] for h in r.holds if h.summary is not None},
                             index=pd.Index(np.round(widths * 100, 2), name="宽度 ±%"))
        if not chart.empty:
            st.line_chart(chart * 100, x_label="对称宽度 ±%", y_label="时间占比中位数 %")
        rows = []
        for i, w in enumerate(widths):
            row = {"宽度 ±": pct(w)}
            for h in r.holds:
                row[h.label] = ("—" if h.summary is None else
                                f"{pct(h.summary['median'][i])} / {pct(h.summary['p10'][i])}")
            rows.append(row)
        with st.expander("明细表（中位数 / p10）"):
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=420)

    with tabs[3]:
        rows = []
        for h in r.holds:
            e = h.exits or {}
            rows.append({
                "周期": h.label, "宽度": _w(h.exit_width) if e else "—",
                "向上出界": pct(e.get("up_share")), "向下出界": pct(e.get("down_share")),
                "倾向": tilt_label(e.get("up_share")),
                "平均出界次数": f"{e['avg_exits']:.2f}" if e else "—",
                "平均回归(h)": f"{e['avg_return_hours']:.1f}" if e.get("avg_return_hours") is not None else "—",
                "永不回归": pct(e.get("never_return_ratio")),
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption(f"用 T={pct(t_main)} 典型档的对称宽度统计。永不回归 = 出过界的样本里，到期仍在区间外的比例。")

    with tabs[4]:
        st.dataframe(pd.DataFrame([{
            "周期": h.label, "history_days": round(h.history_days, 1),
            "独立窗口": round(h.independent_windows, 1), "样本数(重叠)": h.n_samples,
            "gap_ratio": pct(h.gap_ratio), "可信度": h.status if h.status != "ok" else "正常",
        } for h in r.holds]), hide_index=True, width="stretch")

    d1, d2, _ = st.columns([1, 1, 4])
    name = (p.get("name") or "pool").replace("/", "-").replace(" ", "")
    d1.download_button("⬇️ JSON", json.dumps(single_to_dict(r), ensure_ascii=False, default=str, indent=2),
                       file_name=f"lprange_{name}.json", mime="application/json", width="stretch")
    d2.download_button("⬇️ CSV", pd.DataFrame(single_csv_rows(r)).to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"lprange_{name}.csv", mime="text/csv", width="stretch")
    st.caption(DISCLAIMER)


# ============================================================
# 监控列表
# ============================================================
def _status_text(summary: dict | None, label: str) -> str:
    if not summary:
        return "未分析"
    if summary.get("error"):
        return "❌ " + str(summary["error"])[:60]
    h = (summary.get("holds") or {}).get(label)
    if not h:
        return "—"
    return {"ok": "正常"}.get(h["status"], h["status"])


def _analyze_pools(pools_to_run, opts: dict) -> None:
    """逐个分析并保存摘要；单个池子失败只记录原因，不影响其余。"""
    cache = st.session_state.setdefault("lpr_cache", {})
    bar = st.progress(0.0, text="准备 …")
    ok = bad = 0
    for i, p in enumerate(pools_to_run, 1):
        bar.progress((i - 1) / len(pools_to_run), text=f"{i}/{len(pools_to_run)} · {p.name or p.address[:10]}")
        try:
            r = _analyze(p.address, p.network, opts)
            _wl().save_result(p.id, list_summary(r))
            cache[p.id] = r
            ok += 1
        except LPRangeError as exc:
            _wl().save_result(p.id, {"error": str(exc)})
            bad += 1
        except Exception as exc:  # noqa: BLE001
            _wl().save_result(p.id, {"error": f"{type(exc).__name__}: {exc}"})
            bad += 1
    bar.empty()
    st.session_state.pop("lpr_list_editor", None)          # 清掉勾选
    st.session_state["lpr_flash"] = f"分析完成：成功 {ok} 个" + (f"，失败 {bad} 个" if bad else "")


# ============================================================
# 页面
# ============================================================
st.set_page_config(page_title="LP 区间宽度", page_icon="📏", layout="wide")
st.title("📏 LP 区间宽度")
st.caption("区间不调整、持有一段时间，要让价格在区间内的时间占比达到目标，区间至少要多宽（历史统计，不代表未来）。")

if st.session_state.get("lpr_flash"):
    st.toast(st.session_state.pop("lpr_flash"), icon="✅")

with st.expander("⚙️ 分析参数（默认：持有 1 周 · 历史门槛 1 倍）"):
    a1, a2, a3, a4, a5 = st.columns([1.6, 1.4, 1, 1, 1.2])
    holds = a1.multiselect("持有周期", HOLD_OPTIONS, default=DEFAULT_HOLDS, format_func=hold_label,
                           key="lpr_holds")
    targets = a2.multiselect("时间占比目标", TARGET_OPTIONS, default=TARGET_OPTIONS, format_func=pct,
                             key="lpr_targets")
    mult = a3.number_input("历史门槛（倍）", min_value=0.5, max_value=5.0, value=DEFAULT_HISTORY_MULT,
                           step=0.5, key="lpr_mult",
                           help="历史天数 < 持有天数 × 倍数时标「样本不足」不给结果。"
                                "1 倍：持有 1 周只要 7 天历史；倍数越大结果越可靠")
    days = a4.number_input("回看天数", min_value=14, max_value=365, value=180, step=30, key="lpr_days")
    token = a5.selectbox("价格方向", ["base", "quote"], key="lpr_token",
                         format_func={"base": "base 计价（默认）", "quote": "反转（quote）"}.get)
    refresh = st.checkbox("忽略缓存，强制重新拉取", key="lpr_refresh")

opts = {"network": "solana", "bin_step": 0, "days": int(days),
        "holds": sorted(holds) or DEFAULT_HOLDS, "targets": sorted(targets) or TARGET_OPTIONS,
        "token": token, "refresh": refresh, "mult": float(mult)}
labels = [hold_label(h) for h in opts["holds"]]

# ---------------- 加入池子 ----------------
with st.form("lpr_add_form", border=False, clear_on_submit=True):
    c_q, c_net, c_bin, c_add = st.columns([4, 1.4, 1, 1.1], vertical_alignment="bottom")
    query = c_q.text_input("池子 / 代币地址", placeholder="Meteora 池子 / Uniswap 池子地址或 v4 poolId；"
                                                      "代币地址会自动选流动性最大的池子")
    network = c_net.selectbox("网络", list(NETWORKS), format_func=NETWORKS.get)
    bin_step = c_bin.number_input("binStep（0=自动）", min_value=0, max_value=1000, value=0, step=1,
                                  help="仅 Meteora DLMM 有 bin；Solana 池子留 0 会自动读取")
    add = c_add.form_submit_button("➕ 加入并分析", type="primary", width="stretch")

if add:
    q = query.strip()
    if not q:
        st.warning("请输入池子或代币地址")
    else:
        one = {**opts, "network": network, "bin_step": int(bin_step) if network == "solana" else 0}
        with st.spinner("拉取 K 线并计算（首次约 15 秒，之后只增量拉取）…"):
            try:
                r = _analyze(q, network, one)
            except Exception as exc:  # noqa: BLE001
                st.error(f"❌ {exc}")
            else:
                pool = _wl().get(r.pool.get("network") or network, r.pool.get("address") or q)
                if pool is not None:
                    _wl().save_result(pool.id, list_summary(r))
                    st.session_state.setdefault("lpr_cache", {})[pool.id] = r
                    st.session_state["lpr_detail"] = pool.id
                st.session_state["lpr_flash"] = f"已加入并分析：{r.pool.get('name') or q}"
                st.rerun()

# ---------------- 监控列表 ----------------
wl = _wl()
pools = sorted(wl.list(), key=lambda p: p.id)       # 按加入顺序，分析后行不跳动
results = wl.results()
by_id = {p.id: p for p in pools}

head_l, head_sel, head_all = st.columns([3.6, 1.2, 1.2], vertical_alignment="bottom")
head_l.subheader(f"监控列表（{len(pools)}）")
run_selected = head_sel.button("🔍 分析选中", width="stretch", disabled=not pools)
run_all = head_all.button("⚡ 一键分析全部", type="primary", width="stretch", disabled=not pools)

if not pools:
    st.info("列表为空：在上方粘贴地址「加入并分析」，或在下方批量导入。")
else:
    rows = []
    for p in pools:
        analyzed_at, summary = results.get(p.id, (None, None))
        row = {"id": p.id, "选": False, "名称": p.name or p.address[:10] + "…",
               "网络": NETWORKS.get(p.network, p.network)}
        holds_sum = (summary or {}).get("holds") or {}
        for lb in labels:
            h = holds_sum.get(lb)
            ok = h and h.get("status") != STATUS_INSUFFICIENT
            row[f"{lb} 典型"] = _w(h.get("typical")) if ok else ("样本不足" if h else "—")
            row[f"{lb} 保守"] = _w(h.get("conservative")) if ok else ("样本不足" if h else "—")
            if any((v.get("holds") or {}).get(lb, {}).get("bins") for _, v in results.values()):
                row[f"{lb} bins"] = (h or {}).get("bins")
        first = holds_sum.get(labels[0]) or {}
        row["出界倾向"] = first.get("tilt") or "—"
        row["历史(天)"] = (summary or {}).get("history_days")
        row["状态"] = _status_text(summary, labels[0])
        row["分析时间"] = to_local_display(analyzed_at, fallback="—") if analyzed_at else "—"
        row["备注"] = p.note or ""
        row["删除"] = False
        rows.append(row)
    view = pd.DataFrame(rows)
    edited = st.data_editor(
        view, key="lpr_list_editor", hide_index=True, width="stretch",
        disabled=[c for c in view.columns if c not in ("选", "备注", "删除")],
        column_config={
            "id": None,
            "选": st.column_config.CheckboxColumn(width="small", help="勾选后点「分析选中」"),
            "历史(天)": st.column_config.NumberColumn(format="%.0f", width="small"),
            "删除": st.column_config.CheckboxColumn(width="small"),
        },
    )
    st.caption(f"典型 = 中位数样本达标，保守 = 90% 历史样本达标；表中为「80% 时间在区间内」所需的对称宽度。"
               f"参数：持有 {'、'.join(labels)} · 历史门槛 {opts['mult']:g} 倍 · 回看 {opts['days']} 天。")

    picked = [by_id[int(i)] for i in edited.loc[edited["选"], "id"]]
    if run_selected:
        if not picked:
            st.warning("先在「选」列勾选要分析的池子")
        else:
            _analyze_pools(picked, opts)
            st.rerun()
    if run_all:
        _analyze_pools(pools, opts)
        st.rerun()

    orig, new = view.set_index("id"), edited.set_index("id")
    changes = [(pid, "rm" if new.loc[pid, "删除"] else "note") for pid in new.index
               if new.loc[pid, "删除"] or (orig.loc[pid, "备注"] or "") != (new.loc[pid, "备注"] or "")]
    if changes:
        i_l, i_r = st.columns([4, 1], vertical_alignment="center")
        n_del = sum(1 for _, k in changes if k == "rm")
        i_l.info(f"{len(changes)} 项未保存" + (f"，含删除 {n_del} 个池子" if n_del else ""))
        if i_r.button("💾 保存修改", type="primary", width="stretch"):
            for pid, kind in changes:
                if kind == "rm":
                    wl.remove(by_id[int(pid)])
                else:
                    wl.set_note(by_id[int(pid)], str(new.loc[pid, "备注"] or ""))
            st.session_state.pop("lpr_list_editor", None)
            st.toast("已保存", icon="💾")
            st.rerun()

    # ---------------- 单个池子明细 ----------------
    cache = st.session_state.setdefault("lpr_cache", {})
    ids = [p.id for p in pools]
    default = st.session_state.get("lpr_detail")
    d_l, d_r = st.columns([4, 1.2], vertical_alignment="bottom")
    detail_id = d_l.selectbox(
        "查看明细", ids, index=ids.index(default) if default in ids else 0, key="lpr_detail_pick",
        format_func=lambda i: f"{by_id[i].name or by_id[i].address[:10]} · "
                              f"{NETWORKS.get(by_id[i].network, by_id[i].network)}")
    if d_r.button("🔍 分析此池", width="stretch"):
        st.session_state["lpr_detail"] = detail_id
        _analyze_pools([by_id[detail_id]], opts)
        st.rerun()
    if detail_id in cache:
        with st.container(border=True):
            _render_result(cache[detail_id])
    else:
        _, summary = results.get(detail_id, (None, None))
        if summary and summary.get("error"):
            st.error(f"上次分析失败：{summary['error']}")
        else:
            st.caption("完整明细（偏斜区间、出界行为、宽度曲线等）需要在本次会话里分析一次：点「分析此池」。")

    # ---------------- 策略回测 ----------------
    with st.expander("🧪 策略回测：(宽度 × 调仓规则) 网格，按净收益排序", expanded=False):
        bpool = by_id[detail_id]
        b1, b2, b3, b4, b5 = st.columns([1, 1, 1, 1, 1])
        bt_fee = b1.number_input("费率 %（0 = 自动读取）", min_value=0.0, max_value=20.0, value=0.0, step=0.05,
                                 key="bt_fee")
        bt_tvl = b2.number_input("TVL $（0 = 用当前值）", min_value=0.0, value=0.0, step=10_000.0, key="bt_tvl")
        bt_liq = b3.number_input("liq_width", min_value=0.05, max_value=5.0, value=0.5, step=0.05, key="bt_liq",
                                 help="其他 LP 的流动性等效铺开的对数宽度；越小 = 竞争越集中 = 你的手续费越少")
        bt_extra = b4.number_input("调仓额外成本 %", min_value=0.0, max_value=5.0, value=0.2, step=0.05,
                                   key="bt_extra", help="滑点 / gas；另加 0.5 × 费率")
        bt_h = b5.selectbox("H", HOLD_OPTIONS, index=0, format_func=hold_label, key="bt_h",
                            help="波动率定宽 √H 的 H、到期调仓的周期、独立窗口的长度")
        if st.button(f"🧪 回测 {bpool.name or bpool.address[:10]}", type="primary"):
            argv = ["backtest", bpool.address, "--network", bpool.network, "--days", str(opts["days"]),
                    "--holds", str(bt_h), "--token", opts["token"], "--liq-width", str(bt_liq),
                    "--rebalance-extra", str(bt_extra / 100)]
            if bt_fee > 0:
                argv += ["--fee", str(bt_fee / 100)]
            if bt_tvl > 0:
                argv += ["--tvl", str(bt_tvl)]
            if opts["refresh"]:
                argv.append("--refresh")
            with st.spinner("拉取比价与成交量并模拟 64 个组合 …"):
                try:
                    st.session_state["bt_report"] = (detail_id, run_backtest(build_parser().parse_args(argv),
                                                                             _cfg(), _wl(), bpool.address,
                                                                             network=bpool.network))
                except Exception as exc:  # noqa: BLE001
                    st.session_state.pop("bt_report", None)
                    st.error(f"❌ 回测失败：{exc}")
        saved_bt = st.session_state.get("bt_report")
        if saved_bt and saved_bt[0] == detail_id:
            rep_bt = saved_bt[1]
            r_bt = rep_bt.result
            st.caption(f"模拟 {r_bt.hours / 24:.0f} 天 · 费率 {r_bt.fee_rate * 100:.3g}%（{rep_bt.fee_source}）· "
                       f"TVL ${r_bt.tvl_usd:,.0f}（{rep_bt.tvl_source}）· 每次调仓成本 {r_bt.rebalance_cost * 100:.3g}%")
            for n in rep_bt.notes:
                st.warning(n, icon="⚠️")
            df_bt = pd.DataFrame(backtest_rows(rep_bt))
            show = ["排名", "组合", "平均半宽", "净收益年化", "手续费年化", "无常损失年化", "调仓成本年化",
                    "调仓次数", "时间占比", "成交量占比", "窗口净收益p10", "独立窗口"]
            pct_cols = ["平均半宽", "净收益年化", "手续费年化", "无常损失年化", "调仓成本年化", "时间占比",
                        "成交量占比", "窗口净收益p10"]
            view_bt = df_bt[show].copy()
            for col in pct_cols:
                view_bt[col] = view_bt[col] * 100
            st.dataframe(view_bt, hide_index=True, width="stretch", height=460, column_config={
                **{col: st.column_config.NumberColumn(f"{col} %", format="%.1f") for col in pct_cols},
                "独立窗口": st.column_config.NumberColumn(format="%.1f", width="small"),
                "调仓次数": st.column_config.NumberColumn(width="small"),
            })
            st.download_button("⬇️ 回测 CSV", df_bt.to_csv(index=False).encode("utf-8-sig"),
                               file_name="lprange_backtest.csv", mime="text/csv")
            st.caption(BACKTEST_NOTE + " 窗口净收益 p10 = 按 H 切成不重叠窗口后最差 10% 的持有期（不年化）。")
            st.caption(BACKTEST_DISCLAIMER)

# ---------------- 导入 ----------------
with st.expander("➕ 批量导入池子"):
    i_l, i_r = st.columns([3, 2])
    text = i_l.text_area("每行一个地址，可带网络（如 `robinhood:0x…`）", height=110, key="lpr_import",
                         placeholder="7qbRF6YsyGuLUVs6Y1q64bdVrfe4ZcUUz1JRdoVNUJnm\nrobinhood:0x…")
    imp_net = i_l.selectbox("没写网络时默认", list(NETWORKS), format_func=NETWORKS.get, key="lpr_imp_net")
    if i_l.button("导入地址", disabled=not text.strip()):
        added = 0
        for line in text.splitlines():
            parsed = parse_import_line(line, imp_net)
            if parsed:
                added += _wl().upsert(parsed[0], parsed[1], source="manual")
        st.session_state["lpr_flash"] = f"新增 {added} 个池子（点「一键分析全部」出结果）"
        st.rerun()
    i_r.caption("从 Turso 导入 monitor 正在监控的池子（表结构见 lprange/config.toml）")
    t1, t2 = i_r.columns(2)
    preview, do_import = t1.button("预览", width="stretch"), t2.button("导入", type="primary", width="stretch")
    if preview or do_import:
        try:
            rows_t, added, notes = import_turso(_cfg(), _wl(), dry_run=preview)
        except TursoError as exc:
            i_r.error(f"Turso 导入失败：{exc}")
        else:
            if preview:
                i_r.dataframe(pd.DataFrame(rows_t), hide_index=True, width="stretch")
            for n in notes:
                i_r.caption(n)
            if do_import:
                st.session_state["lpr_flash"] = f"Turso：读取 {len(rows_t)} 个，新增 {added} 个"
                st.rerun()

st.caption(DISCLAIMER)

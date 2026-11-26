# -*- coding: utf-8 -*-
"""LP 区间宽度：lprange 命令行工具的页面版。

问题：开仓后持有 1w / 2w / 3w / 1m、区间不调整，想让价格在区间内的「时间占比」达到
70% / 80% / 90%，区间至少要多宽？统计、取数、缓存、watchlist 全部复用 lprange 包。
"""
import json

import numpy as np
import pandas as pd
import streamlit as st

from lprange.cli import build_parser, run_one
from lprange.config import load_config
from lprange.data import LPRangeError
from lprange.report import DISCLAIMER, batch_row, pct, single_csv_rows, single_to_dict
from lprange.stats import STATUS_INSUFFICIENT, STATUS_REFERENCE, bin_counts, hold_label, tilt_label
from lprange.turso import TursoError, import_turso
from lprange.watchlist import Watchlist, parse_import_line

# GeckoTerminal 的 network id（与 monitor 的链代码 sol/eth… 不是一套命名）
NETWORKS = {"solana": "Solana", "robinhood": "Robinhood Chain", "eth": "Ethereum", "base": "Base",
            "bsc": "BNB Chain", "arbitrum": "Arbitrum"}
HOLD_OPTIONS = [168, 336, 504, 720]
TARGET_OPTIONS = [0.7, 0.8, 0.9]


# ============================================================
# 工具
# ============================================================
@st.cache_resource(show_spinner=False)
def _cfg():
    return load_config()


def _wl() -> Watchlist:
    return Watchlist(_cfg().db_path)


def _args(target: str, network: str, bin_step: int, days: int, holds, targets, token: str,
          refresh: bool):
    argv = ["run", target, "--network", network, "--days", str(days),
            "--holds", ",".join(str(h) for h in holds),
            "--targets", ",".join(str(t) for t in targets), "--token", token]
    if bin_step:
        argv += ["--bin-step", str(bin_step)]
    if refresh:
        argv.append("--refresh")
    return build_parser().parse_args(argv)


def _analyze(target: str, network: str | None, opts: dict):
    args = _args(target, network or opts["network"], opts["bin_step"], opts["days"], opts["holds"],
                 opts["targets"], opts["token"], opts["refresh"])
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
# 页面
# ============================================================
st.set_page_config(page_title="LP 区间宽度", page_icon="📏", layout="wide")
st.title("📏 LP 区间宽度")
st.caption("持有 1w~1m、区间不调整时，要让价格在区间内的时间占比达到目标，区间至少要多宽（历史统计）。")

with st.form("lpr_form", border=False):
    c_q, c_net, c_bin, c_go = st.columns([4, 1.3, 1, 0.8], vertical_alignment="bottom")
    query = c_q.text_input("池子 / 代币地址，或历史池子名称", key="lpr_query",
                           placeholder="Meteora 池子地址 / Uniswap 池子地址或 v4 poolId；代币地址自动选流动性最大的池子")
    network = c_net.selectbox("网络", list(NETWORKS), format_func=NETWORKS.get, key="lpr_net")
    bin_step = c_bin.number_input("binStep（0=自动）", min_value=0, max_value=1000, value=0, step=1,
                                  key="lpr_bin", disabled=network != "solana",
                                  help="仅 Meteora DLMM（Solana）有 bin；Uniswap 池子不输出 bin 表")
    go = c_go.form_submit_button("📏 分析", type="primary", width="stretch")
    with st.expander("更多参数"):
        a1, a2, a3, a4 = st.columns(4)
        holds = a1.multiselect("持有周期", HOLD_OPTIONS, default=HOLD_OPTIONS, format_func=hold_label)
        targets = a2.multiselect("时间占比目标", TARGET_OPTIONS, default=TARGET_OPTIONS, format_func=pct)
        days = a3.number_input("回看天数", min_value=14, max_value=365, value=180, step=30)
        token = a4.selectbox("价格方向", ["base", "quote"],
                             format_func={"base": "base 计价（默认）", "quote": "反转（quote）"}.get)
        refresh = st.checkbox("忽略缓存，强制重新拉取")

opts = {"network": network, "bin_step": int(bin_step) if network == "solana" else 0, "days": int(days),
        "holds": sorted(holds) or HOLD_OPTIONS, "targets": sorted(targets) or TARGET_OPTIONS,
        "token": token, "refresh": refresh}


def _run(target: str, net: str | None) -> None:
    """结果 / 错误都放进 session_state，由下方统一渲染（rerun 后不丢）。"""
    st.session_state.pop("lpr_error", None)
    with st.spinner("拉取 K 线并计算（首次约 15 秒，之后只增量拉取）…"):
        try:
            st.session_state["lpr_result"] = _analyze(target, net, opts)
        except LPRangeError as exc:
            st.session_state.pop("lpr_result", None)
            st.session_state["lpr_error"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            st.session_state.pop("lpr_result", None)
            st.session_state["lpr_error"] = f"分析失败：{type(exc).__name__}: {exc}"


if go:
    if not query.strip():
        st.warning("请输入池子地址或历史池子名称")
    else:
        _run(query.strip(), None)   # 历史池子关键字会用池子自己记录的网络

if st.session_state.get("lpr_error"):
    st.error(f"❌ {st.session_state['lpr_error']}")
result = st.session_state.get("lpr_result")
if result is not None:
    with st.container(border=True):
        _render_result(result)

# ---------------- 历史池子 ----------------
wl = _wl()
pools = wl.list()
head_l, head_r = st.columns([4, 1.4], vertical_alignment="bottom")
head_l.subheader(f"历史池子（{len(pools)}）")
batch = head_r.button("📊 全部批量分析", width="stretch", disabled=not pools,
                      help="逐个分析全部池子，输出各周期 80% 典型档汇总")

if pools:
    view = pd.DataFrame([{
        "id": p.id, "名称": p.name or p.address[:10] + "…", "网络": p.network, "来源": p.source,
        "使用": p.use_count, "最近使用": p.last_used or "—", "备注": p.note or "",
        "地址": p.address, "删除": False,
    } for p in pools])
    edited = st.data_editor(
        view, key="lpr_wl_editor", hide_index=True, width="stretch",
        disabled=["名称", "网络", "来源", "使用", "最近使用", "地址"],
        column_config={"id": None,
                       "使用": st.column_config.NumberColumn(width="small"),
                       "删除": st.column_config.CheckboxColumn(width="small")},
    )
    by_id = {p.id: p for p in pools}
    p_l, p_r, s_r = st.columns([4, 1, 1], vertical_alignment="bottom")
    pick = p_l.selectbox("选择池子", [p.id for p in pools], key="lpr_pick",
                         format_func=lambda i: f"{by_id[i].name or by_id[i].address[:10]} · {by_id[i].network}",
                         label_visibility="collapsed")
    if p_r.button("📏 分析选中", width="stretch"):
        _run(by_id[pick].address, by_id[pick].network)
        st.rerun()

    changes = []
    orig, new = view.set_index("id"), edited.set_index("id")
    for pid in new.index:
        if new.loc[pid, "删除"]:
            changes.append(("rm", pid))
        elif (orig.loc[pid, "备注"] or "") != (new.loc[pid, "备注"] or ""):
            changes.append(("note", pid))
    if changes and s_r.button(f"💾 保存（{len(changes)}）", type="primary", width="stretch"):
        for kind, pid in changes:
            if kind == "rm":
                wl.remove(by_id[pid])
            else:
                wl.set_note(by_id[pid], str(new.loc[pid, "备注"] or ""))
        st.session_state.pop("lpr_wl_editor", None)
        st.toast("已保存", icon="💾")
        st.rerun()
else:
    st.caption("分析过的池子会自动记到这里；也可以在下方批量导入。")

if batch:
    rows, failures = [], []
    bar = st.progress(0.0, text="准备 …")
    for i, p in enumerate(pools, 1):
        bar.progress(i / len(pools), text=f"{i}/{len(pools)} · {p.name or p.address[:10]}")
        try:
            r = _analyze(p.address, p.network, opts)
            row = batch_row(r, 0.8 if 0.8 in opts["targets"] else opts["targets"][0])
            for h in opts["holds"]:
                v = row.get(hold_label(h))
                if isinstance(v, float) or v is None:
                    row[hold_label(h)] = _w(v)
            rows.append(row)
        except Exception as exc:  # noqa: BLE001  单个池子失败不影响其余
            failures.append({"池子": p.name or p.address, "原因": str(exc)})
    bar.empty()
    st.session_state["lpr_batch"] = (rows, failures)

if st.session_state.get("lpr_batch"):
    rows, failures = st.session_state["lpr_batch"]
    with st.container(border=True):
        st.subheader("批量汇总 · 80% 时间在区间内所需宽度")
        if rows:
            df = pd.DataFrame(rows).drop(columns=["binStep"], errors="ignore")
            st.dataframe(df, hide_index=True, width="stretch",
                         column_config={"地址": st.column_config.TextColumn(width="small")})
            st.download_button("⬇️ CSV", df.to_csv(index=False).encode("utf-8-sig"),
                               file_name="lprange_batch.csv", mime="text/csv")
        if failures:
            with st.expander(f"失败 {len(failures)} 个"):
                st.dataframe(pd.DataFrame(failures), hide_index=True, width="stretch")

with st.expander("➕ 导入池子"):
    i_l, i_r = st.columns([3, 2])
    text = i_l.text_area("每行一个地址，可带网络（如 `base:0x…`）", height=110, key="lpr_import",
                         placeholder="7qbRF6YsyGuLUVs6Y1q64bdVrfe4ZcUUz1JRdoVNUJnm\nbase:0x…")
    if i_l.button("导入地址", disabled=not text.strip()):
        added = 0
        for line in text.splitlines():
            parsed = parse_import_line(line, network)
            if parsed:
                added += wl.upsert(parsed[0], parsed[1], source="manual")
        st.toast(f"新增 {added} 个池子", icon="✅")
        st.rerun()
    i_r.caption("从 Turso 导入 monitor 正在监控的池子（表结构见 lprange/config.toml）")
    t1, t2 = i_r.columns(2)
    preview, do_import = t1.button("预览", width="stretch"), t2.button("导入", type="primary", width="stretch")
    if preview or do_import:
        try:
            rows_t, added, notes = import_turso(_cfg(), wl, dry_run=preview)
        except TursoError as exc:
            i_r.error(f"Turso 导入失败：{exc}")
        else:
            if preview:
                i_r.dataframe(pd.DataFrame(rows_t), hide_index=True, width="stretch")
            for n in notes:
                i_r.caption(n)
            if do_import:
                st.toast(f"Turso：读取 {len(rows_t)} 个，新增 {added} 个", icon="✅")
                st.rerun()

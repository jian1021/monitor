# -*- coding: utf-8 -*-
"""pages/lp_apr.py — LP 区间 APR 计算器 + 观察列表（robinhood-chain-lp-tools）.

估算（apr）秒级、回放（replay）分钟级，两者都跑本地 Node CLI。
观察列表由常驻主循环 lp_apr 模块定时估算并按阈值推飞书。
"""
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import streamlit as st

from app.core.settings import LIBSQL_TOKEN, LIBSQL_URL
from app.infrastructure.db.apr_watchlist import (
    add_apr_pool,
    list_apr_watchlist,
    remove_apr_pool,
    set_apr_pool_enabled,
)
from app.infrastructure.lp_tools import runner
from app.interfaces.monitor_controls import render_monitor_controls

st.set_page_config(page_title="LP 区间 APR", page_icon="📐", layout="wide")
st.title("📐 LP 区间 APR")
st.caption("Uniswap v3/v4 · Robinhood Chain：按你自己的区间宽度算手续费 APR（不含无常损失与 gas）。")

# ------------------------- 数据库可用性（观察列表/启停依赖它） -------------------------
DB_OK = bool(LIBSQL_URL and LIBSQL_TOKEN)
if not DB_OK:
    st.warning(
        "**未配置数据库**：观察列表、模块启停开关都存不了。"
        "请在项目根目录 `.env`（或 Streamlit secrets）里配置后重启：\n\n"
        "```bash\nLIBSQL_URL=https://xxx.turso.io\nLIBSQL_TOKEN=eyJ...\n```"
    )

render_monitor_controls("lp_apr", "LP 区间 APR")

# ------------------------- 工具链就绪检查 -------------------------
if not runner.ready():
    missing = "、".join(runner.missing_parts())
    st.warning(
        f"**工具链未就绪**（缺：{missing}）。点下面按钮一键补齐：\n\n"
        "拉取子模块 → 下载 Node 官方二进制（免 root，装到 `~/.local/share/monitor-node`）"
        "→ `npm install`，全程约 1~2 分钟。**Streamlit Cloud 不拉子模块也没有 Node，"
        "必须走这里**；容器每次重启后需要重装一次（按钮会自动检测）。"
    )
    if st.button("📦 一键安装（约 1~2 分钟）", type="primary"):
        with st.spinner("拉取代码 / 下载 Node / 安装依赖 ...（看终端日志有进度）"):
            ok = runner.ensure_installed()
        # 只在真正就绪时才 rerun：无条件 rerun 会立刻丢掉下面的成功/失败提示，
        # 安装失败时用户看不到任何报错，只能反复点同一个按钮。
        if ok and runner.ready():
            st.rerun()
        elif ok:
            st.warning("安装脚本已执行，但工具链仍未就绪，请展开下方看终端日志里的报错。")
        else:
            st.error("❌ 安装失败——展开下面看终端日志里的报错")
    with st.expander("本机手动安装（不想点按钮的话）"):
        st.code(
            "git submodule update --init\n"
            "brew install node\n"
            "cd modules/robinhood-chain-lp-tools && npm install",
            language="bash",
        )
    st.stop()

# ------------------------- 计算器 -------------------------
MODES = {"估算（秒级）": "apr", "宽度扫描": "sweep", "精确回放（分钟级）": "replay"}
with st.form("apr_calc_form", border=False):
    c_m, c_p, c_w, c_c, c_go = st.columns([1.4, 3, 0.9, 1, 0.8], vertical_alignment="bottom")
    mode_label = c_m.selectbox("模式", list(MODES))
    pool = c_p.text_input("池子（v3 地址 / v4 poolId）", placeholder="0x…")
    width = c_w.number_input("宽度 ±%", min_value=0.5, max_value=80.0, value=10.0, step=0.5)
    capital = c_c.number_input("仓位 ($)", min_value=100.0, value=10000.0, step=1000.0)
    run = c_go.form_submit_button("▶️ 运行", type="primary", width="stretch")
    with st.expander("更多参数"):
        o1, o2, o3 = st.columns(3)
        window = o1.selectbox("估算窗口", ["h24", "h6", "h1"])
        hours = o2.number_input("回放窗口（小时）", 1, 72, 24)
        measure = o3.checkbox("用链上 feeGrowth 交叉验证（估算模式）", value=True)
mode = MODES[mode_label]

if run:
    p = pool.strip()
    if not p:
        st.warning("请输入池子地址或 poolId")
    else:
        with st.spinner("执行中" + ("（回放约 1~4 分钟）" if mode == "replay" else "") + " …"):
            if mode == "apr":
                ok, res = runner.run_apr(p, width=width, capital=capital, window=window, measure=measure)
            elif mode == "sweep":
                ok, res = runner.run_apr(p, capital=capital, sweep=True)
            else:
                ok, res = runner.run_replay(p, width=width, capital=capital, hours=int(hours))
        if not ok:
            st.error(f"执行失败：\n```\n{str(res)[:3000]}\n```")
        else:
            st.session_state["apr_result"] = {"mode": mode, "res": res}

result = st.session_state.get("apr_result")
if result:
    mode_r, res = result["mode"], result["res"]
    with st.container(border=True):
        st.markdown(f"**{res.get('name', '?')}** · {res.get('version', '?')} · `{res.get('pool', '')[:14]}…`"
                    + (f" · {res.get('tokens', '')}" if res.get("tokens") else ""))
        if mode_r == "sweep":
            sweep = res.get("sweep") or []
            if sweep:
                sdf = pd.DataFrame(sweep)
                for col in ["share", "apr_in_range", "time_in_range", "vol_adjusted_apr"]:
                    if col in sdf.columns:
                        sdf[col] = sdf[col].map(lambda v: f"{v:.2%}" if isinstance(v, float) else v)
                st.dataframe(sdf, width="stretch", hide_index=True)
        else:
            if mode_r == "replay":
                metrics = [("年化 APR（实测）", res.get("annualised_apr"), True),
                           ("窗口收益", res.get("return_pct"), True),
                           ("赚到手续费 ($)", res.get("fees_earned_usd"), False),
                           ("在区间内时间", res.get("time_in_range"), True),
                           ("占全池费用", res.get("share_of_pool_fees"), True),
                           ("成交笔数", res.get("swaps"), False)]
            else:
                metrics = [("区间内 APR", res.get("apr_in_range"), True),
                           ("波动调整后 APR", res.get("vol_adjusted_apr"), True),
                           ("池均 APR", res.get("pool_avg_apr"), True),
                           ("占活跃流动性", res.get("liquidity_share"), True),
                           ("手续费/天 ($)", res.get("fees_per_day_usd"), False),
                           ("预计在区间内", res.get("time_in_range_modelled"), True)]
            cols = st.columns(len(metrics))
            for col, (label, value, is_pct) in zip(cols, metrics):
                if value is None:
                    col.metric(label, "n/a")
                elif is_pct:
                    col.metric(label, f"{value:.2%}")
                elif isinstance(value, float):
                    col.metric(label, f"{value:,.2f}")
                else:
                    col.metric(label, f"{value:,}")
            if res.get("validation_ratios"):
                st.caption("回放 vs feeGrowthGlobal 校验比值："
                           + " / ".join(f"{r:.4f}" for r in res["validation_ratios"])
                           + "（接近 1.0 说明费用计算正确）")
            if res.get("validation_warning"):
                st.warning("池子自身的 feeGrowth 没有增长——v4 hook 池通常意味着 hook 拿走了费用，"
                           "LP 实际赚不到上面的数字。")
            if res.get("dynamic_fee") or res.get("volume_method") == "unusable_dynamic_fee":
                st.info("该池为动态费率（volume × feeRate 不可用），APR 来自 feeGrowth 实测。")
        if res.get("raw"):
            with st.expander("原始输出"):
                st.code(res["raw"], language=None)

# ------------------------- 观察列表 -------------------------
rows = list_apr_watchlist()
st.subheader(f"观察列表（{len(rows)}）")
st.caption("后台定时估算；APR 越过阈值推飞书（同池同方向 6 小时去重），阈值 0 = 不告警。")

with st.form("add_apr_pool", clear_on_submit=True, border=False):
    f1, f2, f3, f4, f5, f6, f7 = st.columns([2.6, 1.4, 0.8, 1, 0.9, 0.9, 0.8], vertical_alignment="bottom")
    new_pool = f1.text_input("池子地址", placeholder="0x…")
    new_name = f2.text_input("备注名", placeholder="WETH/USDG")
    new_width = f3.number_input("宽度 ±%", min_value=0.5, value=10.0, step=0.5)
    new_capital = f4.number_input("仓位 ($)", min_value=100.0, value=10000.0, step=1000.0)
    new_min = f5.number_input("APR ≥ %", min_value=0.0, value=0.0, step=1.0)
    new_max = f6.number_input("APR ≤ %", min_value=0.0, value=0.0, step=5.0)
    if f7.form_submit_button("➕ 加入", type="primary", width="stretch"):
        if not new_pool.strip():
            st.error("❌ 池子地址不能为空")
        elif not DB_OK:
            st.error("❌ 数据库不可用：先配置 LIBSQL_URL / LIBSQL_TOKEN")
        elif add_apr_pool(new_pool, new_name, width=new_width, capital=new_capital,
                          min_apr=new_min if new_min > 0 else None,
                          max_apr=new_max if new_max > 0 else None):
            st.toast("已加入观察列表", icon="✅")
            st.rerun()
        else:
            st.error("❌ 加入失败（写库异常，看终端日志）")

if rows:
    view = pd.DataFrame([{
        "pool": r["pool"], "启用": bool(r.get("enabled")), "名称": r.get("name") or r["pool"][:10] + "…",
        "宽度": f"±{r['width']:.0f}%", "仓位": f"${r['capital']:,.0f}",
        "阈值": f"{r['min_apr'] if r['min_apr'] is not None else '—'}% ~ "
                f"{r['max_apr'] if r['max_apr'] is not None else '—'}%",
        "上次 APR": f"{r['last_apr']:.2%}" if isinstance(r.get("last_apr"), float) else "未跑",
        "上次检查": r.get("last_checked_at") or "—", "删除": False,
    } for r in rows])
    edited = st.data_editor(
        view, key="apr_wl_editor", hide_index=True, width="stretch",
        disabled=["名称", "宽度", "仓位", "阈值", "上次 APR", "上次检查"],
        column_config={"pool": None, "启用": st.column_config.CheckboxColumn(width="small"),
                       "删除": st.column_config.CheckboxColumn(width="small")},
    )
    orig, new = view.set_index("pool"), edited.set_index("pool")
    changes = [(pl, "rm" if new.loc[pl, "删除"] else "on") for pl in new.index
               if new.loc[pl, "删除"] or bool(orig.loc[pl, "启用"]) != bool(new.loc[pl, "启用"])]
    if changes:
        i_l, i_r = st.columns([4, 1], vertical_alignment="center")
        i_l.info(f"{len(changes)} 项未保存")
        if i_r.button("💾 保存修改", type="primary", width="stretch"):
            for pl, kind in changes:
                if kind == "rm":
                    remove_apr_pool(pl)
                else:
                    set_apr_pool_enabled(pl, bool(new.loc[pl, "启用"]))
            st.session_state.pop("apr_wl_editor", None)
            st.toast("已保存", icon="💾")
            st.rerun()

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
from db import get_module_settings, update_module_setting

st.set_page_config(page_title="LP 区间 APR", page_icon="📐", layout="wide")
st.title("📐 LP 区间 APR（Uniswap v3/v4 · Robinhood Chain）")
st.caption("池均 APR 是把所有流动性混在一起的数字，没人真的赚得到；这里算的是"
           "**你自己选定区间**的 APR。估算秒级但近似，回放分钟级但实测。"
           "只计手续费收益，不含无常损失与 gas。")

# ------------------------- 数据库可用性（观察列表/启停依赖它） -------------------------
DB_OK = bool(LIBSQL_URL and LIBSQL_TOKEN)
if not DB_OK:
    st.warning(
        "**未配置数据库**：观察列表、模块启停开关都存不了。"
        "请在项目根目录 `.env`（或 Streamlit secrets）里配置后重启：\n\n"
        "```bash\nLIBSQL_URL=https://xxx.turso.io\nLIBSQL_TOKEN=eyJ...\n```"
    )

# ------------------------- 模块启停 -------------------------
running = bool(get_module_settings().get("lp_apr", True))
c1, c2 = st.columns([1, 4])
with c1:
    if running:
        if st.button("⏹️ 结束监控", type="primary", use_container_width=True):
            if update_module_setting("lp_apr", False):
                st.rerun()
            else:
                st.error("保存失败：数据库不可用，开关状态没有生效")
    else:
        if st.button("▶️ 开始监控", type="primary", use_container_width=True):
            if update_module_setting("lp_apr", True):
                st.rerun()
            else:
                st.error("保存失败：数据库不可用，开关状态没有生效")
with c2:
    if not DB_OK:
        st.info("当前状态显示为默认值（监控中）——数据库不可用时无法持久化启停。")
    elif running:
        st.success("主循环 lp_apr 模块：监控中（按观察列表定时估算并推飞书）")
    else:
        st.warning("主循环 lp_apr 模块：已停止（常驻进程会跳过）")

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
        if ok:
            st.success("✅ 工具链已就绪")
        else:
            st.error("❌ 安装失败——展开下面看终端日志里的报错")
        st.rerun()
    with st.expander("本机手动安装（不想点按钮的话）"):
        st.code(
            "git submodule update --init\n"
            "brew install node\n"
            "cd modules/robinhood-chain-lp-tools && npm install",
            language="bash",
        )
    st.stop()

st.divider()

# ------------------------- 计算器 -------------------------
st.markdown("##### 🧮 计算器")
mode = st.radio("模式", ["APR 估算（秒级）", "APR vs 区间宽度扫描", "精确回放（分钟级）"],
                horizontal=True)

a1, a2, a3 = st.columns(3)
pool = a1.text_input("池子（v3 地址 40 hex / v4 poolId 64 hex）",
                     placeholder="0x...")
width = a2.number_input("区间宽度 ±%", min_value=0.5, max_value=80.0, value=10.0, step=0.5)
capital = a3.number_input("仓位规模 ($)", min_value=100.0, value=10000.0, step=1000.0)

b1, b2 = st.columns(2)
window = b1.selectbox("估算窗口", ["h24", "h6", "h1"], index=0) if mode != "精确回放（分钟级）" else None
hours = b1.number_input("回放窗口（小时）", 1, 72, 24) if mode == "精确回放（分钟级）" else None
measure = b2.checkbox("用链上 feeGrowth 交叉验证", value=(mode != "APR vs 区间宽度扫描")) \
    if mode != "精确回放（分钟级）" else None

run = st.button("▶️ 运行", type="primary", disabled=not pool.strip())
if run:
    p = pool.strip()
    with st.spinner("执行中" + ("（回放可能需要 1~4 分钟）" if mode == "精确回放（分钟级）" else "") + "..."):
        if mode == "APR 估算（秒级）":
            ok, res = runner.run_apr(p, width=width, capital=capital, window=window, measure=measure)
        elif mode == "APR vs 区间宽度扫描":
            ok, res = runner.run_apr(p, capital=capital, sweep=True)
        else:
            ok, res = runner.run_replay(p, width=width, capital=capital, hours=int(hours))
    if not ok:
        st.error(f"执行失败：\n```\n{str(res)[:3000]}\n```")
    else:
        st.session_state["apr_result"] = {"mode": mode, "res": res}
        st.rerun()

result = st.session_state.get("apr_result")
if result:
    mode, res = result["mode"], result["res"]
    st.markdown(f"**{res.get('name', '?')}** · {res.get('version', '?')} · `{res.get('pool', '')[:14]}…`"
                + (f" · {res.get('tokens', '')}" if res.get("tokens") else ""))

    if mode == "APR vs 区间宽度扫描":
        sweep = res.get("sweep") or []
        if sweep:
            sdf = pd.DataFrame(sweep)
            for col in ["share", "apr_in_range", "time_in_range", "vol_adjusted_apr"]:
                if col in sdf.columns:
                    sdf[col] = sdf[col].map(lambda v: f"{v:.2%}" if isinstance(v, float) else v)
            st.dataframe(sdf, width="stretch", hide_index=True)
    else:
        metrics = []
        if mode == "精确回放（分钟级）":
            metrics = [
                ("年化 APR（实测）", res.get("annualised_apr"), True),
                ("窗口收益", res.get("return_pct"), True),
                ("赚到手续费 ($)", res.get("fees_earned_usd"), False),
                ("在区间内时间", res.get("time_in_range"), True),
                ("占全池费用", res.get("share_of_pool_fees"), True),
                ("成交笔数", res.get("swaps"), False),
            ]
        else:
            metrics = [
                ("区间内 APR", res.get("apr_in_range"), True),
                ("波动调整后 APR", res.get("vol_adjusted_apr"), True),
                ("池均 APR", res.get("pool_avg_apr"), True),
                ("占活跃流动性", res.get("liquidity_share"), True),
                ("手续费/天 ($)", res.get("fees_per_day_usd"), False),
                ("预计在区间内", res.get("time_in_range_modelled"), True),
            ]
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
            st.warning("校验警告：池子自身的 feeGrowth 没有增长——v4 hook 池通常意味着 "
                       "hook 拿走了费用，LP 实际赚不到上面的数字。")
        if res.get("dynamic_fee") or res.get("volume_method") == "unusable_dynamic_fee":
            st.info("该池为动态费率（volume × feeRate 不可用），APR 来自 feeGrowth 实测。")

    if res.get("raw"):
        with st.expander("原始输出"):
            st.code(res["raw"], language=None)

st.divider()

# ------------------------- 观察列表 -------------------------
st.markdown("##### 👀 观察列表（主循环定时估算）")
st.caption("APR 越过阈值时推飞书（同池同方向 6 小时去重）；阈值留空 = 不告警。")

with st.form("add_apr_pool", clear_on_submit=True):
    f1, f2, f3, f4 = st.columns([3, 2, 1, 1])
    new_pool = f1.text_input("池子地址", placeholder="0x...")
    new_name = f2.text_input("备注名", placeholder="WETH/USDG")
    new_width = f3.number_input("宽度 ±%", min_value=0.5, value=10.0, step=0.5)
    new_capital = f4.number_input("仓位 ($)", min_value=100.0, value=10000.0, step=1000.0)
    t1, t2, t3 = st.columns([1, 1, 3])
    new_min = t1.number_input("APR 下限 (%)", min_value=0.0, value=0.0, step=1.0,
                              help="0 = 不设下限")
    new_max = t2.number_input("APR 上限 (%)", min_value=0.0, value=0.0, step=5.0,
                              help="0 = 不设上限")
    submitted = t3.form_submit_button("➕ 加入列表", use_container_width=True)
    if submitted:
        if not new_pool.strip():
            st.error("❌ 池子地址不能为空")
        elif not DB_OK:
            st.error("❌ 数据库不可用：先按上方提示配置 LIBSQL_URL / LIBSQL_TOKEN")
        else:
            ok = add_apr_pool(
                new_pool, new_name, width=new_width, capital=new_capital,
                min_apr=new_min if new_min > 0 else None,
                max_apr=new_max if new_max > 0 else None,
            )
            if ok:
                st.success("✅ 已加入观察列表")
                st.rerun()
            else:
                st.error("❌ 加入失败（写库异常，看终端日志）")

rows = list_apr_watchlist()
if not rows:
    st.info("列表为空——加入池子后，主循环每小时估算一次并在越界时推飞书。")
else:
    for row in rows:
        r1, r2, r3, r4, r5, r6 = st.columns([3, 2, 2, 2, 1, 1])
        r1.write(f"`{row['pool'][:14]}…` {row.get('name') or ''}")
        r2.write(f"±{row['width']:.0f}% · ${row['capital']:,.0f}")
        r3.write(f"阈值 {row['min_apr'] if row['min_apr'] is not None else '—'}%"
                 f" ~ {row['max_apr'] if row['max_apr'] is not None else '—'}%")
        last = (f"{row['last_apr']:.2%}" if isinstance(row.get("last_apr"), float) else "未跑")
        r4.write(f"上次：{last}" + (f"（{row['last_checked_at']}）" if row.get("last_checked_at") else ""))
        enabled = r5.toggle("启", value=bool(row.get("enabled")), key=f"apr_on_{row['pool']}")
        if enabled != bool(row.get("enabled")):
            set_apr_pool_enabled(row["pool"], enabled)
            st.rerun()
        if r6.button("🗑️", key=f"apr_del_{row['pool']}"):
            remove_apr_pool(row["pool"])
            st.rerun()

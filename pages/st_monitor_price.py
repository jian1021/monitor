"""价格监控 (st_monitor_price)

一个输入框完成「查询 → 添加」，一张可编辑表格完成「启停 / 改目标价 / 改频率 / 删除」。
支持：链上代币（Dexscreener）、A股 / 场内 ETF、可转债（腾讯行情）。

分层：建表 / 规则 CRUD / 价格回写全部走 app/application/monitors/price.py，
本页只负责渲染与交互，不拼 SQL。
"""
import math
from datetime import time as dtime

import pandas as pd
import streamlit as st

from app.core.settings import FEISHU_WEBHOOK
from app.application.monitors import price as price_service
from app.application.monitors.price import RULE_COLUMNS
from app.infrastructure.market_data.dexscreener import fetch_token_info
from app.infrastructure.market_data.a_share import fetch_realtime_price, is_convertible_bond
from app.interfaces.asset_config import CHAIN_LABELS, TOKEN_CHAINS
from app.interfaces.timefmt import to_local_display

CHAIN_OPTIONS = TOKEN_CHAINS  # 唯一来源：app/interfaces/asset_config.py
TYPE_LABELS = {"token": "🪙 代币", "ashare": "📈 A股/ETF", "cbond": "💴 可转债"}
DIR_LABELS = {"gte": "≥", "lte": "≤"}


# ============================================================
# 工具
# ============================================================
def detect_type(text: str) -> str:
    """6 位数字 → 可转债 / A股ETF；其余视为链上代币地址。"""
    t = (text or "").strip()
    if len(t) == 6 and t.isdigit():
        return "cbond" if is_convertible_bond(t) else "ashare"
    return "token"


def lookup(text: str, chain: str) -> dict | None:
    """统一查询：返回 {type, code, chain, name, price}；查不到返回 None。"""
    code = (text or "").strip()
    kind = detect_type(code)
    if kind == "token":
        if len(code) < 20:
            return None
        info, _src = fetch_token_info(chain, code)
        if not info:
            return None
        raw = (info.get("price") or {}).get("price") or info.get("price_usd")
        try:
            px = float(raw)
        except (TypeError, ValueError):
            px = None
        name = str(info.get("symbol") or info.get("name") or "").strip()
        return {"type": kind, "code": code, "chain": chain, "name": name, "price": px}
    px, name = fetch_realtime_price(code)
    if not name:
        return None
    return {"type": kind, "code": code, "chain": "", "name": name, "price": px}


def price_fmt(px: float | None) -> tuple[float, str]:
    """number_input 的步长（约千分之一价格）与显示精度。"""
    if not px or px <= 0:
        return 0.000001, "%.8f"
    exp = math.floor(math.log10(px)) - 3
    return 10.0 ** exp, f"%.{max(2, -exp)}f"


def fmt_px(v) -> str:
    try:
        return f"{float(v):.6g}"
    except (TypeError, ValueError):
        return "—"


def parse_hhmm(text: str | None) -> str | None:
    return price_service.normalize_daily_check(text)


def fail(msg: str) -> None:
    st.error(f"❌ {msg}，请检查数据库连接。")


# ============================================================
# 页面
# ============================================================
st.set_page_config(page_title="价格监控", page_icon="📈", layout="wide")

try:
    if not price_service.ensure_table():
        st.error("❌ 数据库不可用，无法加载价格监控。")
        st.stop()
except Exception as e:
    st.error(f"❌ 初始化 price_alert 表失败: {e}")
    st.stop()

head_l, head_r = st.columns([4, 1])
head_l.title("📈 价格监控")
head_l.caption("输入代码或合约地址查询，设定目标价后加入监控；到价通过飞书推送。")
if not FEISHU_WEBHOOK:
    head_r.warning("未配置飞书 Webhook，告警仅打印日志", icon="🔕")

# ---------------- 查询 + 添加 ----------------
with st.container(border=True):
    with st.form("lookup_form", border=False):
        c_in, c_chain, c_btn = st.columns([5, 2, 1], vertical_alignment="bottom")
        q_text = c_in.text_input(
            "代码 / 合约地址",
            placeholder="A股/ETF 如 159967 · 可转债 如 113052 · 链上代币粘贴合约地址",
        )
        q_chain = c_chain.selectbox("公链（仅代币）", CHAIN_OPTIONS,
                                    format_func=lambda x: CHAIN_LABELS[x])
        go = c_btn.form_submit_button("🔍 查询", type="primary", width="stretch")

    if go:
        with st.spinner("查询中 ..."):
            res = lookup(q_text, q_chain)
        if not res:
            kind = detect_type(q_text)
            st.session_state.pop("found", None)
            st.error("❌ 未查到：" + ("请核对合约地址与公链" if kind == "token"
                                   else "代码不存在或行情接口不可用"))
        else:
            st.session_state["found"] = res

    found = st.session_state.get("found")
    if found:
        unit = "USD" if found["type"] == "token" else ("元/张" if found["type"] == "cbond" else "元")
        where = CHAIN_LABELS.get(found["chain"], "") if found["type"] == "token" else found["code"]
        px_text = "停牌 / 无报价" if found["price"] is None else f"{found['price']:.10g} {unit}"
        st.markdown(f"**{found['name'] or '未知'}** · {TYPE_LABELS[found['type']]} · {where}"
                    f"　现价 **{px_text}**")

        step, fmt = price_fmt(found["price"])
        with st.form("add_form", border=False):
            f1, f2, f3, f4 = st.columns([2, 2, 2, 1], vertical_alignment="bottom")
            target = f1.number_input("目标价", min_value=0.0, value=float(found["price"] or 0.0),
                                     step=step, format=fmt,
                                     key=f"tgt_{found['type']}_{found['code']}")
            direction = f2.segmented_control("触发", options=["gte", "lte"], default="gte",
                                             format_func=lambda x: "📈 涨到" if x == "gte" else "📉 跌到")
            default_daily = found["type"] != "token"
            check_at = f3.text_input("每日检查（留空=轮询）",
                                     value="09:30" if default_daily else "",
                                     placeholder="HH:MM")
            add = f4.form_submit_button("➕ 添加", type="primary", width="stretch")
            if target > 0 and found["price"]:
                st.caption(f"目标较现价 {(target / found['price'] - 1) * 100:+.2f}%")

        if add:
            if target <= 0:
                st.warning("目标价必须大于 0")
            elif check_at.strip() and not parse_hhmm(check_at):
                st.warning("每日检查时刻格式应为 HH:MM，如 09:30")
            elif price_service.add_rule(found["code"], found["chain"], found["name"], target,
                                        direction or "gte", asset_type=found["type"],
                                        daily_check_at=parse_hhmm(check_at)):
                st.session_state.pop("found", None)
                st.toast(f"已添加 {found['name'] or found['code']}  "
                         f"{DIR_LABELS[direction or 'gte']} {target:g}", icon="✅")
                st.rerun()
            else:
                fail("添加失败")

# ---------------- 监控列表 ----------------
try:
    df = pd.DataFrame(price_service.load_all_rules(), columns=list(RULE_COLUMNS))
except Exception as e:
    st.error(f"❌ 读取规则失败: {e}")
    st.stop()

if df.empty:
    st.info("还没有监控规则，在上方查询后添加。")
    st.stop()

n_on = int(df["enabled"].sum())
n_hit = int((df["enabled"] & df["alerted"]).sum())
bar_l, bar_r = st.columns([4, 1], vertical_alignment="bottom")
bar_l.subheader(f"监控列表　{n_on}/{len(df)} 启用 · {n_hit} 已触发")
refresh = bar_r.button("🔄 刷新现价", width="stretch",
                       help="立即查询所有启用规则的现价（不影响每日定时检查）")

if refresh:
    with st.spinner("刷新中 ..."):
        bad = 0
        for _, r in df[df["enabled"]].iterrows():
            px, _ = price_service.fetch_price(r["chain"] or "", r["address"],
                                              asset_type=r["asset_type"] or "token")
            if px is None:
                bad += 1
                continue
            price_service.update_rule_price(int(r["id"]), px, touch_checked=False)
    st.toast("现价已刷新" + (f"，{bad} 条取价失败" if bad else ""), icon="🔄")
    st.rerun()


def _gap(r) -> float | None:
    try:
        last, tgt = float(r["last_price"]), float(r["target_price"])
    except (TypeError, ValueError):
        return None
    if not last or math.isnan(last):
        return None
    return (tgt / last - 1) * 100


view = pd.DataFrame({
    "id": df["id"],
    "启用": df["enabled"].astype(bool),
    "名称": df["symbol"].where(df["symbol"].astype(bool), df["address"].str[:10] + "…"),
    "类型": df["asset_type"].map(TYPE_LABELS).fillna(TYPE_LABELS["token"]),
    "代码/地址": df["address"],
    "条件": df["direction"].map({"gte": "📈 ≥", "lte": "📉 ≤"}),
    "目标价": df["target_price"].astype(float),
    "现价": df["last_price"].apply(fmt_px),
    "距目标": df.apply(_gap, axis=1),
    "状态": df.apply(lambda r: "🔔 已触发" if r["enabled"] and r["alerted"]
                     else ("🟢 监控中" if r["enabled"] else "⏸️ 停用"), axis=1),
    "每日检查": df["daily_check_at"].fillna(""),
    "最近检查": df["last_checked_at"].apply(lambda v: to_local_display(v, fallback="—")),
    "删除": False,
})

edited = st.data_editor(
    view,
    key="rules_editor",
    hide_index=True,
    width="stretch",
    column_order=["启用", "名称", "类型", "条件", "目标价", "现价", "距目标", "状态",
                  "每日检查", "最近检查", "代码/地址", "删除"],
    disabled=["名称", "类型", "现价", "距目标", "状态", "最近检查", "代码/地址"],
    column_config={
        "id": None,
        "启用": st.column_config.CheckboxColumn(width="small"),
        "条件": st.column_config.SelectboxColumn(options=["📈 ≥", "📉 ≤"], width="small",
                                               required=True),
        "目标价": st.column_config.NumberColumn(format="%.6g", min_value=0.0),
        "距目标": st.column_config.NumberColumn(format="%+.2f%%", width="small",
                                              help="目标价相对现价的涨跌幅"),
        "每日检查": st.column_config.TextColumn(help="HH:MM 每天定时检查一次；留空跟随轮询",
                                              width="small"),
        "代码/地址": st.column_config.TextColumn(width="medium"),
        "删除": st.column_config.CheckboxColumn(width="small"),
    },
)

# ---------------- 保存修改 ----------------
orig = view.set_index("id")
new = edited.set_index("id")
changes = []
for rid in new.index:
    a, b = orig.loc[rid], new.loc[rid]
    if b["删除"]:
        changes.append(("delete", rid, None))
        continue
    if bool(a["启用"]) != bool(b["启用"]):
        changes.append(("enabled", rid, bool(b["启用"])))
    dir_new = "gte" if b["条件"] == "📈 ≥" else "lte"
    if a["条件"] != b["条件"] or float(a["目标价"]) != float(b["目标价"] or 0):
        changes.append(("target", rid, (float(b["目标价"] or 0), dir_new)))
    if (a["每日检查"] or "") != (b["每日检查"] or ""):
        changes.append(("schedule", rid, b["每日检查"]))

if changes:
    n_del = sum(1 for c in changes if c[0] == "delete")
    s_l, s_r = st.columns([4, 1], vertical_alignment="center")
    s_l.info(f"有 {len(changes)} 项未保存的修改" + (f"，其中删除 {n_del} 条（不可恢复）" if n_del else ""))
    if s_r.button("💾 保存修改", type="primary", width="stretch"):
        errors = []
        for kind, rid, val in changes:
            rid = int(rid)
            if kind == "delete":
                ok = price_service.delete_rule(rid)
            elif kind == "enabled":
                ok = price_service.set_rule_enabled(rid, val)
            elif kind == "target":
                ok = price_service.update_rule_target(rid, *val)
            else:
                if (val or "").strip() and not parse_hhmm(val):
                    errors.append(f"#{rid} 每日检查格式应为 HH:MM")
                    continue
                ok = price_service.update_rule_schedule(rid, parse_hhmm(val))
            if not ok:
                errors.append(f"#{rid} {kind} 保存失败")
        st.session_state.pop("rules_editor", None)
        if errors:
            st.error("；".join(errors))
        else:
            st.toast("已保存", icon="💾")
            st.rerun()

st.caption("勾选「启用」「删除」，或直接改目标价 / 条件 / 每日检查后点保存。"
           "改目标价或条件会重置「已触发」状态。后台由 `python main.py` 调度运行。")

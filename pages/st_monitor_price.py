"""价格监控配置页 (st_monitor_price)

功能:
  - 新增价格监控规则: 输入 token 地址 + 所属公链 + 目标价 + 方向
  - 查看规则列表 / 当前价格 / 最近检查时间 / 是否已触发
  - 启用/停用/删除规则
数据表: price_alert (Turso/libSQL)

分层：建表 / 规则 CRUD / 价格回写全部走 app/application/monitors/price.py，
本页只负责渲染与交互，不再自己拼 SQL。
"""
import streamlit as st
import pandas as pd
from datetime import time as dtime

from app.core.settings import FEISHU_WEBHOOK
from app.application.monitors import price as price_service
from app.application.monitors.price import RULE_COLUMNS
from app.infrastructure.market_data.dexscreener import fetch_token_info
from app.infrastructure.market_data.a_share import fetch_realtime_price
from app.interfaces.asset_config import CHAIN_LABELS, TOKEN_CHAINS
from app.interfaces.timefmt import to_local_display

# 链选项唯一来源：app/interfaces/asset_config.py（TOKEN_CHAINS / CHAIN_LABELS）
CHAIN_OPTIONS = TOKEN_CHAINS


# ============================================================
# 数据访问：全部委托 application 层，页面只做错误呈现
# ============================================================
def ensure_table() -> bool:
    try:
        if price_service.ensure_table():
            return True
        st.error("❌ 初始化 price_alert 表失败：数据库连接不可用")
    except Exception as e:
        st.error(f"❌ 初始化 price_alert 表失败: {e}")
    return False


def fetch_rules() -> pd.DataFrame:
    try:
        rows = price_service.load_all_rules()
    except Exception as e:
        st.error(f"❌ 读取规则失败: {e}")
        return pd.DataFrame(columns=list(RULE_COLUMNS))
    return pd.DataFrame(rows, columns=list(RULE_COLUMNS))


def add_rule(address: str, chain: str, symbol: str, target_price: float,
             direction: str, asset_type: str = "token",
             daily_check_at: str | None = None) -> bool:
    if price_service.add_rule(address, chain, symbol, target_price, direction,
                              asset_type, daily_check_at=daily_check_at):
        return True
    st.error("❌ 新增规则失败，请检查数据库连接。")
    return False


def update_rule_status(rule_id: int, enabled: bool) -> bool:
    if price_service.set_rule_enabled(rule_id, enabled):
        return True
    st.error("❌ 更新规则状态失败，请检查数据库连接。")
    return False


def delete_rule(rule_id: int) -> bool:
    if price_service.delete_rule(rule_id):
        return True
    st.error("❌ 删除规则失败，请检查数据库连接。")
    return False


# ============================================================
# 界面
# ============================================================
st.set_page_config(page_title="价格监控", page_icon="📈", layout="wide")
st.title("📈 价格监控")
st.caption("按 Token 地址 + 公链，或 A股 / 场内 ETF 代码监控实时价格，到达目标价时通过飞书推送告警。")

if not ensure_table():
    st.stop()

# ---- 顶部告警通道状态 ----
if FEISHU_WEBHOOK:
    st.success("✅ 飞书告警通道已配置", icon="🔔")
else:
    st.warning("⚠️ 未配置 FEISHU_WEBHOOK，告警仅打印到控制台", icon="🔕")

st.caption("数据源: 链上代币走 Dexscreener；A股 / ETF 走腾讯行情（盘中实时，收盘后为最新收盘价）")

st.divider()

# ================= 新增规则：先查询，再添加 =================
def _token_lookup(chain: str, address: str) -> dict | None:
    """链上代币：返回 {name, symbol, price}；取不到返回 None。"""
    info, _source = fetch_token_info(chain, address)
    if not info:
        return None
    raw = (info.get("price") or {}).get("price")
    if raw is None:
        raw = info.get("price_usd")
    try:
        px = float(raw)
    except (TypeError, ValueError):
        px = None
    return {"name": str(info.get("name") or "").strip(),
            "symbol": str(info.get("symbol") or "").strip(),
            "price": px}


def _price_step(px: float | None) -> tuple[float, str]:
    """按价格量级给出步长（约千分之一价格）与显示精度。"""
    import math
    if not px or px <= 0:
        return 0.000001, "%.8f"
    exp = math.floor(math.log10(px)) - 3
    step = 10.0 ** exp
    decimals = max(2, -exp)
    return step, f"%.{decimals}f"


with st.expander("➕ 新增价格监控规则", expanded=True):
    tab_token, tab_ashare = st.tabs(["🪙 链上代币", "📈 A股 / 场内 ETF"])

    # ---------------- 链上代币 ----------------
    with tab_token:
        q1, q2, q3 = st.columns([3, 1, 1])
        q_address = q1.text_input(
            "Token 合约地址 *",
            placeholder="例如: EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            key="tok_q_address",
        )
        q_chain = q2.selectbox("所属公链", options=CHAIN_OPTIONS,
                               format_func=lambda x: CHAIN_LABELS[x], key="tok_q_chain")
        q3.write("")
        q3.write("")
        if q3.button("🔍 查询", key="tok_q_btn", width="stretch"):
            addr = q_address.strip()
            if len(addr) < 20:
                st.warning("⚠️ 请输入有效的 Token 合约地址")
                st.session_state.pop("tok_lookup", None)
            else:
                with st.spinner("正在查询 ..."):
                    res = _token_lookup(q_chain, addr)
                if not res or res["price"] is None:
                    st.error("❌ 查询失败：请核对地址与所属公链")
                    st.session_state.pop("tok_lookup", None)
                else:
                    st.session_state["tok_lookup"] = {**res, "address": addr, "chain": q_chain}

        found = st.session_state.get("tok_lookup")
        # 地址或公链改了，旧查询结果作废
        if found and (found["address"] != q_address.strip() or found["chain"] != q_chain):
            found = None
            st.session_state.pop("tok_lookup", None)

        if found:
            m1, m2, m3 = st.columns(3)
            m1.metric("名称", found["name"] or "—")
            m2.metric("符号", found["symbol"] or "—")
            m3.metric("当前价格 (USD)", f"{found['price']:.10g}")

            step, fmt = _price_step(found["price"])
            with st.form("add_price_rule_form"):
                f1, f2 = st.columns(2)
                with f1:
                    new_target = st.number_input(
                        "目标价格 *", min_value=0.0, value=float(found["price"]),
                        step=step, format=fmt,
                        key=f"tok_target_{found['address']}_{found['chain']}")
                    new_direction = st.radio(
                        "触发条件", options=["gte", "lte"],
                        format_func=lambda x: "📈 价格 ≥ 目标价时报警" if x == "gte" else "📉 价格 ≤ 目标价时报警",
                        horizontal=True, key="token_direction")
                with f2:
                    new_symbol = st.text_input("显示名称", value=found["symbol"] or found["name"])
                    tok_daily = st.checkbox("每天定时检查一次（不勾选则跟随轮询间隔）",
                                            value=False, key="token_daily_check")
                    tok_check_time = st.time_input("每日检查时刻（北京时间）", value=dtime(9, 30),
                                                   key="token_check_time")
                if new_target > 0 and found["price"]:
                    pct = (new_target / found["price"] - 1) * 100
                    st.caption(f"目标价较现价 {pct:+.2f}%")

                if st.form_submit_button("✅ 添加到监控列表", type="primary"):
                    if new_target <= 0:
                        st.warning("⚠️ 目标价格必须大于 0")
                    else:
                        daily_check = tok_check_time.strftime("%H:%M") if tok_daily else None
                        if add_rule(found["address"], found["chain"], new_symbol.strip(),
                                    new_target, new_direction, daily_check_at=daily_check):
                            st.session_state.pop("tok_lookup", None)
                            st.toast(f"已添加: {new_symbol or found['address'][:10]} "
                                     f"({CHAIN_LABELS[found['chain']]}) 目标 {new_target}", icon="✅")
                            st.rerun()
        else:
            st.caption("输入地址并选择公链后点「查询」，确认名称和现价后再设置目标价。")

    # ---------------- A股 / 场内 ETF ----------------
    with tab_ashare:
        a1, a2 = st.columns([4, 1])
        q_code = a1.text_input("6 位代码 *", placeholder="例如: 159967 / 515790 / 600519",
                               key="ash_q_code")
        a2.write("")
        a2.write("")
        if a2.button("🔍 查询", key="ash_q_btn", width="stretch"):
            code = q_code.strip()
            if len(code) != 6 or not code.isdigit():
                st.warning("⚠️ 请输入 6 位数字代码（如 159967）")
                st.session_state.pop("ash_lookup", None)
            else:
                with st.spinner("正在查询 ..."):
                    px, name = fetch_realtime_price(code)
                if not name:
                    st.error("❌ 查询失败：代码不存在或行情接口不可用")
                    st.session_state.pop("ash_lookup", None)
                else:
                    st.session_state["ash_lookup"] = {"code": code, "name": name, "price": px}

        found = st.session_state.get("ash_lookup")
        if found and found["code"] != q_code.strip():
            found = None
            st.session_state.pop("ash_lookup", None)

        if found:
            m1, m2 = st.columns(2)
            m1.metric("名称", found["name"])
            m2.metric("当前价格 (元)", "停牌 / 无报价" if found["price"] is None else f"{found['price']:.3f}")

            with st.form("add_ashare_rule_form"):
                f1, f2 = st.columns(2)
                with f1:
                    ash_target = st.number_input(
                        "目标价格 *", min_value=0.0, value=float(found["price"] or 0.0),
                        step=0.001, format="%.3f", key=f"ash_target_{found['code']}")
                    ash_direction = st.radio(
                        "触发条件", options=["gte", "lte"],
                        format_func=lambda x: "📈 价格 ≥ 目标价时报警" if x == "gte" else "📉 价格 ≤ 目标价时报警",
                        horizontal=True, key="ashare_direction")
                with f2:
                    ash_daily = st.checkbox("每天定时检查一次", value=True, key="ashare_daily_check",
                                            help="默认每天开盘 09:30（北京时间）检查一次；"
                                                 "不勾选则跟随调度轮询间隔（默认 5 分钟）")
                    ash_check_time = st.time_input("每日检查时刻（北京时间）", value=dtime(9, 30),
                                                   key="ashare_check_time")
                if ash_target > 0 and found["price"]:
                    st.caption(f"目标价较现价 {(ash_target / found['price'] - 1) * 100:+.2f}%")

                if st.form_submit_button("✅ 添加到监控列表", type="primary"):
                    if ash_target <= 0:
                        st.warning("⚠️ 目标价格必须大于 0")
                    else:
                        daily_check = ash_check_time.strftime("%H:%M") if ash_daily else None
                        if add_rule(found["code"], "", found["name"], ash_target, ash_direction,
                                    asset_type="ashare", daily_check_at=daily_check):
                            st.session_state.pop("ash_lookup", None)
                            freq = f"每天 {daily_check}" if daily_check else "跟随轮询"
                            st.toast(f"已添加: {found['code']} {found['name']} 目标 {ash_target}（{freq}）",
                                     icon="✅")
                            st.rerun()
        else:
            st.caption("输入代码后点「查询」，确认名称和现价后再设置目标价。")

st.divider()

# ================= 现有规则列表 =================
df = fetch_rules()
if df.empty:
    st.info("ℹ️ 暂无价格监控规则，请在上方新增。")
    st.stop()

col1, col2, col3, col4 = st.columns(4)
col1.metric("总规则数", len(df))
col2.metric("启用中", len(df[df["enabled"] == True]))
col3.metric("已触发", len(df[df["alerted"] == True]))
col4.metric("待触发", len(df[(df["enabled"] == True) & (df["alerted"] == False)]))

st.divider()

# 展示用副本（隐藏内部字段 id/alerted）
view_df = df.copy()

# 状态列文本化
view_df["状态"] = view_df.apply(
    lambda r: "🟢 监控中" if (r["enabled"] and not r["alerted"])
    else ("🔔 已触发" if (r["enabled"] and r["alerted"]) else "⏸️ 已停用"),
    axis=1,
)
view_df["方向"] = view_df["direction"].map(
    {"gte": "📈 ≥", "lte": "📉 ≤"}
)
view_df["类型"] = view_df["asset_type"].map(
    {"ashare": "📈 A股/ETF", "token": "🪙 链上代币"}
).fillna("🪙 链上代币")
view_df["chain"] = view_df["chain"].fillna("").replace("", "—")
view_df["目标价"] = view_df["target_price"]
view_df["当前价"] = view_df["last_price"].fillna("-")
view_df["最近检查"] = view_df["last_checked_at"].apply(lambda v: to_local_display(v, fallback="—"))
view_df["检查频率"] = view_df["daily_check_at"].apply(
    lambda v: f"每天 {v}" if v else "跟随轮询"
)

# 列筛选
show_cols = {
    "状态": "状态",
    "类型": "类型",
    "chain": "公链",
    "symbol": "符号",
    "address": "地址",
    "方向": "方向",
    "目标价": "目标价",
    "当前价": "当前价",
    "检查频率": "检查频率",
    "最近检查": "最近检查",
    "created_at": "创建时间",
}
columns_cfg = {
    "id": None,
    "chain": st.column_config.TextColumn("公链", disabled=True, width="small"),
    "symbol": st.column_config.TextColumn("符号", disabled=True),
    "address": st.column_config.TextColumn("地址", disabled=True, width="large"),
    "direction": None,
    "target_price": st.column_config.NumberColumn("目标价", disabled=True, format="%.8f"),
    "enabled": None,
    "alerted": None,
    "last_price": st.column_config.NumberColumn("当前价", disabled=True, format="%.8f"),
    "last_checked_at": st.column_config.TextColumn("最近检查", disabled=True),
    "created_at": st.column_config.DatetimeColumn("创建时间", disabled=True, format="YYYY-MM-DD HH:mm"),
}

st.dataframe(
    view_df[list(show_cols.keys())].rename(columns=show_cols),
    column_config=columns_cfg,
    width="stretch",
    hide_index=True,
)

st.divider()

# ================= 管理操作 =================
st.subheader("🛠️ 规则管理")

# 手动触发一次查价 (Dexscreener)
with st.expander("🔍 立即检查选中规则价格"):
    check_sel = st.selectbox(
        "选择要检查的规则",
        options=df["id"].tolist(),
        format_func=lambda i: f"[{i}] {df.loc[df['id']==i,'symbol'].iloc[0] or df.loc[df['id']==i,'address'].iloc[0][:16]}... ({df.loc[df['id']==i,'chain'].iloc[0]})",
    )
    if st.button("检查价格"):
        check_id = int(check_sel)
        rule = df[df["id"] == check_id].iloc[0]
        with st.spinner("正在获取价格 ..."):
            price, fetched_symbol = price_service.fetch_price(
                rule["chain"] or "", rule["address"],
                asset_type=rule.get("asset_type") or "token")
        if price is None:
            st.error("❌ 取价失败：链上代币请检查地址/公链；A 股请检查代码是否正确（停牌时无报价）")
        else:
            sym = fetched_symbol or rule["symbol"] or rule["address"]
            st.success(f"✅ {sym} 当前价格: {price}")
            # 回写最新价格与检查时间（application 层统一实现）
            # 手动查价只更新价格，不推进 last_checked_at，避免吃掉当天的定时检查
            price_service.update_rule_price(check_id, price, touch_checked=False)

with st.expander("⚙️ 启用 / 停用 / 删除 / 检查频率", expanded=True):
    manage_sel = st.selectbox(
        "选择规则 ID",
        options=df["id"].tolist(),
        key="manage_sel",
        format_func=lambda i: f"[{i}] {df.loc[df['id']==i,'symbol'].iloc[0] or df.loc[df['id']==i,'address'].iloc[0][:16]}...",
    )
    manage_id = int(manage_sel)
    rule_row = df[df["id"] == manage_id].iloc[0]

    m1, m2, m3 = st.columns(3)
    with m1:
        if rule_row["enabled"]:
            if st.button("⏸️ 停用", width="stretch"):
                if update_rule_status(manage_id, False):
                    st.toast("已停用", icon="⏸️")
                    st.rerun()
        else:
            if st.button("▶️ 启用", width="stretch"):
                if update_rule_status(manage_id, True):
                    st.toast("已启用", icon="▶️")
                    st.rerun()
    with m2:
        st.caption(f"当前: {'已触发' if rule_row['alerted'] else '未触发'} / 目标 {'≥' if rule_row['direction']=='gte' else '≤'} {rule_row['target_price']}")
    with m3:
        with st.popover("🗑️ 删除规则", width="stretch"):
            st.write("⚠️ 删除后不可恢复，确认删除该规则？")
            if st.button("确认删除", type="primary"):
                if delete_rule(manage_id):
                    st.toast("已删除", icon="🗑️")
                    st.rerun()

    # ---- 检查频率修改 ----
    cur_daily = rule_row.get("daily_check_at") or None
    st.caption(f"📅 当前检查频率: {'每天 ' + cur_daily if cur_daily else '跟随轮询间隔'}")
    edit_daily = st.checkbox("每天定时检查一次", value=bool(cur_daily),
                             key="edit_daily_check")
    default_time = (dtime(*[int(x) for x in cur_daily.split(":")])
                    if cur_daily else dtime(9, 30))
    edit_time = st.time_input("每日检查时刻（北京时间）", value=default_time,
                              key="edit_check_time", disabled=not edit_daily)
    if st.button("💾 保存检查频率"):
        new_val = edit_time.strftime("%H:%M") if edit_daily else None
        if price_service.update_rule_schedule(manage_id, new_val):
            st.toast(f"检查频率已更新: {f'每天 {new_val}' if new_val else '跟随轮询'}",
                     icon="✅")
            st.rerun()
        else:
            st.error("❌ 更新失败，请检查数据库连接。")

# ================= 运行说明 =================
st.divider()
with st.expander("📖 运行说明"):
    st.markdown(
        """
**后台监控脚本 (独立运行):**

```bash
# 手动执行一轮检查:
python -m scripts.monitor_price

# 本地常驻轮询 (每 60 秒):
python -m scripts.monitor_price --loop
python -m scripts.monitor_price --loop --interval 300   # 自定义间隔(秒)
```

**告警去重**：同一规则达标后标记为「已触发」，避免重复轰炸；
价格离开目标价后自动重置，再次达标会重新推送。

**数据源**：Dexscreener API，无需配置 API Key 或代理。
"""
    )
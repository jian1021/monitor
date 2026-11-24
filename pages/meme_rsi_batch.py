import pandas as pd
import streamlit as st

from app.application.monitors import meme_rsi_batch as mrb
from app.infrastructure.db.meme_watchlist import (
    add_meme_pools, get_meme_rsi_params, list_meme_watchlist_pools,
    remove_meme_pool, set_meme_pool_enabled, update_meme_rsi_params,
)
from app.interfaces.monitor_controls import render_monitor_controls

TF_LABELS = {"15m": "15 分钟", "1h": "1 小时"}


def _render_params() -> dict:
    params = get_meme_rsi_params()
    with st.form("mrb_params", border=False):
        p0, p1, p2, p3 = st.columns([1, 1, 1, 1], vertical_alignment="bottom")
        tf = p0.selectbox("K 线周期", list(TF_LABELS), format_func=TF_LABELS.get,
                          index=0 if params.get("timeframe", "15m") == "15m" else 1)
        low = p1.number_input("超卖 RSI <", 0.0, 100.0, float(params["rsi_low"]), 1.0)
        high = p2.number_input("超买 RSI >", 0.0, 100.0, float(params["rsi_high"]), 1.0)
        if p3.form_submit_button("💾 保存参数", width="stretch"):
            if update_meme_rsi_params({"timeframe": tf, "rsi_low": float(low), "rsi_high": float(high)}):
                st.toast("参数已保存，后台下一轮生效", icon="✅")
            else:
                st.error("❌ 保存失败（数据库不可用）")
    return {"timeframe": tf, "rsi_low": float(low), "rsi_high": float(high)}


def _render_import() -> None:
    with st.expander("➕ 导入池子地址"):
        text = st.text_area("Solana(Meteora) 池子地址，每行 / 逗号 / 空格分隔", height=110,
                            key="mrb_input", label_visibility="collapsed",
                            placeholder="粘贴 Solana(Meteora) 池子地址，每行 / 逗号 / 空格分隔")
        if st.button("解析并添加", type="primary"):
            valid, invalid = mrb.parse_pool_addresses(text)
            if valid:
                with st.spinner(f"读取 {len(valid)} 个池子名 ..."):
                    named = [(addr, mrb.fetch_pool_name(addr)) for addr in valid]
                st.toast(f"已添加/更新 {add_meme_pools(named)} 个池子", icon="✅")
            else:
                st.error("未解析到合法池子地址")
            if invalid:
                st.warning(f"{len(invalid)} 个片段无效：" + ", ".join(invalid[:10])
                           + (" ..." if len(invalid) > 10 else ""))


def _render_table(params: dict) -> None:
    pools = list_meme_watchlist_pools()
    if not pools:
        st.info("监控列表为空，展开上方「导入池子地址」添加。")
        return

    df = pd.DataFrame(pools)
    head_l, head_r = st.columns([4, 1], vertical_alignment="bottom")
    head_l.subheader(f"监控列表 {int(df['enabled'].sum())}/{len(df)} 启用")
    scan = head_r.button("🔍 立即扫描", type="primary", width="stretch",
                         help="仅在页面展示命中结果，不发飞书")

    view = pd.DataFrame({
        "address": df["address"],
        "启用": df["enabled"].astype(bool),
        "池子": df["symbol"].fillna(""),
        "RSI": df["last_rsi"],
        "现价": df["last_price"],
        "上次检查": df["last_checked_at"],
        "链接": df["address"].apply(lambda a: f"https://app.meteora.ag/dlmm/{a}"),
        "删除": False,
    })
    edited = st.data_editor(
        view, key="mrb_editor", hide_index=True, width="stretch",
        disabled=["池子", "RSI", "现价", "上次检查", "链接"],
        column_config={
            "address": None,
            "启用": st.column_config.CheckboxColumn(width="small"),
            "RSI": st.column_config.NumberColumn(format="%.1f", width="small"),
            "现价": st.column_config.NumberColumn("现价 (USD)", format="$%.8g"),
            "链接": st.column_config.LinkColumn(display_text="🌊 进池", width="small"),
            "删除": st.column_config.CheckboxColumn(width="small"),
        },
    )

    orig, new = view.set_index("address"), edited.set_index("address")
    changes = []
    for addr in new.index:
        if new.loc[addr, "删除"]:
            changes.append(("delete", addr))
        elif bool(orig.loc[addr, "启用"]) != bool(new.loc[addr, "启用"]):
            changes.append(("enabled", addr))
    if changes:
        n_del = sum(1 for c in changes if c[0] == "delete")
        s_l, s_r = st.columns([4, 1], vertical_alignment="center")
        s_l.info(f"{len(changes)} 项未保存" + (f"，含删除 {n_del} 个池子" if n_del else ""))
        if s_r.button("💾 保存修改", type="primary", width="stretch"):
            for kind, addr in changes:
                if kind == "delete":
                    remove_meme_pool(addr)
                else:
                    set_meme_pool_enabled(addr, bool(new.loc[addr, "启用"]))
            st.session_state.pop("mrb_editor", None)
            st.toast("已保存", icon="💾")
            st.rerun()

    if scan:
        progress = st.progress(0, text="准备扫描 ...")

        def _report(done, total, symbol):
            progress.progress(min(done / max(total, 1), 1.0), text=f"RSI {done}/{total}：{symbol}")

        hits = mrb.scan_watchlist(pools, rsi_low=params["rsi_low"], rsi_high=params["rsi_high"],
                                  period=mrb.RSI_PERIOD, timeframe=params["timeframe"],
                                  on_progress=_report)
        progress.empty()
        if hits:
            st.warning(f"命中 {len(hits)} 个越界池子（仅展示，未发飞书）")
            st.dataframe(pd.DataFrame(hits), width="stretch", hide_index=True)
        else:
            st.success("本轮无越界池子")


def render_page(embedded: bool = False) -> None:
    if not embedded:
        st.title("📡 批量 Meme 超卖超买监控")
    st.caption(f"Meteora 池子 RSI({mrb.RSI_PERIOD}) 越界时合并推送飞书。")
    render_monitor_controls("meme_rsi_batch", "批量 Meme 超卖超买")
    params = _render_params()
    _render_import()
    _render_table(params)


if __name__ == "__main__":
    st.set_page_config(page_title="批量 Meme 超卖超买监控", page_icon="📡", layout="wide")
    render_page()

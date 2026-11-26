"""流动性池价格模拟器 (st_pool_simulator)

功能:
  - 输入 Token 地址 + 所属公链，通过 Dexscreener 拉取实时池子数据
    (现价 / 成交量24h / 流动性 / 市值 等)
  - 预设下跌 / 上涨幅度(%), 结合用户持仓数量 + 成本价，
    自动计算对应目标价、持仓市值、预估盈亏金额与盈亏比例
数据源: Dexscreener API

用法(独立脚本): python pages/st_pool_simulator.py --address <ADDR> --chain <sol>
"""
import os
import sys

# 支持独立运行 (python pages/st_pool_simulator.py): 仓库根加入 sys.path
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.infrastructure.market_data.dexscreener import fetch_token_info
from app.interfaces.asset_config import CHAIN_LABELS, TOKEN_CHAINS

# Windows 控制台默认 cp1252 无法打印中文/emoji，强制 UTF-8 输出
if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        _rer = getattr(_stream, "reconfigure", None)
        if callable(_rer):
            _rer(encoding="utf-8", errors="replace")


def normalize_chain(chain: str) -> str:
    c = (chain or "sol").strip().lower()
    aliases = {"sol": "sol", "bsc": "bsc", "base": "base", "eth": "eth",
               "robinhood": "robinhood", "arc": "arc", "stable": "stable"}
    return aliases.get(c, c)


def fetch_pool(chain: str, address: str):
    """拉取池子数据 (Dexscreener, 无需 API Key), 返回解析后的字典或 {"error": ...}"""
    data, source = fetch_token_info(chain, address)
    if data is None:
        return {"error": source}

    try:
        price = float((data.get("price") or {}).get("price"))
    except (TypeError, ValueError):
        price = None
    try:
        vol24 = float((data.get("price") or {}).get("volume_24h") or 0)
    except (TypeError, ValueError):
        vol24 = 0.0
    try:
        trade_fee = float(data.get("trade_fee") or 0)
    except (TypeError, ValueError):
        trade_fee = 0.0
    try:
        liq = float(data.get("liquidity") or 0)
    except (TypeError, ValueError):
        liq = 0.0
    try:
        holders = int(data.get("holder_count") or 0)
    except (TypeError, ValueError):
        holders = 0

    pool = data.get("pool") or {}
    quote = pool.get("quote_symbol") or ""
    exchange = pool.get("exchange") or ""
    ath = data.get("ath_price")
    try:
        ath = float(ath)
    except (TypeError, ValueError):
        ath = None
    # 费用比率: GMGN 官方字段为 pool.fee_ratio (e.g. 0.1 = 0.1%)
    try:
        fee_ratio = float(pool.get("fee_ratio") or 0)
    except (TypeError, ValueError):
        fee_ratio = 0.0

    return {
        "symbol": data.get("symbol") or "",
        "name": data.get("name") or "",
        "price": price,
        "volume_24h": vol24,
        "trade_fee": trade_fee,
        "fee_ratio": fee_ratio,
        "liquidity": liq,
        "holders": holders,
        "quote": quote,
        "exchange": exchange,
        "ath": ath,
        "total_supply": data.get("total_supply"),
    }


# 默认预设下跌档位(%)
DEFAULT_DROP_STEPS = [-10, -20, -30, -50, -70]


def simulate(position_qty: float, cost_price: float, current_price: float, drop_pct: float):
    """按预设涨跌幅(%)计算目标价与盈亏 (drop_pct 负数为下跌, 正数为上涨)"""
    factor = 1 + drop_pct / 100.0
    target_price = current_price * factor
    total_cost = position_qty * cost_price
    total_value = position_qty * current_price
    target_value = position_qty * target_price
    pnl = target_value - total_cost
    pnl_pct = (pnl / total_cost * 100.0) if total_cost else 0.0
    return {
        "drop_pct": drop_pct,
        "target_price": target_price,
        "total_cost": total_cost,
        "total_value": total_value,
        "target_value": target_value,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
    }


# ============================================================
# CLI 独立运行入口（可选，便于脚本调试）
# ============================================================
def main_cli(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    addr = ""
    chain = "sol"
    if "--address" in argv:
        addr = argv[argv.index("--address") + 1]
    if "--chain" in argv:
        chain = argv[argv.index("--chain") + 1]
    if not addr:
        print("用法: python st_pool_simulator.py --address <ADDR> --chain <sol>")
        return
    d = fetch_pool(chain, addr)
    if "error" in d:
        print("❌", d["error"])
        return
    print(f"池子: {d['symbol']} ({d['name']})  @ {d['exchange']} / {d['quote']}")
    print(f"现价: {d['price']}  成交量24h: {d['volume_24h']:.2f}  费用比率: {d['fee_ratio']}%  流动性: {d['liquidity']:.2f}")


# ============================================================
# Streamlit 界面
# ============================================================
def run_streamlit():
    import streamlit as st
    import pandas as pd

    st.set_page_config(page_title="池子价格模拟器", page_icon="🧪", layout="wide")
    st.title("🧪 池子价格模拟器")
    st.caption("拉取池子实时价格，按预设涨跌幅档位估算持仓盈亏（不含手续费 / 滑点）。")

    with st.form("pool_sim_form", clear_on_submit=False, border=False):
        c1, c2, c3, c4, c5 = st.columns([3, 1.3, 1, 1.2, 0.9], vertical_alignment="bottom")
        address = c1.text_input("Token 合约地址", placeholder="例如: EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
        chain = c2.selectbox("公链", options=TOKEN_CHAINS, format_func=lambda x: CHAIN_LABELS[x])
        qty = c3.number_input("持仓数量", min_value=0.0, value=0.0, step=1.0, format="%.6f")
        cost = c4.number_input("成本价", min_value=0.0, value=0.0, step=0.000001, format="%.8f")
        submitted = c5.form_submit_button("🚀 模拟", type="primary", width="stretch")
        steps_input = st.text_input(
            "涨跌幅档位 %（逗号分隔，负数 = 下跌）",
            value=",".join(str(x) for x in DEFAULT_DROP_STEPS),
        )

    if submitted:
        if not address.strip():
            st.warning("⚠️ 请输入 Token 合约地址")
            st.stop()
        with st.spinner("正在通过 Dexscreener 拉取池子数据 ..."):
            data = fetch_pool(chain, address)
        if "error" in data:
            st.error(f"❌ 拉取失败: {data['error']}")
            st.stop()
        st.session_state["sim_data"] = data
        st.session_state["sim_addr"] = address
        st.session_state["sim_chain"] = chain
    elif "sim_data" not in st.session_state:
        st.caption("填写参数后点「模拟」。")
        st.stop()

    data = st.session_state["sim_data"]
    if data["price"] is None:
        st.error("❌ 未取到该池子的当前价格，无法模拟。请检查地址与公链是否匹配。")
        st.stop()

    fee_display = f"{data['fee_ratio']}%" if data["fee_ratio"] else "未披露"
    line = (f"**{data['symbol'] or address[:10]}** · 现价 **{data['price']:.8g}** {data['quote'] or ''} · "
            f"24h 成交 {data['volume_24h']:,.0f} · 流动性 {data['liquidity']:,.0f} · 费率 {fee_display}")
    if data["holders"]:
        line += f" · 持有人 {data['holders']:,}"
    if data["ath"] is not None and data["price"]:
        line += f" · 距 ATH {(data['price'] / data['ath'] - 1) * 100:.1f}%"
    st.markdown(line)

    # ---- 校验持仓/成本 ----
    if qty <= 0 or cost <= 0:
        st.warning("⚠️ 请输入 >0 的持仓数量与持仓成本价，才能计算盈亏。")
        st.stop()

    # ---- 解析涨跌幅档位 ----
    try:
        steps = [float(x) for x in steps_input.replace("，", ",").split(",") if x.strip()]
    except ValueError:
        st.error("❌ 涨跌幅档位格式错误，请用数字与逗号，如 -10,-20,-30")
        st.stop()
    if not steps:
        st.error("❌ 请至少填一档涨跌幅")
        st.stop()

    # ---- 逐档模拟 ----
    rows = []
    for drop in sorted(set(steps), key=lambda x: x):
        r = simulate(qty, cost, data["price"], drop)
        rows.append({
            "涨跌幅": f"{r['drop_pct']:+.1f}%",
            "目标价": r["target_price"],
            "持仓市值": r["target_value"],
            "初始成本": r["total_cost"],
            "盈亏金额": r["pnl"],
            "盈亏比例": f"{r['pnl_pct']:+.2f}%",
        })

    df = pd.DataFrame(rows)
    st.subheader(f"💰 预估盈亏（持仓 {qty:g} @ 成本 {cost:.8g}，当前市值 {qty*data['price']:.4g}）")

    def color_pnl(val_str):
        try:
            v = float(val_str.replace("%", "").replace("+", ""))
        except (ValueError, AttributeError):
            return ""
        return "background-color: #1f3d2b; color: #7dffa8" if v >= 0 else "background-color: #3d1f22; color: #ff7d7d"

    styled = df.style.map(color_pnl, subset=["盈亏比例","盈亏金额"])
    st.dataframe(
        styled,
        column_config={
            "涨跌幅": st.column_config.TextColumn("涨跌幅", width="small"),
            "目标价": st.column_config.NumberColumn("目标价", format="%.8g"),
            "持仓市值": st.column_config.NumberColumn("持仓市值", format="%.4g"),
            "初始成本": st.column_config.NumberColumn("初始成本", format="%.4g"),
            "盈亏金额": st.column_config.NumberColumn("盈亏金额", format="%.4g"),
            "盈亏比例": st.column_config.TextColumn("盈亏比例", width="small"),
        },
        width="stretch",
        hide_index=True,
    )



# ============================================================
if __name__ == "__main__":
    # 仅在直接运行时支持 CLI；作为 st.Page 由 index.py 的 run_streamlit 场景触发
    if any(a in sys.argv for a in ("--address", "--help", "-h")):
        main_cli()
    else:
        run_streamlit()
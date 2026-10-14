import os, json, time, re
from datetime import datetime, timezone
from pathlib import Path
import requests
import pandas as pd
import streamlit as st

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv 可选，缺了就只用进程环境变量
    def load_dotenv(*args, **kwargs):
        return False

APP_DIR = Path(__file__).resolve().parent
# 进程环境变量优先，其次读取本目录的 .env；不会覆盖已有的真实环境变量。
load_dotenv(APP_DIR / ".env")

BASE = "https://api.geckoterminal.com/api/v2"
STATE_FILE = Path("monitor_state.json")
HEADERS = {"accept": "application/json;version=20230302", "User-Agent": "TokenReversalMonitor/0.1"}
NETWORKS = {"Solana": "solana", "Robinhood Chain": "robinhood"}

st.set_page_config(page_title="代币反转监控", layout="wide")
st.title("代币反转监控")
st.caption("输入合约地址 → 获取链上 DEX K 线 → 检查停止创新低与收盘价突破局部高点 → 飞书报警。不会自动交易。")

st.subheader("监控设置")
c_refresh, c_stop, c_break, c_webhook = st.columns(4)
with c_refresh:
    refresh_seconds = st.number_input("自动刷新间隔（秒）", min_value=60, max_value=600, value=120, step=30,
                                      help="GeckoTerminal 公共 API 有请求频率限制，建议至少 60 秒；监控多个地址时进一步调大。")
with c_stop:
    stop_n = st.number_input("停止创新低观察根数", min_value=2, max_value=8, value=3)
with c_break:
    breakout_m = st.number_input("局部高点观察根数", min_value=3, max_value=12, value=6)
with c_webhook:
    webhook_env = os.getenv("FEISHU_WEBHOOK", "").strip()
    if webhook_env:
        webhook = webhook_env
        st.success("飞书 Webhook 已从 env / .env 加载")
        st.caption("无需在页面输入")
    else:
        webhook = st.text_input("飞书机器人 Webhook", type="password",
                                help="未检测到 FEISHU_WEBHOOK，此处手动输入；建议改配到 .env 或环境变量。")
        st.caption("建议配置在 .env 或环境变量 FEISHU_WEBHOOK 中。Webhook 属于密钥，不要公开或提交到 Git。")
st.divider()


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"items": []}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def get_json(url, params=None):
    r = requests.get(url, params=params, headers=HEADERS, timeout=15)
    if r.status_code == 429:
        raise RuntimeError("行情服务请求频率受限（HTTP 429），请降低监控数量或延长刷新间隔。")
    r.raise_for_status()
    return r.json()


def resolve_pool(network, token_address):
    data = get_json(f"{BASE}/networks/{network}/tokens/{token_address}/pools", {"page": 1})
    pools = data.get("data", [])
    if not pools:
        raise RuntimeError("该链/地址没有查到 GeckoTerminal 支持的交易池，或该网络暂不受支持。")
    # Prefer highest reserve in USD; missing values rank last.
    def liquidity(pool):
        try:
            return float(pool.get("attributes", {}).get("reserve_in_usd") or 0)
        except (TypeError, ValueError):
            return 0
    pools.sort(key=liquidity, reverse=True)
    pool = pools[0]
    return pool["attributes"].get("address") or pool["id"].split("_")[-1], pool["attributes"]


def get_ohlcv(network, pool_address, limit=40):
    # GeckoTerminal supports minute candles with aggregate=5 for 5-minute bars.
    data = get_json(f"{BASE}/networks/{network}/pools/{pool_address}/ohlcv/minute", {
        "aggregate": 5, "limit": limit, "currency": "usd", "token": "base"
    })
    rows = data.get("data", {}).get("attributes", {}).get("ohlcv_list", [])
    if not rows:
        raise RuntimeError("行情接口未返回 K 线数据。")
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["high", "low", "close"])
    # Exclude the newest candle because it may still be forming.
    if len(df) > 1:
        df = df.iloc[:-1].reset_index(drop=True)
    return df


def detect_signal(df, stop_n=3, breakout_m=6):
    if len(df) < max(stop_n + 1, breakout_m + 1):
        return {"status": "K线不足", "stopped": False, "breakout": False, "recent_low": None, "local_high": None}
    # Stopped making lower lows: the latest stop_n lows do not undercut the preceding stop_n-window minimum.
    recent = df.tail(stop_n)
    prior = df.iloc[-(stop_n * 2):-stop_n]
    stopped = float(recent["low"].min()) >= float(prior["low"].min())
    # Compare latest closed candle close against the high of the preceding breakout_m candles, excluding itself.
    latest = df.iloc[-1]
    previous = df.iloc[-(breakout_m + 1):-1]
    local_high = float(previous["high"].max())
    breakout = float(latest["close"]) > local_high
    if breakout:
        status = "反转确认：收盘价突破局部高点" if stopped else "突破局部高点（未满足止跌条件）"
    elif stopped:
        status = "疑似止跌，等待突破"
    else:
        status = "未触发"
    return {"status": status, "stopped": stopped, "breakout": breakout,
            "recent_low": float(recent["low"].min()), "local_high": local_high,
            "close": float(latest["close"]), "timestamp": int(latest["timestamp"])}


def fmt_price(value):
    """Format a price for the table; None/NaN show as a dash."""
    if value is None:
        return "—"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)
    if pd.isna(value):
        return "—"
    if value == 0:
        return "0"
    if abs(value) >= 1:
        return f"{value:,.4f}"
    return f"{value:.10f}".rstrip("0").rstrip(".")


def send_feishu(webhook, title, message):
    if not webhook:
        return False, "未配置飞书 Webhook"
    payload = {"msg_type": "text", "content": {"text": f"{title}\n{message}"}}
    r = requests.post(webhook, json=payload, timeout=10)
    r.raise_for_status()
    result = r.json()
    if result.get("code", 0) not in (0, None) or result.get("StatusCode", 0) not in (0, None):
        raise RuntimeError(f"飞书返回异常：{result}")
    return True, "已发送"

state = load_state()

with st.expander("添加监控代币", expanded=True):
    c1, c2, c3 = st.columns([1, 3, 1])
    with c1:
        chain_label = st.selectbox("区块链", list(NETWORKS.keys()))
    with c2:
        address = st.text_input("代币合约地址", placeholder="Solana 地址或 Robinhood Chain 的 0x 地址")
    with c3:
        label = st.text_input("备注（可选）", placeholder="例如 PONS")
    if st.button("添加到监控表", type="primary"):
        address = address.strip()
        if not address:
            st.error("请先输入代币合约地址。")
        elif any(x["network"] == NETWORKS[chain_label] and x["address"].lower() == address.lower() for x in state["items"]):
            st.warning("这个代币已经在监控列表中。")
        else:
            state["items"].append({"id": f"{NETWORKS[chain_label]}:{address}", "chain": chain_label,
                                    "network": NETWORKS[chain_label], "address": address,
                                    "label": label.strip(), "enabled": False, "last_alert": 0,
                                    "last_signal": "", "pool": "", "error": ""})
            save_state(state)
            st.success("已添加，请在表格中点击“开始监控”。")
            st.rerun()

st.subheader("监控列表")
if not state["items"]:
    st.info("尚无代币。请在上方输入合约地址并添加。")
else:
    # Controls per item keep start/stop explicit and avoid claiming a background daemon is running.
    for item in state["items"]:
        with st.container(border=True):
            a, b, c, d, e = st.columns([1.2, 2.8, 1.2, 1.2, 1.2])
            a.markdown(f"**{item['chain']}**")
            b.markdown(f"{item['label'] or item['address'][:12] + '…'}\n\n`{item['address']}`")
            c.markdown("🟢 监控中" if item["enabled"] else "⚪ 已停止")
            if d.button("停止监控" if item["enabled"] else "开始监控", key=f"toggle_{item['id']}"):
                item["enabled"] = not item["enabled"]
                item["error"] = ""
                save_state(state)
                st.rerun()
            if e.button("删除", key=f"delete_{item['id']}"):
                state["items"] = [x for x in state["items"] if x["id"] != item["id"]]
                save_state(state)
                st.rerun()

    if st.button("立即检查所有运行中的代币", type="primary"):
        now = time.time()
        checked = 0
        for item in state["items"]:
            if not item["enabled"]:
                continue
            try:
                pool_address, pool_attrs = resolve_pool(item["network"], item["address"])
                item["pool"] = pool_address
                df = get_ohlcv(item["network"], pool_address)
                sig = detect_signal(df, int(stop_n), int(breakout_m))
                item["status"] = sig["status"]
                item["price"] = sig.get("close")
                item["recent_low"] = sig.get("recent_low")
                item["local_high"] = sig.get("local_high")
                item["checked_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                item["error"] = ""
                checked += 1
                is_signal = sig.get("stopped") and sig.get("breakout")
                signal_key = f"{sig.get('timestamp')}:{sig.get('status')}"
                # Alert once per closed candle/signal; avoid repeated messages on each refresh.
                if is_signal and item.get("last_signal") != signal_key:
                    message = (f"链：{item['chain']}\n代币：{item['label'] or item['address']}\n"
                               f"合约：{item['address']}\n池地址：{pool_address}\n周期：5分钟\n"
                               f"信号：停止创新低 + 收盘价突破局部高点\n收盘价：{sig.get('close')}\n"
                               f"局部高点：{sig.get('local_high')}\n时间戳：{sig.get('timestamp')}\n"
                               f"说明：仅为技术信号，不代表后续必然上涨。")
                    try:
                        send_feishu(webhook, "🔔 代币反转信号", message)
                        item["last_signal"] = signal_key
                        item["last_alert"] = now
                    except Exception as exc:
                        item["error"] = f"信号已识别，但飞书发送失败：{exc}"
            except Exception as exc:
                item["error"] = str(exc)
                item["status"] = "数据获取失败/网络暂不支持"
        save_state(state)
        st.success(f"本轮检查完成：{checked} 个监控项获取到 K 线数据。")
        st.rerun()

    rows = []
    for item in state["items"]:
        rows.append({
            "链": item["chain"], "代币/备注": item["label"] or "—", "合约地址": item["address"],
            "监控状态": "运行中" if item["enabled"] else "已停止", "信号状态": item.get("status", "尚未检查"),
            "最近收盘价": fmt_price(item.get("price")),
            "停止创新低观察价": fmt_price(item.get("recent_low")),
            "局部高点观察价": fmt_price(item.get("local_high")),
            "最近检查时间": item.get("checked_at", "—"),
            "错误/提示": item.get("error", "")
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

st.warning("注意：Streamlit 页面需要保持打开并定期点击“立即检查”，本版本不是后台常驻服务。Robinhood Chain 的 GeckoTerminal 网络支持需以实际 API 返回为准；若无数据会显示错误，不会猜测信号。")
st.markdown("**行情接口文档：** [GeckoTerminal API](https://api.geckoterminal.com/docs/index.html) · [DEX Screener API](https://docs.dexscreener.com/api/reference)")

"""价格监控后台脚本：按指定的 token 地址 + 公链定时拉取实时价格，
到达设定目标价时通过飞书 Webhook 告警。

数据库: Turso (libSQL) 中的 price_alert 表
数据源: gmgn-cli (GMGN OpenAPI)，按 token 地址 + 公链查询实时价格
告警  : 飞书 Webhook (send_feishu_msg)

用法:
  python monitor_price.py            # 单轮检查后退出 (适合 GitHub Actions cron)
  python monitor_price.py --loop     # 本地常驻轮询 (间隔 60 秒)
  python monitor_price.py --loop --interval 300   # 自定义轮询间隔(秒)
"""
import os
import sys
import time
import traceback

from db import get_db_client
from config import FEISHU_WEBHOOK
from send_feishu_msg import send_feishu_msg
from gmgn_cli import fetch_token_info

# Windows 控制台默认 cp1252 无法打印 emoji，强制 UTF-8 输出
if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        _rer = getattr(_stream, "reconfigure", None)
        if callable(_rer):
            _rer(encoding="utf-8", errors="replace")

# 数据拉取逻辑 (CLI 优先 / 直连 OpenAPI 兜底 / 代理与 PATH) 全部在 gmgn_cli.fetch_token_info 中统一处理
CHAIN_ALIASES = {
    "sol": "SOL", "bsc": "BSC", "base": "BASE", "eth": "ETH",
    "robinhood": "ROBINHOOD", "arc": "ARC", "stable": "STABLE",
}

DEFAULT_INTERVAL = 60  # --loop 模式默认轮询间隔(秒)


def normalize_chain(chain: str) -> str:
    chain = (chain or "sol").strip().lower()
    return CHAIN_ALIASES.get(chain, chain.upper())


# ============================================================
# 建表（幂等）
# ============================================================
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS price_alert (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    address       TEXT NOT NULL,
    chain         TEXT NOT NULL DEFAULT 'sol',
    symbol        TEXT,
    target_price  REAL NOT NULL,
    direction     TEXT NOT NULL DEFAULT 'gte',   -- gte: 价格>=目标; lte: 价格<=目标
    enabled       INTEGER NOT NULL DEFAULT 1,
    last_price    REAL,
    last_checked_at TEXT,
    alerted       INTEGER NOT NULL DEFAULT 0,    -- 1=该规则已触发过告警(避免重复轰炸)
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_price_alert_enabled ON price_alert(enabled);
"""


def ensure_table():
    client = get_db_client()
    if not client:
        print("❌ 无法连接数据库，跳过建表")
        return False
    try:
        # libsql-client 允许多条语句批量执行
        client.batch([CREATE_TABLE_SQL])
        print("✅ price_alert 表已就绪")
        return True
    except Exception as e:
        print(f"❌ 建表失败: {e}")
        return False
    finally:
        client.close()


# ============================================================
# 查询价格（gmgn-cli 优先, 直连 OpenAPI 兜底）
# ============================================================
def fetch_price(chain: str, address: str):
    """拉取当前价格, 返回 (price, symbol) 或 (None, None).

    gmgn-cli 缺失/失败时自动降级为直连 GMGN OpenAPI, 无需依赖 node 环境。
    """
    data, source = fetch_token_info(chain, address)
    if data is None:
        print(f"❌ 拉取失败 ({source})")
        return None, None

    symbol = data.get("symbol") or ""
    price = None
    pobj = data.get("price") or {}
    raw = pobj.get("price")
    if raw is None:
        raw = data.get("price_usd")  # 兼容性兜底
    try:
        price = float(raw)
    except (TypeError, ValueError):
        print(f"❌ 无法读取价格字段: {raw}")
        return None, symbol
    return price, symbol


# ============================================================
# 规则读取
# ============================================================
def load_enabled_rules():
    """读取所有已启用的 price_alert 规则"""
    client = get_db_client()
    if not client:
        return []
    rules = []
    try:
        res = client.execute(
            "SELECT id, address, chain, symbol, target_price, direction, alerted, created_at"
            " FROM price_alert WHERE enabled = 1 ORDER BY id"
        )
        for row in res.rows:
            rules.append({
                "id": row[0],
                "address": row[1],
                "chain": row[2],
                "symbol": row[3],
                "target_price": float(row[4]),
                "direction": row[5],
                "alerted": bool(row[6]),
            })
        return rules
    except Exception as e:
        print(f"❌ 读取规则失败: {e}")
        return []
    finally:
        client.close()


def update_rule_price(rule_id: int, price: float):
    """回写最新价格与检查时间"""
    client = get_db_client()
    if not client:
        return
    try:
        client.execute(
            "UPDATE price_alert SET last_price = ?, last_checked_at = datetime('now') WHERE id = ?",
            [price, rule_id])
    except Exception as e:
        print(f"❌ 回写价格失败 (id={rule_id}): {e}")
    finally:
        client.close()


def mark_alerted(rule_id: int):
    """标记规则已触发（避免同一条目标价重复告警）"""
    client = get_db_client()
    if not client:
        return
    try:
        client.execute(
            "UPDATE price_alert SET alerted = 1 WHERE id = ?", [rule_id])
    except Exception as e:
        print(f"❌ 标记告警状态失败 (id={rule_id}): {e}")
    finally:
        client.close()


# ============================================================
# 单轮监控
# ============================================================
def run_once():
    rules = load_enabled_rules()
    if not rules:
        print("ℹ️ 没有启用的价格监控规则")
        return

    print(f"🔍 本轮检查 {len(rules)} 条价格规则 ...")
    ok, fail = 0, 0
    for rule in rules:
        chain = normalize_chain(rule["chain"])
        addr = rule["address"]
        # 跳过 0 地址(如 GMGN 分页占位)
        if len(addr) < 20:
            print(f"⚠️ 跳过疑似无效地址: {addr}")
            continue

        price, symbol = fetch_price(chain, addr)
        if price is None:
            fail += 1
            continue
        ok += 1
        sym = rule["symbol"] or symbol or addr[:8] + "..."

        print(f"✅ [{sym}] {chain} 现价: {price}")
        update_rule_price(rule["id"], price)

        target = rule["target_price"]
        triggered = False
        if rule["direction"] == "gte" and price >= target:
            triggered = True
        elif rule["direction"] == "lte" and price <= target:
            triggered = True

        if triggered:
            if rule["alerted"]:
                print(f"⏹️ [{sym}] 已达目标价，已告警过，跳过重复推送")
                continue
            direction_cn = "上涨达到" if rule["direction"] == "gte" else "下跌达到"
            msg = (
                f"🚨 【价格报警】{sym} ({chain})\n"
                f"当前价格: {price}\n"
                f"目标价({direction_cn}): {target}\n"
                f"地址: {addr}\n"
                f"监控地址: https://gmgn.ai/{chain.lower()}/token/{addr}"
            )
            send_feishu_msg(FEISHU_WEBHOOK, msg)
            mark_alerted(rule["id"])
            print(f"🚨 已推送告警: [{sym}] {price} {'>=' if rule['direction']=='gte' else '<='} {target}")

    print(f"📊 本轮完成: 成功 {ok}，失败 {fail}")


# ============================================================
# 常驻循环模式 (本地进程)
# ============================================================
def run_loop(interval: int):
    print(f"🔁 常驻监控模式: 每 {interval} 秒检查一次 (Ctrl+C 退出)")
    while True:
        try:
            run_once()
        except KeyboardInterrupt:
            print("\n👋 已退出常驻监控")
            break
        except Exception:
            traceback.print_exc()
        time.sleep(interval)


def main():
    ensure_table()

    args = sys.argv[1:]
    if "--loop" in args:
        interval = DEFAULT_INTERVAL
        if "--interval" in args:
            idx = args.index("--interval")
            if len(args) > idx + 1:
                interval = int(args[idx + 1])
        run_loop(interval)
    else:
        run_once()


if __name__ == "__main__":
    main()
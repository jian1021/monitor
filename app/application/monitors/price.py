"""价格监控后台脚本：按指定的 token 地址 + 公链定时拉取实时价格，
到达设定目标价时通过飞书 Webhook 告警。

数据库: Turso (libSQL) 中的 price_alert 表
数据源: Dexscreener API，按 token 地址 + 公链查询实时价格
告警  : 飞书 Webhook (send_feishu_msg)

用法:
  python -m scripts.monitor_price            # 单轮检查后退出 (适合定时任务或手动执行)
  python -m scripts.monitor_price --loop     # 本地常驻轮询 (间隔 60 秒)
  python -m scripts.monitor_price --loop --interval 300   # 自定义轮询间隔(秒)
"""
import os
import sys
import time
import traceback
from datetime import datetime, time as dtime, timedelta, timezone

from app.infrastructure.db.client import get_db_client
from app.core.settings import FEISHU_WEBHOOK
from app.infrastructure.notifications.sender import send_feishu_msg
from app.infrastructure.db.module_webhooks import resolve_webhook
from app.infrastructure.market_data.dexscreener import fetch_token_info

# Windows 控制台默认 cp1252 无法打印 emoji，强制 UTF-8 输出
if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        _rer = getattr(_stream, "reconfigure", None)
        if callable(_rer):
            _rer(encoding="utf-8", errors="replace")

# 数据拉取逻辑 (Dexscreener, 无需 API Key) 统一在
# app.infrastructure.market_data.dexscreener.fetch_token_info 中处理
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
    address       TEXT NOT NULL,                 -- token: 合约地址 / ashare: 6 位代码
    chain         TEXT NOT NULL DEFAULT 'sol',   -- ashare 规则不使用
    asset_type    TEXT NOT NULL DEFAULT 'token', -- token=链上代币 / ashare=A股・场内ETF
    symbol        TEXT,
    target_price  REAL NOT NULL,
    direction     TEXT NOT NULL DEFAULT 'gte',   -- gte: 价格>=目标; lte: 价格<=目标
    enabled       INTEGER NOT NULL DEFAULT 1,
    last_price    REAL,
    last_checked_at TEXT,
    daily_check_at TEXT,                           -- NULL=跟随调度轮询; 'HH:MM'=每天北京时间该时刻后检查一次
    alerted       INTEGER NOT NULL DEFAULT 0,    -- 1=该规则已触发过告警(避免重复轰炸)
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# 老库补列：CREATE TABLE IF NOT EXISTS 不会改动已存在的表，必须单独 ALTER
_MIGRATIONS = (
    "ALTER TABLE price_alert ADD COLUMN asset_type TEXT NOT NULL DEFAULT 'token'",
    "ALTER TABLE price_alert ADD COLUMN daily_check_at TEXT",
)
CREATE_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_price_alert_enabled ON price_alert(enabled);"

# A 股 / 场内 ETF 新规则的默认检查频率：每天开盘 09:30（北京时间）检查一次
ASHARE_DEFAULT_DAILY_CHECK = "09:30"
BEIJING_TZ = timezone(timedelta(hours=8))


def ensure_table():
    client = get_db_client()
    if not client:
        print("❌ 无法连接数据库，跳过建表")
        return False
    try:
        # pipeline API 的每个 batch 元素必须是单条语句
        client.batch([CREATE_TABLE_SQL, CREATE_INDEX_SQL])
        for ddl in _MIGRATIONS:
            try:
                client.execute(ddl)
                print(f"✅ price_alert 结构升级: {ddl.split('ADD COLUMN')[-1].strip()[:40]}")
            except Exception:
                pass  # 列已存在
        print("✅ price_alert 表已就绪")
        return True
    except Exception as e:
        print(f"❌ 建表失败: {e}")
        return False
    finally:
        client.close()


# ============================================================
# 查询价格 (Dexscreener)
# ============================================================
def fetch_price(chain: str, address: str, asset_type: str = "token"):
    """拉取当前价格, 返回 (price, symbol) 或 (None, None).

    asset_type='ashare' 走腾讯实时行情（A 股 / 场内 ETF）；
    其余走 Dexscreener（链上代币，无需 API Key）。
    """
    if asset_type == "ashare":
        from app.infrastructure.market_data.a_share import fetch_realtime_price

        return fetch_realtime_price(address)

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
            "SELECT id, address, chain, asset_type, symbol, target_price, direction, alerted,"
            " last_checked_at, daily_check_at, created_at FROM price_alert WHERE enabled = 1 ORDER BY id"
        )
        for row in res.rows:
            rules.append({
                "id": row[0],
                "address": row[1],
                "chain": row[2],
                "asset_type": row[3] or "token",
                "symbol": row[4],
                "target_price": float(row[5]),
                "direction": row[6],
                "alerted": bool(row[7]),
                "last_checked_at": row[8],
                "daily_check_at": row[9],
            })
        return rules
    except Exception as e:
        print(f"❌ 读取规则失败: {e}")
        return []
    finally:
        client.close()


def update_rule_price(rule_id: int, price: float, touch_checked: bool = True):
    """回写最新价格；touch_checked=True 时同时推进 last_checked_at。

    last_checked_at 是「每日定时规则今天是否已检查」的唯一依据，只能在
    调度检查完整处理完（含告警送达）后推进：
      - 页面手动查价必须传 False，否则当天 09:30 后手动查一次，定时检查就被跳过；
      - 告警推送失败时不推进，保证每日规则下一轮重试，而不是拖到明天。
    """
    client = get_db_client()
    if not client:
        return
    try:
        if touch_checked:
            client.execute(
                "UPDATE price_alert SET last_price = ?, last_checked_at = datetime('now') WHERE id = ?",
                [price, rule_id])
        else:
            client.execute("UPDATE price_alert SET last_price = ? WHERE id = ?", [price, rule_id])
    except Exception as e:
        print(f"❌ 回写价格失败 (id={rule_id}): {e}")
    finally:
        client.close()


def update_rule_symbol(rule_id: int, symbol: str):
    """回写代币符号（规则建立时留空则由每轮取数自动补全）"""
    client = get_db_client()
    if not client:
        return
    try:
        client.execute(
            "UPDATE price_alert SET symbol = ? WHERE id = ?", [symbol, rule_id])
    except Exception as e:
        print(f"❌ 回写代币符号失败 (id={rule_id}): {e}")
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


def clear_alerted(rule_id: int):
    """条件解除后清除告警标志，使下次条件满足时重新推送。"""
    client = get_db_client()
    if not client:
        return
    try:
        client.execute(
            "UPDATE price_alert SET alerted = 0 WHERE id = ?", [rule_id])
    except Exception as e:
        print(f"❌ 清除告警状态失败 (id={rule_id}): {e}")
    finally:
        client.close()


# ============================================================
# 规则维护（页面 CRUD 的唯一实现；页面不得再自己拼 SQL）
# ============================================================
RULE_COLUMNS = ("id", "address", "chain", "asset_type", "symbol", "target_price", "direction",
                "enabled", "last_price", "last_checked_at", "daily_check_at",
                "alerted", "created_at")


def load_all_rules() -> list[dict]:
    """读取全部规则（含停用），按 id 倒序，供管理页展示。"""
    client = get_db_client()
    if not client:
        return []
    try:
        rows = client.execute(
            "SELECT id, address, chain, asset_type, symbol, target_price, direction, enabled,"
            " last_price, last_checked_at, daily_check_at, alerted, created_at"
            " FROM price_alert ORDER BY id DESC"
        ).rows
        return [
            {
                "id": row[0], "address": row[1], "chain": row[2] or "",
                "asset_type": row[3] or "token", "symbol": row[4] or "",
                "target_price": row[5], "direction": row[6], "enabled": bool(row[7]),
                "last_price": row[8], "last_checked_at": row[9],
                "daily_check_at": row[10],
                "alerted": bool(row[11]), "created_at": row[12],
            }
            for row in rows
        ]
    except Exception as e:
        print(f"❌ 读取价格规则失败: {e}")
        raise
    finally:
        client.close()


def add_rule(address: str, chain: str, symbol: str, target_price: float,
             direction: str, asset_type: str = "token",
             daily_check_at: str | None = None) -> bool:
    """新增价格规则；参数由调用方校验后传入。

    asset_type='ashare' 时 address 存 6 位代码、chain 留空。
    daily_check_at: 'HH:MM'（北京时间，每天该时刻后检查一次）；None 跟随调度轮询。
    """
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute(
            "INSERT INTO price_alert (address, chain, asset_type, symbol, target_price,"
            " direction, daily_check_at, enabled) VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            [address.strip(), chain or "", asset_type,
             (symbol or "").strip(), target_price, direction,
             normalize_daily_check(daily_check_at)],
        )
        return True
    except Exception as e:
        print(f"❌ 新增价格规则失败: {e}")
        return False
    finally:
        client.close()


def normalize_daily_check(value: str | None) -> str | None:
    """规整每日检查时刻：'9:30' -> '09:30'；空值/非法值返回 None（跟随轮询）。"""
    text = (value or "").strip()
    if not text:
        return None
    try:
        hh, mm = text.split(":")
        return f"{int(hh):02d}:{int(mm):02d}"
    except (ValueError, AttributeError):
        return None


def update_rule_schedule(rule_id: int, daily_check_at: str | None) -> bool:
    """更新规则的每日检查时刻；None/空串表示恢复跟随调度轮询。"""
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute(
            "UPDATE price_alert SET daily_check_at = ? WHERE id = ?",
            [normalize_daily_check(daily_check_at), rule_id])
        return True
    except Exception as e:
        print(f"❌ 更新检查频率失败 (id={rule_id}): {e}")
        return False
    finally:
        client.close()


def set_rule_enabled(rule_id: int, enabled: bool) -> bool:
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute("UPDATE price_alert SET enabled = ? WHERE id = ?",
                       [1 if enabled else 0, rule_id])
        return True
    except Exception as e:
        print(f"❌ 更新价格规则状态失败 (id={rule_id}): {e}")
        return False
    finally:
        client.close()


def delete_rule(rule_id: int) -> bool:
    client = get_db_client()
    if not client:
        return False
    try:
        client.execute("DELETE FROM price_alert WHERE id = ?", [rule_id])
        return True
    except Exception as e:
        print(f"❌ 删除价格规则失败 (id={rule_id}): {e}")
        return False
    finally:
        client.close()


# ============================================================
# 单轮监控
# ============================================================
def _parse_utc(ts) -> datetime | None:
    """数据库 datetime('now') 存的是 UTC 文本，解析为 aware datetime。"""
    if not ts:
        return None
    try:
        return datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


def due_for_daily_check(rule: dict, now: datetime | None = None) -> bool:
    """带 daily_check_at 的规则今天是否到了检查时刻（北京时间）。

    - 无 daily_check_at：True（跟随调度轮询，不受限制）；
    - 当前北京时间未到该时刻：False；
    - 今天该时刻之后已检查过（last_checked_at 落在今天且晚于该时刻）：False；
    - 否则 True（今天该时刻后还没查过，立即检查）。
    """
    daily = normalize_daily_check(rule.get("daily_check_at"))
    if not daily:
        return True
    try:
        hh, mm = daily.split(":")
        target = dtime(int(hh), int(mm))
    except (ValueError, AttributeError):
        return True
    now = now or datetime.now(BEIJING_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=BEIJING_TZ)
    if now.time() < target:
        return False
    last = _parse_utc(rule.get("last_checked_at"))
    if last is not None:
        last_local = last.astimezone(BEIJING_TZ)
        if last_local.date() == now.date() and last_local.time() >= target:
            return False
    return True


# 告警解除迟滞：价格需回撤超过目标价的该比例才重新武装，
# 避免价格在目标价附近来回抖动时反复推送。
REARM_PCT = 0.5


def is_triggered(direction: str, price: float, target: float) -> bool:
    if direction == "gte":
        return price >= target
    if direction == "lte":
        return price <= target
    return False


def should_rearm(direction: str, price: float, target: float, pct: float = REARM_PCT) -> bool:
    """已告警规则是否离开目标价足够远（超过迟滞带）可重新武装。"""
    band = abs(target) * pct / 100.0
    if direction == "gte":
        return price < target - band
    if direction == "lte":
        return price > target + band
    return False


def ashare_market_open_day(now: datetime | None = None) -> bool:
    """A 股是否开市日：周末跳过；节假日走交易日历（失败则仅按周末判断）。"""
    now = now or datetime.now(BEIJING_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=BEIJING_TZ)
    local = now.astimezone(BEIJING_TZ)
    if local.weekday() >= 5:
        return False
    try:
        from app.infrastructure.market_data.trade_calendar import a_share_trading_day
        return a_share_trading_day(local.date())
    except Exception:  # noqa: BLE001
        return True


def run_once():
    rules = load_enabled_rules()
    if not rules:
        print("ℹ️ 没有启用的价格监控规则")
        return

    print(f"🔍 本轮检查 {len(rules)} 条价格规则 ...")
    ok, fail = 0, 0
    for rule in rules:
        asset_type = rule.get("asset_type") or "token"
        chain = normalize_chain(rule["chain"])
        addr = rule["address"]

        # 每日定时规则（如 A股/ETF 每天 09:30）：未到时刻或今天已查过则跳过
        if not due_for_daily_check(rule):
            continue

        if asset_type == "ashare" and not ashare_market_open_day():
            # 休市日行情是上一交易日收盘价，无新信息；不检查也不推进 last_checked_at
            continue

        if asset_type == "ashare":
            # A 股 / 场内 ETF：按代码走腾讯行情，没有合约地址可校验
            price, symbol = fetch_price("", addr, asset_type="ashare")
            source_label = addr
        else:
            # 跳过 0 地址(如 GMGN 分页占位)
            if len(addr) < 20:
                print(f"⚠️ 跳过疑似无效地址: {addr}")
                continue
            price, symbol = fetch_price(chain, addr)
            source_label = chain

        if price is None:
            fail += 1
            continue
        ok += 1
        if not rule["symbol"] and symbol:
            update_rule_symbol(rule["id"], symbol)
            print(f"🏷️ 已自动补全标的名称: {symbol}")
        sym = rule["symbol"] or symbol or addr[:8] + "..."

        print(f"✅ [{sym}] {source_label} 现价: {price}")
        # 先只写价格；last_checked_at 等本条规则处理完（告警已送达或无需告警）再推进
        update_rule_price(rule["id"], price, touch_checked=False)

        target = rule["target_price"]
        triggered = is_triggered(rule["direction"], price, target)

        if triggered:
            if rule["alerted"]:
                print(f"⏹️ [{sym}] 已达目标价，已告警过，跳过重复推送")
                update_rule_price(rule["id"], price)
                continue
            direction_cn = "上涨达到" if rule["direction"] == "gte" else "下跌达到"
            if asset_type == "ashare":
                msg = (
                    f"🚨 【价格报警】{sym} ({addr})\n"
                    f"当前价格: {price}\n"
                    f"目标价({direction_cn}): {target}\n"
                    f"标的: {addr}（A股/场内ETF）"
                )
            else:
                msg = (
                    f"🚨 【价格报警】{sym} ({chain})\n"
                    f"当前价格: {price}\n"
                    f"目标价({direction_cn}): {target}\n"
                    f"地址: {addr}\n"
                    f"监控地址: https://gmgn.ai/{chain.lower()}/token/{addr}"
                )
            if not send_feishu_msg(resolve_webhook("price"), msg):
                # 发送失败不标记已告警，否则这条规则以后不会再推（要等价格离开目标价才重置）
                print(f"❌ [{sym}] 飞书推送失败，保留未告警状态，下轮重试")
                continue
            mark_alerted(rule["id"])
            update_rule_price(rule["id"], price)
            print(f"🚨 已推送告警: [{sym}] {price} {'>=' if rule['direction']=='gte' else '<='} {target}")
        else:
            if rule["alerted"] and should_rearm(rule["direction"], price, target):
                clear_alerted(rule["id"])
                print(f"🔁 [{sym}] 价格已离开目标价超过 {REARM_PCT}%，告警标志重置，下次达标将重新推送")
            update_rule_price(rule["id"], price)

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
"""RSI 告警用例：闩锁策略、消息批次、发送回滚与各资产类别的扫描循环。

从 main.py 下沉而来：入口文件只负责装配与调度，业务规则集中在这里。
本层允许依赖 infrastructure（DB / 通知 / 行情适配器），但不得依赖 streamlit。
"""

import random
import time

from app.application.monitors import rsi as monitor_rsi
from app.core.settings import FEISHU_WEBHOOK
from app.infrastructure.db.assets import update_asset_alert_state
from app.infrastructure.db.module_webhooks import get_module_webhook
from app.infrastructure.notifications.sender import send_feishu_msg

# 逐只取数之间的随机间隔（秒），避免对数据源形成尖峰
SLEEP_A_SHARE = (0.8, 1.5)
SLEEP_METEORA = (1.0, 2.0)


# ================= 告警闩锁策略 =================
def rsi_zone(rsi, settings) -> str:
    """RSI 所处区间：below（超卖）/ above（超买）/ mid（中枢）/ unknown。"""
    if rsi is None:
        return "unknown"
    if rsi < settings["rsi_low"]:
        return "below"
    if rsi > settings["rsi_high"]:
        return "above"
    return "mid"


def should_push(item, rsi, price, settings) -> bool:
    """写入标的告警状态并返回本轮是否应推送；闩锁未解除时抑制重复告警。

    闩锁只在「同一侧仍然越界」时抑制。RSI(3) 这类极短周期指标可能从超卖
    直接跳到超买，若沿用上一侧的闩锁，另一侧的告警会被静默吞掉。
    """
    zone = rsi_zone(rsi, settings)
    if zone == "mid":
        update_asset_alert_state(item.get("id"), active=False, latched=False,
                                 rsi=float(rsi), price=float(price), alert=False)
        return False
    prev_zone = rsi_zone(item.get("last_rsi"), settings)
    already_latched = bool(item.get("alert_latched")) and prev_zone == zone
    update_asset_alert_state(
        item.get("id"),
        active=bool(item.get("alarm_active")) or not already_latched,
        latched=True, rsi=float(rsi), price=float(price), alert=not already_latched)
    return not already_latched


def should_push_rebound(item, rsi, price, settings) -> bool:
    """超卖区回升并上穿下限时报一次「反转突破」（离场/反弹信号）。

    判据是「上一轮 last_rsi 在超卖区、本轮回到中枢」，本身是边缘事件：
    同一段回升只会命中一次，不需要额外闩锁。但仍要清掉超卖闩锁，
    否则下一轮重新跌破时不会再次报警。
    """
    if not settings.get("alert_on_rebound"):
        return False
    prev = item.get("last_rsi")
    if prev is None:
        return False
    if rsi_zone(prev, settings) != "below" or rsi_zone(rsi, settings) != "mid":
        return False
    update_asset_alert_state(item.get("id"), active=False, latched=False,
                             rsi=float(rsi), price=float(price), alert=True)
    return True



def webhook_for(module_key: str) -> str:
    """模块专属飞书 Webhook；未配置时回落到全局 FEISHU_WEBHOOK。"""
    try:
        return get_module_webhook(module_key, FEISHU_WEBHOOK)
    except Exception as e:
        print(f"⚠️ 读取模块 Webhook 失败（{module_key}），使用全局: {e}")
        return FEISHU_WEBHOOK


class AlertBatch:
    """一轮监控的消息收集器：命中即记录闩锁，最后合并成一条消息发送。

    发送失败必须回滚本轮新写的闩锁，否则该标的要等 RSI 回到中枢区间才重新
    武装——等于永久丢失这次告警。
    """

    def __init__(self, webhook: str | None = None, empty_note: str = ""):
        self.webhook = webhook
        self.empty_note = empty_note
        self.messages: list[str] = []
        self.pending: list[tuple] = []

    def collect(self, item, rsi, price, message: str) -> None:
        """把已判定需要推送的事件加入批次（边缘事件自行负责去重）。"""
        self.pending.append((item, rsi, price))
        self.messages.append(message)

    def offer(self, item, rsi, price, message: str, settings: dict) -> bool:
        """命中区间且未被闩锁时收集消息，返回是否真的加入了待推送批次。"""
        if not should_push(item, rsi, price, settings):
            return False
        self.collect(item, rsi, price, message)
        return True

    def flush(self) -> bool:
        """发送本批消息；无内容时打印空批次提示并返回 False。"""
        if not self.messages:
            if self.empty_note:
                print(self.empty_note)
            return False
        if send_feishu_msg(self.webhook or FEISHU_WEBHOOK, "\n\n".join(self.messages)):
            return True
        print("❌ 飞书推送失败，回滚本轮新增的告警闩锁，下轮重试")
        for item, rsi, price in self.pending:
            # last_rsi 也要退回上一轮的值：反转突破靠「前值在超卖区」判边缘，
            # 若把当前值留在库里，这次告警就再也检测不出来了。
            prev = item.get("last_rsi")
            update_asset_alert_state(item.get("id"), active=bool(item.get("alarm_active")),
                                     latched=False,
                                     rsi=float(prev) if prev is not None else float(rsi),
                                     price=float(price), alert=False)
        return False


# ================= A 股取数登录 =================
def baostock_login() -> bool:
    """登录 baostock；失败时 A 股取数自动降级到腾讯源。"""
    try:
        import baostock as bs

        lg_ret = bs.login()
        if lg_ret.error_code == '0':
            print("✅ baostock login success")
            return True
    except Exception as e:
        print(f"⚠️ baostock登录失败，A股源将全部降级到腾讯源 {e}")
    return False


def baostock_logout() -> None:
    try:
        import baostock as bs

        bs.logout()
    except Exception:
        pass


# ================= 各资产类别的扫描循环 =================
def scan_a_share(items, settings, tag, batch: AlertBatch, bs_login_ok: bool) -> None:
    """逐只扫 A 股类标的（可转债 / ETF），命中区间的加入批次。"""
    tf = monitor_rsi.timeframe_label(settings["frequency"])
    low, high, period = settings["rsi_low"], settings["rsi_high"], settings["period"]
    for item in items:
        if not item.get("enabled", True):
            continue
        code = str(item.get("code"))
        cfg_name = item.get("name", code)
        rsi, price = monitor_rsi.get_a_share_rsi(
            code, bs_login_ok, length=period, frequency=settings["frequency"])
        if rsi is not None and price is not None:
            print(f"✅ [{tag}] {cfg_name}({code}) 现价: {price:.3f}, {tf} RSI({period}): {rsi:.2f}")
            prev_rsi = item.get("last_rsi")
            if should_push_rebound(item, rsi, price, settings):
                message = (f"🔁 【{tag} RSI 反转突破】{cfg_name}({code}) 现价: {price:.3f} 元，"
                           f"{tf} RSI: {rsi:.2f}（上穿 {low}，前值 {float(prev_rsi):.2f}），"
                           f"已脱离超卖区")
                batch.collect(item, rsi, price, message)
            elif rsi < low:
                message = (f"🚨 【{tag} RSI 超卖】{cfg_name}({code}) 现价: {price:.3f} 元，"
                           f"{tf} RSI: {rsi:.2f} (低于 {low})")
                batch.offer(item, rsi, price, message, settings)
            else:
                message = (f"⚠️ 【{tag} RSI 超买】{cfg_name}({code}) 现价: {price:.3f} 元，"
                           f"{tf} RSI: {rsi:.2f} (高于 {high})")
                batch.offer(item, rsi, price, message, settings)
        time.sleep(random.uniform(*SLEEP_A_SHARE))


def scan_meteora_pools(pools, settings, batch: AlertBatch) -> None:
    """Meteora 流动池（GeckoTerminal 池 K 线口径）。"""
    period = settings["period"]
    for pool in pools:
        if not pool.get("enabled", True):
            continue
        address = pool.get("code")
        pool_name = pool.get("name", address)
        if not address:
            continue
        rsi, price = monitor_rsi.get_meteora_rsi(
            address, settings["timeframe"], settings["aggregate"], period)
        if rsi is not None and price is not None:
            print(f"✅ [Meteora] {pool_name} 现价: ${price:.6f}, RSI({period}): {rsi:.2f}")
            if rsi < settings["rsi_low"]:
                message = (f"🚨 【Meteora {pool_name} 超卖】现价 ${price:.6f}，"
                           f"1H RSI: {rsi:.2f} (低于 {settings['rsi_low']})")
            else:
                message = (f"⚠️ 【Meteora {pool_name} 超买】现价 ${price:.6f}，"
                           f"1H RSI: {rsi:.2f} (高于 {settings['rsi_high']})")
            batch.offer(pool, rsi, price, message, settings)
        time.sleep(random.uniform(*SLEEP_METEORA))


def scan_okx_symbols(coins, settings, batch: AlertBatch) -> None:
    """OKX 加密货币；时间级别取每个标的自身的 timeframe。"""
    period = settings["period"]
    for coin in coins:
        if not coin.get("enabled", True):
            continue
        symbol = coin.get("symbol")
        if not symbol:
            continue
        interval = coin.get("timeframe") or settings["interval"]
        tf = monitor_rsi.timeframe_label(interval)
        rsi, price = monitor_rsi.get_okx_rsi(symbol, interval, period)
        if rsi is not None and price is not None:
            print(f"✅ [OKX] {symbol} 现价: ${price:.4f}, {tf} RSI({period}): {rsi:.2f}")
            if rsi < settings["rsi_low"]:
                message = (f"🚨 【{symbol} 超卖】现价 ${price:.4f}，{tf} RSI: {rsi:.2f} "
                           f"(低于 {settings['rsi_low']})")
            else:
                message = (f"⚠️ 【{symbol} 超买】现价 ${price:.4f}，{tf} RSI: {rsi:.2f} "
                           f"(高于 {settings['rsi_high']})")
            batch.offer(coin, rsi, price, message, settings)


def scan_onchain_tokens(tokens, settings, batch: AlertBatch) -> None:
    """链上代币；每只标的自带 chain / timeframe。"""
    period = settings["period"]
    for item in tokens:
        if not item.get("enabled", True):
            continue
        code = str(item.get("code"))
        chain = item.get("chain") or "sol"
        cfg_name = item.get("name", code)
        resolution = item.get("timeframe") or settings["resolution"]
        days = monitor_rsi.TOKEN_RESOLUTION_DAYS.get(resolution, settings.get("days"))
        tf = monitor_rsi.timeframe_label(resolution)
        rsi, price = monitor_rsi.get_token_rsi(chain, code, resolution, period, days)
        if rsi is not None and price is not None:
            short = code[:10]
            print(f"✅ [链上代币] {cfg_name}({short}...) {chain} 现价: {price:.8g}, "
                  f"{tf} RSI({period}): {rsi:.2f}")
            if rsi < settings["rsi_low"]:
                message = (f"🚨 【链上代币 RSI 超卖】{cfg_name}({short}...) 链 {chain} "
                           f"现价: {price:.8g}，{tf} RSI: {rsi:.2f} (低于 {settings['rsi_low']})")
            else:
                message = (f"⚠️ 【链上代币 RSI 超买】{cfg_name}({short}...) 链 {chain} "
                           f"现价: {price:.8g}，{tf} RSI: {rsi:.2f} (高于 {settings['rsi_high']})")
            batch.offer(item, rsi, price, message, settings)
        time.sleep(random.uniform(*SLEEP_A_SHARE))

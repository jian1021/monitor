"""LP 区间 APR 监控：对观察列表里的池子定时跑 uni-range-apr，越界推飞书.

每次运行是秒级的估算（volume feed + 当前流动性），不是分钟级的 replay；
replay 走 Streamlit 页面手动触发。工具链（Node / 子模块）缺失时静默跳过——
CI 环境既没有子模块也没有 node_modules，属预期而不是故障。
"""

from app.core.settings import FEISHU_WEBHOOK
from app.infrastructure.db.apr_watchlist import list_apr_watchlist, record_apr_result
from app.infrastructure.db.pump_alerts import filter_unpushed, mark_pushed
from app.infrastructure.lp_tools import runner
from app.infrastructure.notifications.feishu import FeishuNotifier

APR_MODULE = "lp_apr"
APR_TTL_HOURS = 6    # 同一池同一方向 6 小时内不重复推


def _pct_text(value) -> str:
    return f"{value * 100:.2f}%" if isinstance(value, (int, float)) else "n/a"


def build_threshold_alerts(row: dict, apr: float | None) -> list[tuple[str, str]]:
    """单池阈值判定 → (去重键, 消息)；未配置阈值或 apr 缺失时为空.

    apr 是小数（0.36 = 36%）；min_apr / max_apr 存的是百分数（36.0 = 36%），
    与 Streamlit 页面的输入框一致。
    """
    if apr is None:
        return []
    alerts: list[tuple[str, str]] = []
    pool, name = row["pool"], row.get("name") or row["pool"][:10]
    apr_pct = apr * 100
    params = f"±{row['width']:.0f}% 区间（${row['capital']:,.0f}）估算 APR {apr_pct:.2f}%"
    if row.get("min_apr") is not None and apr_pct < float(row["min_apr"]):
        alerts.append((
            f"{pool}:low",
            f"⚠️ 【LP APR 低于下限】{name}\n"
            f"{params} < 下限 {float(row['min_apr']):.2f}%",
        ))
    if row.get("max_apr") is not None and apr_pct > float(row["max_apr"]):
        alerts.append((
            f"{pool}:high",
            f"🚨 【LP APR 高于上限】{name}\n"
            f"{params} > 上限 {float(row['max_apr']):.2f}%（异常高 APR 通常伴随风险）",
        ))
    return alerts


def run_lp_apr_monitor() -> None:
    rows = [r for r in list_apr_watchlist() if r.get("enabled")]
    if not rows:
        print("⏭️ LP APR 观察列表为空，跳过")
        return
    if not runner.ready() and not runner.ensure_installed():
        print("⏭️ robinhood-chain-lp-tools 未就绪（缺子模块 / node / npm install），跳过")
        return

    pending: list[tuple[str, str]] = []
    for row in rows:
        pool = str(row["pool"])
        try:
            ok, result = runner.run_apr(
                pool,
                width=float(row.get("width") or 10),
                capital=float(row.get("capital") or 10000),
            )
        except Exception as e:
            print(f"❌ [APR] {pool} 执行异常: {e}")
            continue
        if not ok:
            print(f"❌ [APR] {pool} 失败: {str(result)[:300]}")
            continue
        # 优先用波动调整后的 APR 做告警，缺了退回在区间内 APR
        apr = result.get("vol_adjusted_apr")
        if apr is None:
            apr = result.get("apr_in_range")
        if apr is None:
            apr = result.get("apr_in_range_measured")
        record_apr_result(pool, apr)
        label = result.get("name") or pool[:10]
        print(f"✅ [APR] {label} ±{row['width']:.0f}% → {_pct_text(apr)}"
              f"（池均 {_pct_text(result.get('pool_avg_apr'))}）")
        row = {**row, "name": label}
        pending.extend(build_threshold_alerts(row, apr))

    if not pending:
        return
    fresh = set(filter_unpushed(APR_MODULE, [k for k, _ in pending], ttl_hours=APR_TTL_HOURS))
    msgs = [msg for key, msg in pending if key in fresh]
    if msgs:
        FeishuNotifier().send(FEISHU_WEBHOOK, "\n\n".join(msgs))
        mark_pushed(APR_MODULE, [k for k, _ in pending if k in fresh])

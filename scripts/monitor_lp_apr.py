"""monitor_lp_apr.py — LP 区间 APR 监控入口（薄转发层）.

实现位于 app/application/lp_tools/monitor.py；
Node CLI 运行与输出解析位于 app/infrastructure/lp_tools/runner.py。
"""

from app.application.lp_tools.monitor import run_lp_apr_monitor as run_monitor

__all__ = ["run_monitor"]

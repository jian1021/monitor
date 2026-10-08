"""monitor_rhpools.py — Robinhood Pools 观测站监控入口（薄转发层）.

实现位于 app/application/rhpools/monitor.py；
服务进程管理与 API 客户端位于 app/infrastructure/rhpools/client.py。
"""

from app.application.rhpools.monitor import run_rhpools_monitor as run_monitor

__all__ = ["run_monitor"]

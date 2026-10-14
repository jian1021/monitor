"""monitor_price.py — 价格监控 CLI 入口 + 兼容转发层（实现位于 app/application/monitors/price.py）.

用法:
  python -m scripts.monitor_price        # 单轮检查后退出
"""

from app.application.monitors.price import *  # noqa: F401,F403
from app.application.monitors.price import main  # noqa: F401

if __name__ == "__main__":
    main()

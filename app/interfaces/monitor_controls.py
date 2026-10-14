"""Shared inline controls for a background monitoring module."""

import streamlit as st

from app.infrastructure.db.intervals import DEFAULT_INTERVALS, get_module_intervals, update_module_interval
from app.infrastructure.db.module_settings import get_module_settings, update_module_setting


def render_monitor_controls(module_key: str, label: str) -> None:
    """Render the enable switch and polling interval on the module's own page."""
    settings = get_module_settings()
    intervals = get_module_intervals(DEFAULT_INTERVALS)
    enabled = bool(settings.get(module_key, True))
    interval = int(intervals.get(module_key, DEFAULT_INTERVALS.get(module_key, 60)))

    with st.container(border=True):
        st.markdown(f"##### ⚙️ {label}后台监控")
        left, right = st.columns([1, 2])
        new_enabled = left.toggle(
            "启用后台监控", value=enabled, key=f"monitor_enabled_{module_key}"
        )
        new_interval = right.number_input(
            "后台执行间隔（分钟）", min_value=1, max_value=10080,
            value=interval, step=5, key=f"monitor_interval_{module_key}"
        )

        changed = False
        if new_enabled != enabled:
            if update_module_setting(module_key, new_enabled):
                changed = True
            else:
                st.error("启停设置保存失败，请检查数据库连接。")
        if new_interval != interval:
            if update_module_interval(module_key, int(new_interval)):
                changed = True
            else:
                st.error("执行间隔保存失败，请检查数据库连接。")
        if changed:
            st.rerun()

        st.caption(
            "设置仅控制常驻后台进程 `python main.py`；页面上的手动查询或扫描不受影响。"
        )

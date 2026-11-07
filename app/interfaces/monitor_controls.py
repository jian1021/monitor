"""Shared inline controls for a background monitoring module."""

import streamlit as st

from app.core.settings import FEISHU_WEBHOOK
from app.infrastructure.db.intervals import DEFAULT_INTERVALS, get_module_intervals, update_module_interval
from app.infrastructure.db.module_settings import get_module_settings, update_module_setting
from app.infrastructure.db.module_webhooks import get_module_webhook, set_module_webhook


def render_monitor_controls(module_key: str, label: str, *,
                            webhook_editable: bool = False) -> None:
    """Render the enable switch, polling interval and (optional) dedicated webhook."""
    settings = get_module_settings()
    intervals = get_module_intervals(DEFAULT_INTERVALS)
    enabled = bool(settings.get(module_key, True))
    interval = int(intervals.get(module_key, DEFAULT_INTERVALS.get(module_key, 60)))

    with st.container(border=True):
        st.subheader(f"⚙️ {label}后台监控")
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

        if webhook_editable:
            current_hook = get_module_webhook(module_key, "")
            with st.form(f"monitor_webhook_form_{module_key}"):
                new_hook = st.text_input(
                    "专属飞书 Webhook（留空则使用全局）",
                    value=current_hook,
                    key=f"monitor_webhook_{module_key}",
                    placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/...",
                    help="仅本模块的告警发到此群；留空或点「清除」恢复全局 Webhook。",
                )
                save_col, clear_col = st.columns([1, 1])
                if save_col.form_submit_button("保存 Webhook", type="primary"):
                    if set_module_webhook(module_key, new_hook):
                        st.toast("专属 Webhook 已保存", icon="✅")
                        st.rerun()
                    else:
                        st.error("保存失败，请检查数据库连接。")
                if clear_col.form_submit_button("清除（用全局）"):
                    if set_module_webhook(module_key, ""):
                        st.toast("已恢复使用全局 Webhook", icon="🧹")
                        st.rerun()
                    else:
                        st.error("清除失败，请检查数据库连接。")
            st.caption(
                f"当前生效：{current_hook or FEISHU_WEBHOOK or '未配置（仅打印日志）'}"
            )

        st.caption(
            "设置仅控制常驻后台进程 `python main.py`；页面上的手动查询或扫描不受影响。"
        )


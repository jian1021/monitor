"""Feishu notification adapter with dependency injection for tests."""

from collections.abc import Callable
from dataclasses import dataclass

from send_feishu_msg import send_feishu_msg


@dataclass(frozen=True)
class NotifyResult:
    ok: bool
    detail: str = ""


class FeishuNotifier:
    """Sends via an injected sender; returns an explicit result and never swallows sender errors."""

    def __init__(self, sender: Callable[[str, str], object] = send_feishu_msg) -> None:
        self._sender = sender

    def send(self, webhook: str, message: str) -> NotifyResult:
        if not webhook:
            return NotifyResult(False, "webhook 未配置")
        if not self._sender(webhook, message):
            return NotifyResult(False, "飞书发送失败")
        return NotifyResult(True, "已发送")

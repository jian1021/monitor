"""Feishu notification adapter with dependency injection for tests."""

from collections.abc import Callable

from send_feishu_msg import send_feishu_msg


class FeishuNotifier:
    def __init__(self, sender: Callable[[str, str], object] = send_feishu_msg):
        self._sender = sender

    def send(self, webhook: str, message: str) -> object:
        return self._sender(webhook, message)

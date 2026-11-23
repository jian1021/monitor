"""低优先级修复：模块 webhook 统一、弱口令拦截。"""
import pathlib

from app.infrastructure.db import module_webhooks

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_resolve_webhook_prefers_module(monkeypatch):
    monkeypatch.setattr(module_webhooks, "get_module_webhook", lambda name, fb: f"hook-{name}")
    assert module_webhooks.resolve_webhook("lp_alert") == "hook-lp_alert"


def test_resolve_webhook_falls_back_on_error(monkeypatch):
    from app.core.settings import FEISHU_WEBHOOK

    def boom(*_):
        raise RuntimeError("db down")
    monkeypatch.setattr(module_webhooks, "get_module_webhook", boom)
    assert module_webhooks.resolve_webhook("lp_alert") == FEISHU_WEBHOOK


def test_no_module_sends_to_global_webhook_directly():
    offenders = [str(p.relative_to(ROOT)) for p in (ROOT / "app").rglob("*.py")
                 if "send_feishu_msg(FEISHU_WEBHOOK" in p.read_text(encoding="utf-8")]
    assert offenders == []


def test_weak_default_password_removed():
    from app.core import config
    assert "123456" in config.WEAK_ADMIN_PASSWORDS
    src = (ROOT / "app/core/config.py").read_text(encoding="utf-8")
    assert '_setting("ADMIN_PASS", "123456")' not in src

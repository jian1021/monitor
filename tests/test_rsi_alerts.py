"""RSI 告警批次的语义守卫。

这套逻辑从 main.py 下沉到 application 层后，行为必须与原来一致：
1. 命中区间且未闩锁 → 收集消息并写闩锁；
2. 已在区间内被闩锁 → 不再重复推送；
3. 离开区间 → 清闩锁，下次进区间重新武装；
4. 发送失败 → 回滚本轮闩锁，否则告警永久丢失。
"""

from app.application.monitors import rsi_alerts


LOW_HIGH = {"rsi_low": 10, "rsi_high": 90}


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, asset_id, **kwargs):
        self.calls.append((asset_id, kwargs))
        return True


def test_out_of_range_clears_latch(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(rsi_alerts, "update_asset_alert_state", rec)
    batch = rsi_alerts.AlertBatch()

    assert batch.offer({"id": 1}, 50, 1.0, "msg", LOW_HIGH) is False
    assert batch.messages == []
    assert rec.calls[0][1]["latched"] is False


def test_in_range_first_time_collects_and_latches(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(rsi_alerts, "update_asset_alert_state", rec)
    batch = rsi_alerts.AlertBatch()

    assert batch.offer({"id": 2, "alert_latched": False}, 5, 1.0, "msg", LOW_HIGH) is True
    assert batch.messages == ["msg"]
    assert rec.calls[0][1]["latched"] is True


def test_already_latched_is_suppressed(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(rsi_alerts, "update_asset_alert_state", rec)
    batch = rsi_alerts.AlertBatch()

    assert batch.offer({"id": 3, "alert_latched": True}, 5, 1.0, "msg", LOW_HIGH) is False
    assert batch.messages == []


def test_flush_sends_once_and_uses_module_webhook(monkeypatch):
    monkeypatch.setattr(rsi_alerts, "update_asset_alert_state", _Recorder())
    sent = []
    monkeypatch.setattr(rsi_alerts, "send_feishu_msg",
                        lambda hook, text: (sent.append((hook, text)), True)[1])
    batch = rsi_alerts.AlertBatch(webhook="https://hook/etf")
    batch.messages = ["m1", "m2"]

    assert batch.flush() is True
    assert sent == [("https://hook/etf", "m1\n\nm2")]


def test_flush_failure_rolls_back_new_latch(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(rsi_alerts, "update_asset_alert_state", rec)
    monkeypatch.setattr(rsi_alerts, "send_feishu_msg", lambda hook, text: False)
    batch = rsi_alerts.AlertBatch()
    batch.pending = [({"id": 9}, 5.0, 1.0)]
    batch.messages = ["m"]

    assert batch.flush() is False
    assert rec.calls[-1] == (9, {"active": False, "latched": False,
                                 "rsi": 5.0, "price": 1.0, "alert": False})


def test_flush_without_messages_is_noop(monkeypatch):
    called = []
    monkeypatch.setattr(rsi_alerts, "send_feishu_msg",
                        lambda hook, text: called.append(text) or True)
    batch = rsi_alerts.AlertBatch(empty_note="无告警")
    assert batch.flush() is False
    assert called == []

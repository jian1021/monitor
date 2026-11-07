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
    """同一侧持续越界且已闩锁 → 不重复推。"""
    rec = _Recorder()
    monkeypatch.setattr(rsi_alerts, "update_asset_alert_state", rec)
    batch = rsi_alerts.AlertBatch()

    item = {"id": 3, "alert_latched": True, "last_rsi": 5.0}
    assert batch.offer(item, 6, 1.0, "msg", LOW_HIGH) is False
    assert batch.messages == []


def test_opposite_side_is_not_swallowed_by_stale_latch(monkeypatch):
    """RSI(3) 可能从超卖直接跳到超买，旧闩锁不能把反侧告警吞掉。"""
    monkeypatch.setattr(rsi_alerts, "update_asset_alert_state", _Recorder())
    batch = rsi_alerts.AlertBatch()

    item = {"id": 4, "alert_latched": True, "last_rsi": 5.0}
    assert batch.offer(item, 95, 1.0, "超买", LOW_HIGH) is True


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


# ================= 反转突破（超卖 → 中枢）=================
ETF_SET = {"frequency": "w", "period": 3, "rsi_low": 10, "rsi_high": 90,
           "alert_on_rebound": True}
BOND_SET = {"rsi_low": 10, "rsi_high": 90}   # 未开启反转突破


def test_rebound_across_low_pushes(monkeypatch):
    """上一轮在超卖区、本轮回到中枢 → 报一次反转突破。"""
    rec = _Recorder()
    monkeypatch.setattr(rsi_alerts, "update_asset_alert_state", rec)

    item = {"id": 11, "alert_latched": True, "last_rsi": 5.0}
    assert rsi_alerts.should_push_rebound(item, 12.0, 1.0, ETF_SET) is True
    wrote = rec.calls[-1][1]
    assert wrote["latched"] is False and wrote["alert"] is True
    assert wrote["rsi"] == 12.0


def test_rebound_only_once_per_crossing(monkeypatch):
    """回升到 12 之后继续在 10 上方震荡，不应反复报。"""
    monkeypatch.setattr(rsi_alerts, "update_asset_alert_state", _Recorder())
    item = {"id": 12, "alert_latched": False, "last_rsi": 12.0}
    assert rsi_alerts.should_push_rebound(item, 13.0, 1.0, ETF_SET) is False


def test_rebound_needs_previous_low_value(monkeypatch):
    """没有历史 RSI（首次入库）不算突破，避免新标的立刻误报。"""
    monkeypatch.setattr(rsi_alerts, "update_asset_alert_state", _Recorder())
    assert rsi_alerts.should_push_rebound({"id": 13}, 12.0, 1.0, ETF_SET) is False


def test_rebound_jump_to_overbought_is_not_rebound(monkeypatch):
    """从超卖直接跳到超买属于超买事件，不该报成「突破 10」。"""
    monkeypatch.setattr(rsi_alerts, "update_asset_alert_state", _Recorder())
    item = {"id": 14, "alert_latched": True, "last_rsi": 5.0}
    assert rsi_alerts.should_push_rebound(item, 95.0, 1.0, ETF_SET) is False


def test_rebound_disabled_for_other_asset_classes(monkeypatch):
    """可转债等未开启该口径的类别保持原行为。"""
    monkeypatch.setattr(rsi_alerts, "update_asset_alert_state", _Recorder())
    item = {"id": 15, "alert_latched": True, "last_rsi": 5.0}
    assert rsi_alerts.should_push_rebound(item, 12.0, 1.0, BOND_SET) is False


def test_flush_failure_restores_previous_rsi(monkeypatch):
    """发送失败回滚必须把 last_rsi 退回前值，否则突破检测永远丢失。"""
    rec = _Recorder()
    monkeypatch.setattr(rsi_alerts, "update_asset_alert_state", rec)
    monkeypatch.setattr(rsi_alerts, "send_feishu_msg", lambda hook, text: False)
    batch = rsi_alerts.AlertBatch()
    batch.pending = [({"id": 16, "last_rsi": 5.0}, 12.0, 1.0)]
    batch.messages = ["m"]

    assert batch.flush() is False
    assert rec.calls[-1][1]["rsi"] == 5.0


def test_etf_scan_emits_rebound_message(monkeypatch):
    """ETF 扫描里 prev<10 且本轮上穿 10 时，文案要带上「反转突破」与前值。"""
    monkeypatch.setattr(rsi_alerts, "update_asset_alert_state", _Recorder())
    monkeypatch.setattr(rsi_alerts.monitor_rsi, "get_a_share_rsi",
                        lambda code, ok, length=3, frequency="w", **kw: (12.34, 1.5))
    monkeypatch.setattr(rsi_alerts, "time", type("T", (), {
        "sleep": staticmethod(lambda *_: None)}))

    batch = rsi_alerts.AlertBatch()
    rsi_alerts.scan_a_share([{"id": 17, "code": "515790", "name": "光伏ETF",
                              "last_rsi": 2.85}], ETF_SET, "ETF", batch, True)

    assert len(batch.messages) == 1
    msg = batch.messages[0]
    assert "反转突破" in msg and "12.34" in msg and "2.85" in msg

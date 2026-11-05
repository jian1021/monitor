"""告警投递语义：飞书业务码判定、发送失败不得写去重/告警标记。

背景（真实缺陷）：
1. 飞书自定义机器人在签名错误、限流(9499)、关键词不匹配时同样返回 HTTP 200，
   body 里 code != 0。旧实现只看 status_code，把失败当成功。
2. 多个监控模块在 send 之后无条件 mark_pushed / mark_alerted，一旦发送失败，
   该标的会在 TTL（6~24h）甚至永久内被压制 → 告警丢失。
"""

from unittest.mock import patch

from app.application.lp_alert import service as lp_alert_service
from app.application.monitors import meme_rsi_batch, meme_underval
from app.infrastructure.notifications import sender


class _Resp:
    def __init__(self, status_code=200, body=None, raise_json=False):
        self.status_code = status_code
        self._body = body
        self._raise_json = raise_json

    def json(self):
        if self._raise_json:
            raise ValueError("not json")
        return self._body


def test_sender_rejects_feishu_business_error():
    """HTTP 200 + code=19021（签名校验失败）必须判定为失败。"""
    resp = _Resp(200, {"code": 19021, "msg": "sign match fail"})
    with patch.object(sender.requests, "post", return_value=resp):
        assert sender.send_feishu_msg("https://example.com/hook", "hi") is False


def test_sender_accepts_success_code():
    resp = _Resp(200, {"code": 0, "msg": "success"})
    with patch.object(sender.requests, "post", return_value=resp):
        assert sender.send_feishu_msg("https://example.com/hook", "hi") is True


def test_sender_rejects_http_error():
    resp = _Resp(500, {})
    with patch.object(sender.requests, "post", return_value=resp):
        assert sender.send_feishu_msg("https://example.com/hook", "hi") is False


def test_sender_without_webhook_is_failure():
    assert sender.send_feishu_msg("", "hi") is False


def test_meme_underval_does_not_mark_pushed_when_send_fails():
    hits = [{"meme_mint": "mint-1", "symbol": "M", "market_cap": 1, "tvl": 1,
             "fees_24h_usd": 1, "volume_24h_usd": 1, "fee_ratio_24h": 1,
             "base_fee_pct": 1, "bin_step": 1, "rsi": 5, "age_hours": 1,
             "pool_address": "pool-1"}]
    with patch.object(meme_underval, "get_meme_underval_params", return_value={}), \
         patch.object(meme_underval, "fetch_top_performers", return_value=[{"address": "p"}]), \
         patch.object(meme_underval, "scan_undervalued", return_value=hits), \
         patch.object(meme_underval, "filter_unpushed", return_value=["mint-1"]), \
         patch.object(meme_underval, "send_feishu_msg", return_value=False) as send, \
         patch.object(meme_underval, "mark_pushed") as mark:
        result = meme_underval.run_meme_underval_monitor()
    send.assert_called_once()
    mark.assert_not_called()
    assert result == []


def test_meme_rsi_batch_skips_when_all_hits_already_pushed():
    pool = {"address": "pool-address", "symbol": "MEME", "enabled": True,
            "last_rsi": 95.0}
    with patch.object(meme_rsi_batch, "get_meme_rsi_params", return_value={}), \
         patch.object(meme_rsi_batch, "list_meme_watchlist_pools", return_value=[pool]), \
         patch.object(meme_rsi_batch, "scan_watchlist",
                      return_value=[{"address": "pool-address", "symbol": "MEME",
                                     "rsi": 95.0, "price": 1.0, "side": "超买"}]), \
         patch.object(meme_rsi_batch, "clear_pushed"), \
         patch.object(meme_rsi_batch, "filter_unpushed", return_value=[]), \
         patch.object(meme_rsi_batch, "send_feishu_msg") as send:
        result = meme_rsi_batch.run_batch_meme_rsi_monitor()
    send.assert_not_called()
    assert result == []


def test_meme_rsi_batch_rearms_pools_back_in_band():
    """回到正常区间的池子必须清掉推送记录，否则下次越界不会再报。"""
    pool = {"address": "pool-address", "symbol": "MEME", "enabled": True,
            "last_rsi": 50.0}
    with patch.object(meme_rsi_batch, "get_meme_rsi_params", return_value={}), \
         patch.object(meme_rsi_batch, "list_meme_watchlist_pools", return_value=[pool]), \
         patch.object(meme_rsi_batch, "scan_watchlist", return_value=[]), \
         patch.object(meme_rsi_batch, "clear_pushed") as clear:
        meme_rsi_batch.run_batch_meme_rsi_monitor()
    clear.assert_called_once()
    assert clear.call_args[0][1] == ["pool-address"]


def test_lp_alert_does_not_mark_alerted_when_send_fails():
    rule = {"id": 7, "kind": "pool_price", "chain": "sol", "pool_address": "pool"}
    result = {"status": "open", "fired": ["floor"], "cur": {"price": 1.0},
              "message": "🚨 跌穿"}
    with patch.object(lp_alert_service, "load_rules", return_value=[rule]), \
         patch.object(lp_alert_service, "check_rule", return_value=result), \
         patch.object(lp_alert_service, "update_runtime"), \
         patch.object(lp_alert_service, "send_feishu_msg", return_value=False), \
         patch.object(lp_alert_service, "mark_alerted") as mark:
        lp_alert_service.run_once()
    mark.assert_not_called()

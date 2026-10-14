"""
飞书自定义机器人 Webhook 报警（文档 10.）。

- Webhook 与签名密钥从 .env（环境变量）读取，绝不硬编码。
- 支持文本消息；如配置签名密钥则附加签名。
- 相同池子和事件通过 dedup_key + 时间窗去重。
- 每次发送均写入 alerts 表（成功/失败及失败原因）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from .db import Database

# 不同事件的去重时间窗（小时）
DEDUP_WINDOWS = {
    "test": 0.0,
    "new_pool": 24.0,
    "mcap_reached": 24.0,
    "volume_threshold": 6.0,
    "feetvl_threshold": 6.0,
    "watch_change": 3.0,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _fmt(v) -> str:
    if v is None:
        return "-"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(f) >= 1_000_000:
        return f"{f:,.0f}"
    if abs(f) >= 1:
        return f"{f:,.2f}"
    return f"{f:.4f}"


class FeishuAlerter:
    def __init__(self, db: Database, webhook_url: str = "", secret: str = "", timeout: float = 15.0):
        self.db = db
        self.webhook_url = (webhook_url or "").strip()
        self.secret = (secret or "").strip()
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.webhook_url)

    # ------------------------------------------------------------ 传输
    def _build_payload(self, text: str) -> dict:
        payload = {"msg_type": "text", "content": {"text": text}}
        if self.secret:
            ts = str(int(time.time()))
            string_to_sign = f"{ts}\n{self.secret}"
            hmac_code = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
            payload["timestamp"] = ts
            payload["sign"] = base64.b64encode(hmac_code).decode("utf-8")
        return payload

    def _post(self, text: str) -> tuple[bool, str]:
        if not self.configured:
            return False, "未配置 FEISHU_WEBHOOK_URL"
        try:
            resp = requests.post(
                self.webhook_url,
                data=json.dumps(self._build_payload(text)).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            return False, f"网络错误：{exc}"
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        try:
            body = resp.json()
        except ValueError:
            return True, "已发送（响应非 JSON）"
        code = body.get("code", body.get("StatusCode", 0))
        if code in (0, None):
            return True, "success"
        return False, f"飞书返回 code={code}, msg={body.get('msg') or body.get('StatusMessage')}"

    # ------------------------------------------------------------ 业务
    def send_raw(self, text: str) -> tuple[bool, str]:
        return self._post(text)

    def test(self) -> tuple[bool, str]:
        ok, msg = self._post(f"【Meteora 扫描器】测试报警\n时间：{_now_iso()}")
        self.db.log_alert(pool_address="", pool_type="", event_type="test",
                          dedup_key="test", message="测试报警", status="ok" if ok else "fail",
                          error="" if ok else msg)
        return ok, msg

    def build_pool_message(self, event_type: str, row: dict, reason: str,
                           score: Optional[float] = None) -> str:
        tx, ty = row.get("token_x_symbol") or "?", row.get("token_y_symbol") or "?"
        lines = [
            f"【Meteora 报警】{event_type}",
            f"触发原因：{reason}",
            f"池类型：{row.get('pool_type', '-')}",
            f"池地址：{row.get('pool_address', '-')}",
            f"代币 X：{tx} ({row.get('token_x_name') or '-'})  Mint：{row.get('token_x_mint') or '-'}",
            f"代币 Y：{ty} ({row.get('token_y_name') or '-'})  Mint：{row.get('token_y_mint') or '-'}",
            f"市值 X / Y：{_fmt(row.get('token_x_market_cap'))} / {_fmt(row.get('token_y_market_cap'))}",
            f"TVL：{_fmt(row.get('tvl'))}",
            f"24h 成交量：{_fmt(row.get('volume_24h'))}",
            f"24h 手续费收入：{_fmt(row.get('fees_24h'))}",
            f"24h Fees/TVL：{_fmt(row.get('fee_tvl_ratio_24h'))}%",
            f"配置手续费率：{_fmt(row.get('fee_rate_pct'))}%",
        ]
        if score is not None:
            lines.append(f"手续费机会观察评分：{score}")
        lines.append(f"更新时间：{_now_iso()}")
        return "\n".join(lines)

    def send_pool_alert(self, event_type: str, row: dict, reason: str,
                        score: Optional[float] = None) -> tuple[bool, str]:
        """按事件去重后发送池报警，并记录日志。"""
        pool_address = row.get("pool_address", "")
        dedup_key = f"{event_type}:{pool_address}"
        window = DEDUP_WINDOWS.get(event_type, 6.0)
        if window > 0:
            since = (datetime.now(timezone.utc).astimezone() - timedelta(hours=window)).isoformat(timespec="seconds")
            if self.db.alert_sent_since(dedup_key, since):
                return False, "duplicate"

        message = self.build_pool_message(event_type, row, reason, score)
        ok, msg = self._post(message)
        self.db.log_alert(pool_address=pool_address, pool_type=row.get("pool_type", ""),
                          event_type=event_type, dedup_key=dedup_key, message=message,
                          status="ok" if ok else "fail", error="" if ok else msg)
        return ok, msg

    def send_digest(self, event_type: str, title: str, items: list[str], dedup_key: str) -> tuple[bool, str]:
        """发送汇总消息（用于新发现池等批量报警）。"""
        window = DEDUP_WINDOWS.get(event_type, 6.0)
        if window > 0:
            since = (datetime.now(timezone.utc).astimezone() - timedelta(hours=window)).isoformat(timespec="seconds")
            if self.db.alert_sent_since(dedup_key, since):
                return False, "duplicate"
        text = f"【Meteora 报警】{title}\n共 {len(items)} 项\n更新时间：{_now_iso()}\n\n" + "\n\n".join(items)
        ok, msg = self._post(text)
        self.db.log_alert(pool_address="", pool_type="", event_type=event_type,
                          dedup_key=dedup_key, message=text[:2000],
                          status="ok" if ok else "fail", error="" if ok else msg)
        return ok, msg

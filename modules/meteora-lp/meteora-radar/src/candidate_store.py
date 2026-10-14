"""候选名单与提醒状态：持久化到 data/candidates.json。

负责：
* 候选记录的增改查（方案第 7 节的「候选数据记录」字段）。
* 提醒去重 / 冷却（避免持续低 RSI 刷屏）。
* 信号后 15 分钟 / 1 小时 / 4 小时价格表现的记录。
* 池子 TVL 历史，用于检测流动性骤降。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

STORE_VERSION = 1


class CandidateStore:
    def __init__(self, path, logger=None):
        self.path = Path(path)
        self.logger = logger
        self.candidates: dict[str, dict] = {}
        self.tvl_history: dict[str, dict] = {}
        self._loaded = False

    # ---------------- 读写 ----------------
    def load(self) -> "CandidateStore":
        try:
            if self.path.exists():
                with self.path.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    self.candidates = dict(data.get("candidates") or {})
                    self.tvl_history = dict(data.get("tvl_history") or {})
        except Exception as exc:  # noqa: BLE001 - 损坏的存档不应阻断监控
            self._warn(f"读取候选存档失败，按空存档继续: {exc}")
            self.candidates = {}
            self.tvl_history = {}
        self._loaded = True
        return self

    def save(self) -> bool:
        payload = {
            "version": STORE_VERSION,
            "updated_at": int(time.time()),
            "tvl_history": self.tvl_history,
            "candidates": self.candidates,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
            return True
        except Exception as exc:  # noqa: BLE001
            self._warn(f"写入候选存档失败: {exc}")
            return False

    # ---------------- 查询 ----------------
    def all(self) -> list[dict]:
        return list(self.candidates.values())

    def get(self, token_address) -> dict | None:
        return self.candidates.get(str(token_address))

    # ---------------- 候选写入 ----------------
    def upsert_from_screen(self, screen_result, now=None) -> dict:
        """用第一层筛选结果创建/更新候选基础信息，保留已有跟踪字段。"""
        now = int(now if now is not None else time.time())
        metrics = dict(screen_result.metrics or {})
        token = str(metrics.get("meme_mint") or "")
        record = self.candidates.get(token)
        if record is None:
            record = {
                "chain": "solana",
                "token_address": token,
                "first_seen_ts": now,
                "alerts": {},
                "followups": {},
            }
            self.candidates[token] = record

        record.update({
            "pool_address": screen_result.pool_address,
            "symbol": metrics.get("symbol") or record.get("symbol") or "",
            "name": metrics.get("name") or record.get("name") or "",
            "tvl": metrics.get("tvl"),
            "volume_24h_usd": metrics.get("volume_24h_usd"),
            "volume_tvl_ratio": metrics.get("volume_tvl_ratio"),
            "market_cap": metrics.get("market_cap"),
            "holders": metrics.get("holders"),
            "bin_step": metrics.get("bin_step"),
            "base_fee_pct": metrics.get("base_fee_pct"),
            "age_hours": metrics.get("age_hours"),
            "risk": {
                "freeze_authority_disabled": metrics.get("freeze_authority_disabled"),
                "is_blacklisted": metrics.get("is_blacklisted"),
                "is_verified": metrics.get("is_verified"),
            },
            "unknowns": list(screen_result.unknowns or []),
            "last_update_ts": now,
        })
        return record

    def mark_rsi(self, token_address, rsi, price, hit: bool = False, now=None) -> dict | None:
        """记录 1H RSI；仅在命中超跌(hit=True)时写触发时间/价格。"""
        record = self._touch(token_address, now)
        if record is None:
            return None
        now = int(now if now is not None else time.time())
        record["rsi_1h"] = None if rsi is None else round(float(rsi), 4)
        record["rsi_last_ts"] = now
        if hit and record.get("rsi_trigger_ts") is None:
            record["rsi_trigger_ts"] = now
            record["rsi_trigger_price"] = price
        return record

    def mark_signal_baseline(self, token_address, price, now=None):
        """若还没有后验基准价，用首次信号的价格/时间作为基准。"""
        record = self._touch(token_address, now)
        if record is None or price is None:
            return
        if record.get("signal_ts") is None:
            record["signal_ts"] = int(now if now is not None else time.time())
            record["signal_price"] = float(price)

    def mark_stabilized(self, token_address, stabilized: bool, now=None):
        record = self._touch(token_address, now)
        if record is None:
            return
        record["stabilized"] = bool(stabilized)
        record["stabilized_ts"] = int(now if now is not None else time.time())

    def mark_rebound(self, token_address, rebound: dict, price, now=None) -> dict | None:
        record = self._touch(token_address, now)
        if record is None:
            return None
        now = int(now if now is not None else time.time())
        record["rebound"] = dict(rebound or {})
        record["rebound_ts"] = now
        record["rebound_price"] = price
        return record

    # ---------------- 提醒状态 ----------------
    def should_alert(self, token_address, kind: str, now=None, cooldown_minutes: int = 0) -> bool:
        record = self.candidates.get(str(token_address))
        if record is None:
            return False
        now = int(now if now is not None else time.time())
        last = (record.get("alerts") or {}).get(kind)
        if last is None:
            return True
        return now - int(last) >= max(0, int(cooldown_minutes)) * 60

    def mark_alert(self, token_address, kind: str, now=None):
        record = self._touch(token_address, now)
        if record is None:
            return
        record.setdefault("alerts", {})[kind] = int(now if now is not None else time.time())

    # ---------------- 信号后价格表现 ----------------
    def pending_followups(self, now=None, horizons=(15, 60, 240)) -> list[tuple]:
        """返回 [(token, horizon, signal_price)]：到达回看时点但尚未记录。"""
        now = int(now if now is not None else time.time())
        pending = []
        for token, record in self.candidates.items():
            signal_ts = record.get("signal_ts")
            signal_price = record.get("signal_price")
            if signal_ts is None or signal_price is None:
                continue
            done = record.get("followups") or {}
            for horizon in horizons:
                if str(horizon) in done:
                    continue
                if now - int(signal_ts) >= int(horizon) * 60:
                    pending.append((token, int(horizon), float(signal_price)))
        return pending

    def record_followup(self, token_address, horizon, price, now=None) -> bool:
        record = self.candidates.get(str(token_address))
        if record is None or price is None:
            return False
        signal_price = record.get("signal_price")
        if signal_price is None:
            return False
        pct = (float(price) / float(signal_price) - 1.0) * 100.0
        record.setdefault("followups", {})[str(int(horizon))] = {
            "ts": int(now if now is not None else time.time()),
            "price": float(price),
            "pct": round(pct, 4),
        }
        return True

    # ---------------- TVL 历史 / 清理 ----------------
    def update_tvl_history(self, mapping, now=None):
        now = int(now if now is not None else time.time())
        for address, tvl in (mapping or {}).items():
            if address and tvl is not None:
                self.tvl_history[address] = {"tvl": float(tvl), "ts": now}

    def tvl_snapshot(self) -> dict:
        return {addr: item.get("tvl") for addr, item in self.tvl_history.items()}

    def prune(self, now=None, ttl_hours: int = 48, tvl_ttl_hours: int = 24):
        """清理过期候选与 TVL 历史；已触发反弹的候选保留到回看窗口结束。"""
        now = int(now if now is not None else time.time())
        ttl = max(1, int(ttl_hours)) * 3600
        stale = []
        for token, record in self.candidates.items():
            last = record.get("last_update_ts")
            if last is None:
                last = record.get("first_seen_ts")
            if last is None:
                last = now
            signal_ts = record.get("signal_ts")
            if signal_ts is None:
                signal_ts = last
            if now - int(last) > ttl and now - int(signal_ts) > ttl:
                stale.append(token)
        for token in stale:
            self.candidates.pop(token, None)
        tvl_ttl = max(1, int(tvl_ttl_hours)) * 3600
        for address in [a for a, item in self.tvl_history.items()
                        if now - int(item.get("ts") or 0) > tvl_ttl]:
            self.tvl_history.pop(address, None)
        return stale

    # ---------------- 内部 ----------------
    def _touch(self, token_address, now=None) -> dict | None:
        token = str(token_address or "")
        record = self.candidates.get(token)
        if record is None:
            return None
        record["last_update_ts"] = int(now if now is not None else time.time())
        return record

    def _warn(self, message):
        if self.logger:
            self.logger.warning(message)
        else:
            print(message)

"""
重点池监控（文档 8.）。

- 定时刷新监控池的行情与手续费指标。
- 保存历史快照。
- 展示成交量 / TVL / 手续费收入 / Fees-TVL 变化。
- DLMM 池可基于真实 K 线计算 RSI(3)、停止创新低、突破局部高点、成交量恢复信号。
- 反转信号与手续费机会评分分开保存、分开展示。
- RSI(3) 仅用于观察，不用于推算 LP 手续费收益。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional

from . import config
from .db import Database
from .feishu import FeishuAlerter
from .indicators import compute_signals
from .providers import dlmm
from .scanner import Scanner
from .scoring import (compute_score, stability_score, volatility_score_fallback,
                      volatility_score_from_candles)

ProgressCb = Callable[[dict], None]


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _pct_change(new: Optional[float], old: Optional[float]) -> Optional[float]:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / abs(old) * 100.0


class Monitor:
    def __init__(self, db: Database, scanner: Scanner, alerter: Optional[FeishuAlerter] = None):
        self.db = db
        self.scanner = scanner
        self.alerter = alerter

    def _fetch_fresh(self, row: dict):
        """拉取单池最新指标并回写数据库。返回刷新后的 row。"""
        pool_address = row["pool_address"]
        try:
            if row.get("pool_type") == config.POOL_TYPE_DLMM:
                raw = dlmm.fetch_pool(self.scanner.dlmm, pool_address)
                rec = dlmm.parse_pool(raw)
            else:
                return row, None
            self.db.upsert_pool(rec)
            fresh = self.db.get_pool(pool_address)
            return fresh or row, rec
        except Exception as exc:  # noqa: BLE001
            self.db.log_api_error(pool_type=row.get("pool_type") or "", endpoint="/pool-refresh",
                                  attempt=0, message=str(exc))
            return row, None

    def refresh_pool(self, row: dict) -> bool:
        """按池地址重新拉取 Meteora 官方最新行情并回写数据库。成功返回 True。

        用于「筛选结果」页手动刷新单个/多个池的市值、价格、TVL 等，
        避免整池重扫即可拿到实时数据。
        """
        _, rec = self._fetch_fresh(row)
        return rec is not None

    def refresh(self, weights: Optional[dict] = None, *, compute_kline: bool = True,
                change_threshold_pct: float = 20.0,
                progress_cb: Optional[ProgressCb] = None) -> dict:
        watch = [w for w in self.db.list_watch() if w.get("enabled") and not w.get("paused")]
        summary = {"total": len(watch), "refreshed": 0, "failed": 0, "alerts": 0,
                   "signals_found": 0, "items": []}

        for i, w in enumerate(watch, 1):
            pool_address = w["pool_address"]
            row = self.db.get_pool(pool_address)
            if not row:
                summary["failed"] += 1
                continue

            prev = self.db.latest_snapshot(pool_address)
            row, rec = self._fetch_fresh(row)
            if rec is None:
                summary["failed"] += 1
                if progress_cb:
                    progress_cb({"i": i, "total": len(watch), "pool": pool_address, "ok": False})
                continue
            summary["refreshed"] += 1

            # 历史稳定性
            snaps = self.db.get_snapshots(pool_address, 100)
            fee_series = [s["fees_24h"] for s in snaps if s.get("fees_24h") is not None]
            stability = stability_score(fee_series)

            # 波动风险与信号（DLMM K 线）
            volatility = None
            volatility_fallback = False
            signals: dict[str, bool] = {}
            if compute_kline and row.get("pool_type") == config.POOL_TYPE_DLMM:
                try:
                    candles = dlmm.fetch_ohlcv(self.scanner.dlmm_kline, pool_address, "1h")
                    volatility = volatility_score_from_candles(candles)
                    signals = compute_signals(candles)
                except Exception as exc:  # noqa: BLE001
                    self.db.log_api_error(pool_type=row.get("pool_type", ""),
                                          endpoint="/ohlcv", attempt=0, message=str(exc))
            if volatility is None:
                volatility = volatility_score_fallback(row.get("price_change_24h"))
                volatility_fallback = volatility is not None

            sc = compute_score(row, weights, stability=stability, volatility=volatility,
                               volatility_is_fallback=volatility_fallback)
            active_signals = [k for k, v in signals.items() if v]
            if active_signals:
                summary["signals_found"] += 1

            self.db.add_snapshot(
                pool_address,
                pool_type=row.get("pool_type", ""),
                tvl=row.get("tvl"), volume_24h=row.get("volume_24h"), fees_24h=row.get("fees_24h"),
                fee_rate_pct=row.get("fee_rate_pct"), fee_tvl_ratio=row.get("fee_tvl_ratio_24h"),
                token_x_market_cap=row.get("token_x_market_cap"),
                token_y_market_cap=row.get("token_y_market_cap"),
                price=row.get("price"), price_change_24h=row.get("price_change_24h"),
                score=sc["score"], data_integrity="ok", signals=active_signals,
            )

            # 变化检测与报警
            changes = []
            for label, key in (("TVL", "tvl"), ("成交量", "volume_24h"),
                               ("手续费收入", "fees_24h"), ("Fees/TVL", "fee_tvl_ratio_24h")):
                ch = _pct_change(row.get(key), (prev or {}).get(key))
                if ch is not None and abs(ch) >= change_threshold_pct:
                    changes.append(f"{label} 变化 {ch:+.1f}%")
            if changes and self.alerter and self.alerter.configured:
                ok, _ = self.alerter.send_pool_alert("watch_change", row,
                                                     reason="；".join(changes), score=sc["score"])
                if ok:
                    summary["alerts"] += 1

            summary["items"].append({
                "pool_address": pool_address,
                "pool_type": row.get("pool_type"),
                "score": sc["score"],
                "signals": active_signals,
                "notes": sc["notes"],
                "changes": changes,
            })
            if progress_cb:
                progress_cb({"i": i, "total": len(watch), "pool": pool_address, "ok": True})

        return summary

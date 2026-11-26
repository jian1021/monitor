"""数据层：GeckoTerminal 取数、本地 K 线缓存、CSV 读取与 1h 预处理。"""

from __future__ import annotations

import logging
import math
import os
import sqlite3
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd
import requests

log = logging.getLogger("lprange.data")

PUBLIC_BASE = "https://api.geckoterminal.com/api/v2"
PRO_BASE = "https://pro-api.coingecko.com/api/v3/onchain"
METEORA_DLMM_BASE = "https://dlmm.datapi.meteora.ag"
PAGE_LIMIT = 1000
HOUR = 3600


class LPRangeError(Exception):
    """面向用户的错误：只打印消息，不打印堆栈。"""


class NotFound(LPRangeError):
    pass


# ================= GeckoTerminal 客户端 =================
class GeckoClient:
    """GeckoTerminal / CoinGecko Onchain 客户端：限流、429/5xx 指数退避、超时。

    - 未设置 CG_API_KEY：公共接口（官方说明免费额度约 30 次/分钟，默认间隔 2.5 秒）；
    - 设置 CG_API_KEY：改用 pro-api，请求头 x-cg-pro-api-key。
    """

    def __init__(self, interval: float = 2.5, timeout: float = 15.0, max_retries: int = 5,
                 session: requests.Session | None = None) -> None:
        key = os.getenv("CG_API_KEY", "").strip()
        self.base = PRO_BASE if key else PUBLIC_BASE
        self.headers = {"Accept": "application/json", "User-Agent": "lprange/1.0"}
        if key:
            self.headers["x-cg-pro-api-key"] = key
        self.interval, self.timeout, self.max_retries = interval, timeout, max_retries
        self.session = session or requests.Session()
        self._last = 0.0
        self.requests_made = 0

    def _wait(self) -> None:
        delay = self.interval - (time.monotonic() - self._last)
        if delay > 0:
            time.sleep(delay)
        self._last = time.monotonic()

    def get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base}{path}"
        last_err: str = ""
        for attempt in range(self.max_retries + 1):
            self._wait()
            try:
                resp = self.session.get(url, params=params, headers=self.headers, timeout=self.timeout)
                self.requests_made += 1
            except requests.RequestException as exc:
                last_err = f"网络错误：{type(exc).__name__}"
                resp = None
            if resp is not None:
                if resp.status_code == 404:
                    raise NotFound(f"接口返回 404：{path}")
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_err = f"HTTP {resp.status_code}"
                    retry_after = resp.headers.get("Retry-After")
                else:
                    if resp.status_code >= 400:
                        raise LPRangeError(f"接口请求失败 HTTP {resp.status_code}：{resp.text[:200]}")
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise LPRangeError(f"接口返回的不是 JSON：{path}") from exc
            else:
                retry_after = None
            if attempt >= self.max_retries:
                break
            backoff = float(retry_after) if retry_after and retry_after.isdigit() else \
                min(60.0, self.interval * (2 ** attempt))
            log.warning("请求失败（%s），%.1f 秒后重试 %d/%d", last_err, backoff, attempt + 1,
                        self.max_retries)
            time.sleep(backoff)
        raise LPRangeError(f"接口多次重试仍失败（{last_err}）：{path}")

    # ---- 业务接口 ----
    def pool_info(self, network: str, address: str) -> dict:
        payload = self.get(f"/networks/{network}/pools/{address}")
        return parse_pool(payload.get("data") or {}, network)

    def top_pool_for_token(self, network: str, token: str) -> dict:
        payload = self.get(f"/networks/{network}/tokens/{token}/pools")
        pools = [parse_pool(p, network) for p in (payload.get("data") or [])]
        if not pools:
            raise NotFound(f"代币 {token} 在 {network} 上没有可用的池子")
        return max(pools, key=lambda p: p.get("reserve_usd") or 0.0)

    def ohlcv_page(self, network: str, pool: str, before: int | None, currency: str,
                   token: str) -> list[list[float]]:
        params = {"aggregate": 1, "limit": PAGE_LIMIT, "currency": currency, "token": token}
        if before is not None:
            params["before_timestamp"] = int(before)
        payload = self.get(f"/networks/{network}/pools/{pool}/ohlcv/hour", params)
        rows = ((payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        return [r for r in rows if isinstance(r, (list, tuple)) and len(r) >= 6]


def _to_float(v) -> float | None:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def parse_pool(item: dict, network: str) -> dict:
    attrs = item.get("attributes") or {}
    return {
        "network": network,
        "address": attrs.get("address") or str(item.get("id", "")).split("_", 1)[-1],
        "name": attrs.get("name") or "",
        "price_usd": _to_float(attrs.get("base_token_price_usd")),
        "quote_price_usd": _to_float(attrs.get("quote_token_price_usd")),
        "price_in_quote": _to_float(attrs.get("base_token_price_quote_token")),
        "reserve_usd": _to_float(attrs.get("reserve_in_usd")),
        "created_at": attrs.get("pool_created_at"),
        "dex": ((item.get("relationships") or {}).get("dex") or {}).get("data", {}).get("id"),
    }


def resolve_pool(client: GeckoClient, network: str, address: str) -> tuple[dict, bool]:
    """先按池子地址查；404 再当作代币地址，取流动性最大的池子。返回 (池子信息, 是否由代币解析)。"""
    try:
        return client.pool_info(network, address), False
    except NotFound:
        log.info("按池子地址未找到，尝试作为代币地址查询 …")
    try:
        return client.top_pool_for_token(network, address), True
    except NotFound as exc:
        raise LPRangeError(f"地址无效或池子不存在：{address}（network={network}）") from exc


def fetch_meteora_bin_step(address: str, timeout: float = 10.0) -> int | None:
    """尽力从 Meteora DLMM 接口读取 bin_step；拿不到返回 None（不影响主流程）。"""
    try:
        resp = requests.get(f"{METEORA_DLMM_BASE}/pools/{address}", timeout=timeout,
                            headers={"User-Agent": "lprange/1.0"})
        if resp.status_code != 200:
            return None
        data = resp.json() or {}
    except Exception:  # noqa: BLE001
        return None
    for holder in (data, data.get("pool_config") or {}, data.get("dlmm_params") or {},
                   data.get("parameters") or {}):
        value = holder.get("bin_step") if isinstance(holder, dict) else None
        if value not in (None, ""):
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


# ================= 本地 K 线缓存 =================
class OhlcvCache:
    """SQLite 缓存，只存接口原始 K 线（不存补齐值）。

    主键在 (network, pool, ts) 基础上加了 series（如 "usd:base"）：同一池子的 USD 口径和
    币本位口径价格不同，必须分开缓存，否则会互相覆盖。
    """

    def __init__(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS ohlcv (
                network TEXT NOT NULL, pool TEXT NOT NULL, series TEXT NOT NULL DEFAULT 'usd:base',
                ts INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL, volume REAL,
                PRIMARY KEY (network, pool, series, ts)
            );
            CREATE TABLE IF NOT EXISTS ohlcv_meta (
                network TEXT NOT NULL, pool TEXT NOT NULL, series TEXT NOT NULL,
                earliest_ts INTEGER, PRIMARY KEY (network, pool, series)
            );
        """)

    def bounds(self, network: str, pool: str, series: str) -> tuple[int | None, int | None]:
        row = self.conn.execute("SELECT MIN(ts), MAX(ts) FROM ohlcv WHERE network=? AND pool=? AND series=?",
                                (network, pool, series)).fetchone()
        return row[0], row[1]

    def write(self, network: str, pool: str, series: str, rows: list[list[float]]) -> None:
        self.conn.executemany(
            "INSERT OR REPLACE INTO ohlcv VALUES (?,?,?,?,?,?,?,?,?)",
            [(network, pool, series, int(r[0]), r[1], r[2], r[3], r[4], r[5]) for r in rows])
        self.conn.commit()

    def read(self, network: str, pool: str, series: str, since: int) -> list[tuple]:
        return self.conn.execute(
            "SELECT ts, open, high, low, close, volume FROM ohlcv WHERE network=? AND pool=? AND series=?"
            " AND ts >= ? ORDER BY ts", (network, pool, series, since)).fetchall()

    def earliest_known(self, network: str, pool: str, series: str) -> int | None:
        row = self.conn.execute("SELECT earliest_ts FROM ohlcv_meta WHERE network=? AND pool=? AND series=?",
                                (network, pool, series)).fetchone()
        return row[0] if row else None

    def mark_earliest(self, network: str, pool: str, series: str, ts: int) -> None:
        self.conn.execute("INSERT OR REPLACE INTO ohlcv_meta VALUES (?,?,?,?)", (network, pool, series, ts))
        self.conn.commit()

    def clear(self, network: str, pool: str, series: str) -> None:
        self.conn.execute("DELETE FROM ohlcv WHERE network=? AND pool=? AND series=?", (network, pool, series))
        self.conn.execute("DELETE FROM ohlcv_meta WHERE network=? AND pool=? AND series=?", (network, pool, series))
        self.conn.commit()


@dataclass
class FetchReport:
    requests: int = 0
    new_rows: int = 0
    from_cache: int = 0
    exhausted: bool = False      # 接口已经没有更早的数据


def _page_back(client: GeckoClient, network: str, pool: str, currency: str, token: str,
               before: int | None, stop_at: int, max_pages: int, sink) -> tuple[int, bool]:
    """从 before 往前翻页，直到最旧时间戳 ≤ stop_at 或接口返回空。返回 (行数, 是否到头)。"""
    total = 0
    for _ in range(max_pages):
        rows = client.ohlcv_page(network, pool, before, currency, token)
        if not rows:
            return total, True
        sink(rows)                       # 每页先落地（缓存），网络中断也不丢已拉到的数据
        total += len(rows)
        oldest = int(min(r[0] for r in rows))
        if oldest <= stop_at:
            return total, False
        before = oldest - 1
    return total, False


def load_ohlcv(client: GeckoClient, network: str, pool: str, days: float, *, currency: str = "usd",
               token: str = "base", cache: OhlcvCache | None = None, refresh: bool = False,
               now: int | None = None) -> tuple[pd.DataFrame, FetchReport]:
    """拉取（并缓存）最近 days 天的 1h K 线原始数据。"""
    now = int(now or time.time())
    start = now - int(days * 86400)
    series = f"{currency}:{token}"
    report = FetchReport()
    max_pages = int(math.ceil(days * 24 / PAGE_LIMIT)) + 3
    mem: dict[int, list[float]] = {}

    def sink(rows):
        for r in rows:
            mem[int(r[0])] = r
        if cache is not None:
            cache.write(network, pool, series, rows)

    req0 = client.requests_made
    if cache is not None and refresh:
        cache.clear(network, pool, series)
    lo, hi = cache.bounds(network, pool, series) if cache is not None else (None, None)

    if hi is None:
        n, exhausted = _page_back(client, network, pool, currency, token, None, start, max_pages, sink)
        report.new_rows, report.exhausted = n, exhausted
    else:
        # 增量：从现在往回补到缓存最新一根（含，最新一根可能是当时未收盘的小时，需要覆盖）
        n, _ = _page_back(client, network, pool, currency, token, None, hi, max_pages, sink)
        report.new_rows += n
        # 需要更早的历史：从缓存最早一根往前补
        earliest = cache.earliest_known(network, pool, series)
        if lo > start + HOUR and not (earliest is not None and earliest >= lo):
            n, exhausted = _page_back(client, network, pool, currency, token, lo - 1, start, max_pages, sink)
            report.new_rows += n
            report.exhausted = exhausted
        elif earliest is not None and earliest >= lo:
            report.exhausted = True
    if cache is not None and report.exhausted:
        first, _ = cache.bounds(network, pool, series)
        if first is not None:
            cache.mark_earliest(network, pool, series, first)
    report.requests = client.requests_made - req0

    if cache is not None:
        rows = cache.read(network, pool, series, start)
        report.from_cache = max(0, len(rows) - report.new_rows)
    else:
        rows = [tuple(mem[k]) for k in sorted(mem) if k >= start]
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    return df, report


# ================= CSV =================
_TS_ALIASES = ("timestamp", "ts", "time", "date", "datetime")


def _parse_ts(col: pd.Series) -> pd.Series:
    """自动识别秒 / 毫秒 / ISO 字符串，统一成 UTC 秒。"""
    num = pd.to_numeric(col, errors="coerce")
    if num.notna().mean() > 0.9:
        unit_ms = num.dropna().abs().median() > 1e11
        return (num / 1000 if unit_ms else num).round().astype("Int64")
    dt = pd.to_datetime(col, utc=True, errors="coerce")
    # 不依赖 datetime64 的内部精度（pandas 2 可能是 s/ms/us/ns），直接算距 epoch 的秒数
    secs = (dt - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)
    return secs.astype("Int64")


def load_csv(path: str) -> pd.DataFrame:
    try:
        raw = pd.read_csv(path)
    except FileNotFoundError as exc:
        raise LPRangeError(f"找不到 CSV 文件：{path}") from exc
    cols = {c.lower().strip(): c for c in raw.columns}
    ts_col = next((cols[a] for a in _TS_ALIASES if a in cols), None)
    missing = [c for c in ("high", "low", "close") if c not in cols]
    if ts_col is None or missing:
        raise LPRangeError("CSV 需要包含 timestamp/high/low/close 列"
                           + (f"（缺少：{', '.join(missing)}）" if missing else "（缺少 timestamp）"))
    df = pd.DataFrame({"ts": _parse_ts(raw[ts_col])})
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(raw[cols[c]], errors="coerce") if c in cols else np.nan
    df = df[df["ts"].notna()].copy()
    df["ts"] = df["ts"].astype("int64")
    return df


# ================= 预处理 =================
@dataclass
class Prepared:
    ts: np.ndarray
    close: np.ndarray
    frame: pd.DataFrame
    gap_ratio: float
    history_days: float
    dropped_bad: int


def prepare_hourly(df: pd.DataFrame, extend_to: int | None = None) -> Prepared:
    """统一成 UTC 1h、升序、补齐缺失小时。

    - 价格为 0 / NaN 的 K 线剔除，并计入 gap；
    - 缺失小时：close 取前值，open/high/low = 该 close，volume = 0；
    - extend_to：把窗口终点补到该时刻（向下取整到小时），冷门池最近几小时无成交时不滞后。
    """
    if df is None or df.empty:
        raise LPRangeError("池子无历史数据")
    d = df.copy()
    d["close"] = pd.to_numeric(d["close"], errors="coerce")
    bad = ~(d["close"] > 0) | ~np.isfinite(d["close"])
    dropped = int(bad.sum())
    d = d[~bad]
    if d.empty:
        raise LPRangeError("池子无有效价格数据（全部为 0 或 NaN）")
    d["ts"] = (d["ts"].astype("int64") // HOUR) * HOUR
    d = d.sort_values("ts").drop_duplicates("ts", keep="last").set_index("ts")
    start = int(d.index.min())
    end = int(d.index.max())
    if extend_to is not None:
        end = max(end, (int(extend_to) // HOUR) * HOUR)
    full = pd.RangeIndex(start, end + HOUR, HOUR)
    d = d.reindex(full)
    filled = d["close"].isna()
    d["close"] = d["close"].ffill()
    for c in ("open", "high", "low"):
        d[c] = pd.to_numeric(d[c], errors="coerce").where(~filled, d["close"]).fillna(d["close"])
    d["volume"] = pd.to_numeric(d["volume"], errors="coerce").where(~filled, 0.0).fillna(0.0)
    d.index.name = "ts"
    n = len(d)
    return Prepared(ts=d.index.to_numpy(dtype=np.int64), close=d["close"].to_numpy(float),
                    frame=d.reset_index(), gap_ratio=float(filled.sum() / n),
                    history_days=n / 24, dropped_bad=dropped)

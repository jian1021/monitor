"""lprange 数据层、缓存、watchlist、Turso 导入与 CLI 的测试（不联网）。"""

import json

import numpy as np
import pandas as pd
import pytest

from lprange import cli, data, turso
from lprange.config import load_config
from lprange.watchlist import Watchlist, parse_import_line

HOUR = 3600
POOL = "7qbRF6YsyGuLUVs6Y1q64bdVrfe4ZcUUz1JRdoVNUJnm"
OTHER = "So11111111111111111111111111111111111111112"


class FakeClient:
    """模拟 GeckoTerminal：倒序分页、每页 1000 根。"""

    def __init__(self, now, hours, pools=(POOL,)):
        self.now = now // HOUR * HOUR
        self.hours, self.pools = hours, pools
        self.requests_made, self.calls = 0, []

    def pool_info(self, network, address):
        self.requests_made += 1
        if address not in self.pools:
            raise data.NotFound("404")
        return {"network": network, "address": address, "name": "TEST/SOL"}

    def top_pool_for_token(self, network, token):
        self.requests_made += 1
        if token == "TokenMint1111111111111111111111111111111111":
            return {"network": network, "address": POOL, "name": "TEST/SOL"}
        raise data.NotFound("no pools")

    def ohlcv_page(self, network, pool, before, currency, token):
        self.requests_made += 1
        self.calls.append(before)
        top = self.now if before is None else int(before) // HOUR * HOUR
        oldest = self.now - (self.hours - 1) * HOUR
        rows, t = [], top
        while t >= oldest and len(rows) < data.PAGE_LIMIT:
            k = (t - oldest) // HOUR
            rows.append([t, 1, 1, 1, 100 * np.exp(0.02 * np.sin(k / 9)), 1.0])
            t -= HOUR
        return rows


NOW = 1_790_000_000


# ---------- 预处理 ----------
def test_prepare_fills_gaps_drops_bad_and_extends():
    ts = NOW // HOUR * HOUR + np.arange(48) * HOUR
    df = pd.DataFrame({"ts": ts, "open": 1.0, "high": 1.0, "low": 1.0,
                       "close": np.linspace(1, 2, 48), "volume": 1.0})
    df = df.drop([5, 6, 7])
    df.loc[10, "close"] = 0
    df.loc[11, "close"] = np.nan
    p = data.prepare_hourly(df, extend_to=int(ts[-1]) + 3 * HOUR + 59)
    assert len(p.close) == 51 and p.dropped_bad == 2
    assert p.gap_ratio == pytest.approx(8 / 51)
    gap = p.frame.iloc[5:8]
    assert (gap["volume"] == 0).all() and (gap["high"] == gap["close"]).all()
    assert np.all(np.diff(p.ts) == HOUR)


def test_prepare_empty_raises_friendly_error():
    with pytest.raises(data.LPRangeError, match="无历史数据"):
        data.prepare_hourly(pd.DataFrame(columns=["ts", "close"]))


@pytest.mark.parametrize("values", [
    [NOW, NOW + HOUR],                                      # 秒
    [NOW * 1000, (NOW + HOUR) * 1000],                      # 毫秒
    ["2026-09-21T13:46:40Z", "2026-09-21T14:46:40Z"],       # ISO
])
def test_csv_timestamp_autodetect(tmp_path, values):
    f = tmp_path / "k.csv"
    pd.DataFrame({"Timestamp": values, "High": [1, 1], "Low": [1, 1], "Close": [1, 1]}).to_csv(f, index=False)
    ts = data.load_csv(str(f))["ts"].tolist()
    assert ts[1] - ts[0] == HOUR


def test_csv_missing_columns(tmp_path):
    f = tmp_path / "k.csv"
    pd.DataFrame({"timestamp": [1], "close": [1]}).to_csv(f, index=False)
    with pytest.raises(data.LPRangeError, match="high"):
        data.load_csv(str(f))


# ---------- 取数与缓存（验收 5 的离线部分） ----------
def test_cache_incremental_and_backfill(tmp_path):
    cache = data.OhlcvCache(str(tmp_path / "c.db"))
    fc = FakeClient(NOW, 24 * 200)
    df, rep = data.load_ohlcv(fc, "solana", POOL, 180, cache=cache, now=NOW)
    assert len(df) == 180 * 24 and rep.requests == 5        # 约 4320 根，5 次请求
    fc.calls.clear()
    fc.now += 5 * HOUR
    df, rep = data.load_ohlcv(fc, "solana", POOL, 180, cache=cache, now=NOW + 5 * HOUR)
    assert rep.requests == 1 and fc.calls == [None]           # 二次运行只增量拉取
    # 需要更早历史：先缓存 30 天，再要 180 天 → 往前补
    cache2 = data.OhlcvCache(str(tmp_path / "c2.db"))
    later = NOW + 5 * HOUR
    data.load_ohlcv(fc, "solana", POOL, 30, cache=cache2, now=later)
    df, rep = data.load_ohlcv(fc, "solana", POOL, 180, cache=cache2, now=later)
    assert len(df) == 180 * 24 and rep.requests == 5          # 1 次增量 + 4 页向前补


def test_short_history_marks_exhausted_and_stops_paging(tmp_path):
    cache = data.OhlcvCache(str(tmp_path / "c.db"))
    fc = FakeClient(NOW, 24 * 50)
    df, rep = data.load_ohlcv(fc, "solana", POOL, 180, cache=cache, now=NOW)
    assert rep.exhausted and len(df) == 50 * 24
    df, rep = data.load_ohlcv(fc, "solana", POOL, 180, cache=cache, now=NOW)
    assert rep.requests == 1                                   # 不再反复向前翻页


def test_network_failure_keeps_fetched_pages(tmp_path):
    class Flaky(FakeClient):
        def ohlcv_page(self, *a):
            if len(self.calls) >= 2:
                raise data.LPRangeError("接口多次重试仍失败")
            return super().ohlcv_page(*a)

    cache = data.OhlcvCache(str(tmp_path / "c.db"))
    with pytest.raises(data.LPRangeError):
        data.load_ohlcv(Flaky(NOW, 24 * 200), "solana", POOL, 180, cache=cache, now=NOW)
    lo, hi = cache.bounds("solana", POOL, "usd:base")
    assert (hi - lo) // HOUR + 1 == 2000


def test_token_address_falls_back_to_top_pool():
    info, from_token = data.resolve_pool(FakeClient(NOW, 10), "solana",
                                         "TokenMint1111111111111111111111111111111111")
    assert from_token and info["address"] == POOL
    with pytest.raises(data.LPRangeError, match="地址无效"):
        data.resolve_pool(FakeClient(NOW, 10), "solana", OTHER)


# ---------- watchlist ----------
def test_watchlist_upsert_order_and_match(tmp_path):
    wl = Watchlist(str(tmp_path / "w.db"))
    assert wl.upsert("solana", POOL, name="SOL-USDC", note="core")
    assert not wl.upsert("solana", POOL, name="覆盖不了", source="query", touch=True)
    p = wl.get("solana", POOL)
    assert p.name == "SOL-USDC" and p.source == "manual" and p.use_count == 1
    wl.upsert("base", "0x" + "AB" * 20)
    assert wl.list()[0].address == POOL                       # 最近使用排最前
    assert [x.address for x in wl.match("sol-usdc")] == [POOL]
    assert wl.match("1")[0].address == POOL
    assert wl.get("base", "0x" + "ab" * 20) is not None        # EVM 地址统一小写


def test_parse_import_line():
    assert parse_import_line(f"solana:{POOL}", "eth") == ("solana", POOL)
    assert parse_import_line(f"{POOL} solana", "eth") == ("solana", POOL)
    assert parse_import_line(f"base,0x{'AB' * 20}", "solana") == ("base", "0x" + "ab" * 20)
    assert parse_import_line("# comment", "solana") is None
    assert parse_import_line("not an address", "solana") is None


# ---------- Turso ----------
class _RS:
    def __init__(self, rows):
        self.rows = rows


class FakeTurso:
    def execute(self, sql):
        if "meme_watchlist" in sql:
            return _RS([(POOL, "SOL-USDC", None), ("bad", "x", None)])
        if "lp_position_alert" in sql:
            return _RS([(OTHER, "SOL pool", "sol")])
        raise RuntimeError("no such table: asset_config")

    def close(self):
        pass


def test_turso_dry_run_and_import(tmp_path):
    cfg = load_config(db_override=str(tmp_path / "w.db"))
    wl = Watchlist(cfg.db_path)
    rows, added, notes = turso.import_turso(cfg, wl, dry_run=True, client=FakeTurso())
    assert len(rows) == 2 and added == 0 and wl.list() == []
    assert any("查询失败" in n for n in notes)                 # 单个来源失败不影响其他来源
    rows, added, _ = turso.import_turso(cfg, wl, client=FakeTurso())
    assert added == 2
    assert {(p.network, p.source) for p in wl.list()} == {("solana", "turso")}   # sol → solana


def test_turso_rejects_bad_identifier():
    with pytest.raises(turso.TursoError):
        turso.build_query(turso.TursoSource(table="x; drop", address_column="a"))


def test_turso_missing_credentials(monkeypatch):
    monkeypatch.setattr(turso, "turso_credentials", lambda: ("", ""))
    with pytest.raises(turso.TursoError, match="TURSO_DATABASE_URL"):
        turso.connect()


# ---------- CLI ----------
def test_cli_csv_run_and_export(tmp_path, capsys):
    n = 24 * 120
    close = np.exp(np.cumsum(np.random.default_rng(5).normal(0, 0.01, n)))
    f = tmp_path / "k.csv"
    pd.DataFrame({"timestamp": NOW + np.arange(n) * HOUR, "high": close, "low": close,
                  "close": close}).to_csv(f, index=False)
    out = tmp_path / "r.json"
    rc = cli.main(["run", "--csv", str(f), "--bin-step", "100", "--db", str(tmp_path / "w.db"),
                   "--out", str(out)])
    text = capsys.readouterr().out
    assert rc == 0
    for title in ("表一", "表二", "表二-B", "表三", "表四", "数据质量", "不代表未来"):
        assert title in text
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert [h["label"] for h in payload["holds"]] == ["1w", "2w", "3w", "1m"]


def test_cli_run_records_watchlist_and_batch(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "GeckoClient", lambda interval=2.5: FakeClient(NOW, 24 * 200, (POOL, OTHER)))
    monkeypatch.setattr(cli, "fetch_meteora_bin_step", lambda a: 80)
    monkeypatch.setattr(cli.time, "time", lambda: NOW)
    db = str(tmp_path / "w.db")
    assert cli.main(["run", POOL, "--db", db]) == 0
    wl = Watchlist(db)
    assert wl.get("solana", POOL).source == "query"            # 查询后自动入库
    cli.main(["add", OTHER, "--name", "SOL2", "--db", db])
    cli.main(["add", "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin", "--db", db])
    capsys.readouterr()
    out = tmp_path / "b.csv"
    assert cli.main(["all", "--sort", "1w", "--desc", "--db", db, "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "批量汇总" in text and "失败 1 个" in text           # 单个池子失败不影响其余
    assert "1w_bins" in out.read_text(encoding="utf-8-sig")

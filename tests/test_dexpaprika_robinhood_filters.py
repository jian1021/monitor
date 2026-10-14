"""Tests for DexPaprika pool FDV / fee-rate threshold helpers (no network)."""

from app.infrastructure.market_data import dexpaprika_robinhood as dx


def _pool(fee="missing", fdvs=(1_000_000.0, 2_000_000.0)):
    tokens = [{"symbol": f"T{i}", "fdv": v} for i, v in enumerate(fdvs)]
    pool: dict = {"id": "0xabc", "tokens": tokens}
    if fee != "missing":
        pool["fee"] = fee
    return pool


def test_fee_rate_parses_fraction():
    assert dx.pool_fee_rate(_pool(fee=0.01)) == 0.01


def test_fee_rate_none_when_missing():
    assert dx.pool_fee_rate(_pool()) is None


def test_fee_rate_parses_percent_string():
    assert abs(dx.pool_fee_rate(_pool(fee="0.3%")) - 0.003) < 1e-12


def test_fdv_gate_requires_every_token():
    assert dx.apply_pool_thresholds(
        _pool(fee=0.01, fdvs=(200_000.0, 50_000.0)),
        min_token_fdv=100_000.0, min_fee_rate=0.0,
    )["passed"] is False


def test_fdv_and_fee_gates_pass():
    out = dx.apply_pool_thresholds(
        _pool(fee=0.01, fdvs=(200_000.0, 500_000.0)),
        min_token_fdv=100_000.0, min_fee_rate=0.002,
    )
    assert out["passed"] is True
    assert out["max_fdv"] == 500_000.0
    assert out["fee_rate"] == 0.01


def test_missing_fee_fails_enabled_gate():
    out = dx.apply_pool_thresholds(_pool(), min_token_fdv=0, min_fee_rate=0.002)
    assert out["passed"] is False
    assert "费率" in out["reason"]


def test_missing_fdv_fails_enabled_gate():
    pool = {"id": "0xabc", "fee": 0.01, "tokens": [{"symbol": "T0"}]}
    out = dx.apply_pool_thresholds(pool, min_token_fdv=100_000.0, min_fee_rate=0.0)
    assert out["passed"] is False


def test_zero_gates_pass_everything():
    assert dx.apply_pool_thresholds(
        _pool(), min_token_fdv=0, min_fee_rate=0)["passed"] is True

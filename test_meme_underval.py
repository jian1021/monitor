import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import monitor_meme_underval as mu

SOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
MEME = "Meme111111111111111111111111111111111111111"


def _pool(mc_x=5_000_000.0, mc_y=80_000_000.0, bin_step=100,
          base_fee_pct=2.0, fee_ratio_24h=3.5, blacklisted=False, addr="PoolAddr123",
          mint_x=MEME, mint_y=SOL, symbol_x="MEME", symbol_y="SOL"):
    return {
        "address": addr,
        "name": f"{symbol_x}-{symbol_y}",
        "is_blacklisted": blacklisted,
        "tvl": 250_000.0,
        "pool_config": {"bin_step": bin_step, "base_fee_pct": base_fee_pct},
        "fee_tvl_ratio": {"24h": fee_ratio_24h},
        "fees": {"24h": 8750.0},
        "volume": {"24h": 1_200_000.0},
        "token_x": {"address": mint_x, "symbol": symbol_x, "market_cap": mc_x},
        "token_y": {"address": mint_y, "symbol": symbol_y, "market_cap": mc_y},
    }


def test_meme_market_cap_picks_non_quote_side():
    assert mu.meme_market_cap(_pool()) == 5_000_000.0


def test_meme_market_cap_falls_back_to_min_when_both_quote():
    p = _pool(mint_x=SOL, mint_y=USDC, mc_x=70_000_000_000.0, mc_y=8_000_000_000.0)
    assert mu.meme_market_cap(p) == 8_000_000_000.0


def test_meme_market_cap_missing_fields_is_zero():
    assert mu.meme_market_cap({}) == 0.0


def test_base_fee_pct_reads_pool_config():
    assert mu.base_fee_pct(_pool()) == 2.0
    assert mu.base_fee_pct({}) == 0.0


def test_prefilter_passes_default_thresholds():
    assert mu.pool_passes_prefilter(
        _pool(), min_market_cap=1_000_000.0, min_bin_step=100, min_base_fee_pct=2.0
    ) is True


def test_prefilter_rejects_small_cap():
    p = _pool(mc_x=500_000.0)
    assert mu.pool_passes_prefilter(
        p, min_market_cap=1_000_000.0, min_bin_step=100, min_base_fee_pct=2.0
    ) is False


def test_prefilter_rejects_small_bin_step():
    p = _pool(bin_step=25)
    assert mu.pool_passes_prefilter(
        p, min_market_cap=1_000_000.0, min_bin_step=100, min_base_fee_pct=2.0
    ) is False


def test_prefilter_rejects_low_base_fee():
    p = _pool(base_fee_pct=0.5)
    assert mu.pool_passes_prefilter(
        p, min_market_cap=1_000_000.0, min_bin_step=100, min_base_fee_pct=2.0
    ) is False


def test_prefilter_rejects_blacklisted():
    p = _pool(blacklisted=True)
    assert mu.pool_passes_prefilter(
        p, min_market_cap=1_000_000.0, min_bin_step=100, min_base_fee_pct=2.0
    ) is False


def test_prefilter_rejects_zero_tvl():
    p = _pool()
    p["tvl"] = 0.0
    assert mu.pool_passes_prefilter(
        p, min_market_cap=1_000_000.0, min_bin_step=100, min_base_fee_pct=2.0
    ) is False


def test_prefilter_respects_custom_tvl_floor():
    p = _pool()
    p["tvl"] = 5_000.0
    assert mu.pool_passes_prefilter(
        p, min_market_cap=1_000_000.0, min_bin_step=100,
        min_base_fee_pct=2.0, min_tvl_usd=10_000.0
    ) is False
    assert mu.pool_passes_prefilter(
        p, min_market_cap=1_000_000.0, min_bin_step=100,
        min_base_fee_pct=2.0, min_tvl_usd=1_000.0
    ) is True


def test_push_message_contains_links():
    hit = {"symbol": "MEME-SOL", "meme_mint": MEME,
           "pool_address": "PoolAddr123", "market_cap": 5_000_000.0,
           "tvl": 250_000.0, "fee_ratio_24h": 3.5, "base_fee_pct": 2.0,
           "rsi": 6.2}
    msg = mu.build_push_message([hit])
    assert "MEME-SOL" in msg
    assert "2.00%" in msg
    assert "MEME-SOL" in msg
    assert f"https://gmgn.ai/sol/token/{MEME}" in msg
    assert "https://app.meteora.ag/dlmm/PoolAddr123" in msg


def test_scheduler_interval_registered():
    from app.infrastructure.db.intervals import DEFAULT_INTERVALS

    assert DEFAULT_INTERVALS.get("meme_underval") == 30


def test_page_inputs_default_to_saved_params():
    page_src = (REPO_ROOT / "pages" / "discover_lp.py").read_text(encoding="utf-8")
    for key in ("min_market_cap", "min_tvl_usd", "min_bin_step",
                "min_base_fee_pct", "rsi_max"):
        assert f'saved["{key}"]' in page_src
    assert "update_meme_underval_params" in page_src


def test_dedupe_keeps_max_tvl_pool_per_mint():
    pools = [
        _pool(addr="dead", bin_step=20, base_fee_pct=0.2, fee_ratio_24h=1e9),
        _pool(addr="hero", bin_step=100, base_fee_pct=2.0, fee_ratio_24h=3.5),
    ]
    pools[0]["tvl"] = 0.0
    pools[1]["tvl"] = 150_000.0
    other = _pool(addr="other", mint_x="Other1111111111111111111111111111111111111")
    other["tvl"] = 50_000.0
    result = mu.dedupe_by_meme_mint(pools + [other])
    assert {p["address"] for p in result} == {"hero", "other"}


def test_dedupe_empty_safe():
    assert mu.dedupe_by_meme_mint([]) == []
    assert mu.dedupe_by_meme_mint(None) == []


def test_scan_reports_progress_per_candidate():
    calls = []
    pools = [
        _pool(addr="a"),
        _pool(addr="b", mint_x="Other1111111111111111111111111111111111111"),
    ]
    mu.scan_undervalued(
        pools, rsi_top_n=10,
        rsi_fetcher=lambda a, t, g, length: (5.0, 1.0),
        on_progress=lambda done, total, symbol: calls.append((done, total, symbol)),
    )
    assert [c[0] for c in calls] == [1, 2]
    assert [c[1] for c in calls] == [2, 2]


def test_meme_param_defaults_match_module_constants():
    from app.infrastructure.db.meme_config import MEME_PARAM_DEFAULTS
    import monitor_meme_underval as mu

    assert MEME_PARAM_DEFAULTS["min_market_cap"] == mu.DEFAULT_MIN_MARKET_CAP
    assert MEME_PARAM_DEFAULTS["min_tvl_usd"] == mu.DEFAULT_MIN_TVL_USD
    assert MEME_PARAM_DEFAULTS["min_bin_step"] == mu.DEFAULT_MIN_BIN_STEP
    assert MEME_PARAM_DEFAULTS["min_base_fee_pct"] == mu.DEFAULT_MIN_BASE_FEE_PCT
    assert MEME_PARAM_DEFAULTS["rsi_max"] == mu.DEFAULT_RSI_MAX


def test_get_params_falls_back_to_defaults_without_db(monkeypatch):
    import app.infrastructure.db.meme_config as mc

    monkeypatch.setattr(mc, "get_db_client", lambda: None)
    assert mc.get_meme_underval_params() == dict(mc.MEME_PARAM_DEFAULTS)


def test_update_params_fails_gracefully_without_db(monkeypatch):
    import app.infrastructure.db.meme_config as mc

    monkeypatch.setattr(mc, "get_db_client", lambda: None)
    assert mc.update_meme_underval_params({"rsi_max": 8.0}) is False


def test_monitor_run_loads_saved_params_when_none_given(monkeypatch):
    import monitor_meme_underval as mu

    seen = {}
    monkeypatch.setattr(
        mu, "get_meme_underval_params",
        lambda: {"min_market_cap": 1.0, "min_tvl_usd": 1.0, "min_bin_step": 1,
                 "min_base_fee_pct": 0.0, "rsi_max": 100.0,
                 "rsi_top_n": 7, "page_size": 50, "max_pages": 1,
                 "rsi_period": 3},
    )

    def fake_fetch(page_size=100, max_pages=3, timeout=20):
        seen["page_size"] = page_size
        seen["max_pages"] = max_pages
        return []

    monkeypatch.setattr(mu, "fetch_top_performers", fake_fetch)
    assert mu.run_meme_underval_monitor() == []
    assert (seen["page_size"], seen["max_pages"]) == (50, 1)


def test_dead_pump_monitor_fully_removed():
    main_src = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
    assert "meteora_pump" not in main_src
    assert not (REPO_ROOT / "monitor_meteora_pump.py").exists()
    settings_src = (
        REPO_ROOT / "app" / "infrastructure" / "db" / "module_settings.py"
    ).read_text(encoding="utf-8")
    assert "meteora_pump" not in settings_src
    from app.infrastructure.db.intervals import DEFAULT_INTERVALS

    assert "meteora_pump" not in DEFAULT_INTERVALS


def test_monitor_settings_labels_expose_meme_module():
    import ast
    from pathlib import Path

    tree = ast.parse(
        (REPO_ROOT / "pages" / "monitor_settings.py").read_text(encoding="utf-8")
    )
    labels = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "MODULE_LABELS":
            labels = ast.literal_eval(node.value)
    assert labels is not None
    assert "meme_underval" in labels
    assert "meteora_pump" not in labels

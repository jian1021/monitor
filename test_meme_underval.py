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

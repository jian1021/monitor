from config import RadarConfig
from src.meteora_api import SOL_MINT, USDC_MINT
from src.token_filter import PASS, REJECT, REVIEW, dedupe_by_meme_mint, screen_pool

MEME_MINT = "DEW9dSN6QpWyNthphCpMmAbZP1Q4cEKR9xQXAri98WDP"


def make_pool(*, tvl=200_000.0, volume_24h=300_000.0, freeze=True,
              market_cap=5_000_000.0, blacklisted=False,
              quote=SOL_MINT, meme=MEME_MINT):
    return {
        "address": "DLZwtfc9eSfZyD7v8GoaxjgK5x3FK4DXgMSmZLzuJjSB",
        "name": "MEME-SOL",
        "tvl": tvl,
        "volume": {"24h": volume_24h},
        "is_blacklisted": blacklisted,
        "created_at": 1_700_000_000_000,
        "token_x": {
            "address": meme,
            "symbol": "MEME",
            "price": 0.00123,
            "market_cap": market_cap,
            "holders": 1234,
            "freeze_authority_disabled": freeze,
            "is_verified": True,
        },
        "token_y": {"address": quote, "symbol": "SOL", "price": 150.0, "market_cap": 0},
    }


def cfg(**overrides):
    params = dict(
        min_pool_tvl_usd=50_000.0,
        min_volume_24h_usd=50_000.0,
        min_volume_tvl_ratio=0.5,
        min_market_cap_usd=0.0,
    )
    params.update(overrides)
    return RadarConfig(**params)


def test_good_pool_passes_and_flags_unknowns():
    result = screen_pool(make_pool(), cfg())
    assert result.status == PASS
    assert "增发/铸造权限未知" in result.unknowns
    assert result.metrics["volume_tvl_ratio"] == 1.5


def test_low_tvl_rejected():
    result = screen_pool(make_pool(tvl=1000.0), cfg())
    assert result.status == REJECT
    assert any("TVL 过低" in r for r in result.reasons)


def test_low_volume_rejected():
    result = screen_pool(make_pool(volume_24h=1000.0), cfg())
    assert result.status == REJECT
    assert any("成交量过低" in r for r in result.reasons)


def test_freeze_authority_not_disabled_rejected():
    result = screen_pool(make_pool(freeze=False), cfg())
    assert result.status == REJECT
    assert any("冻结权限" in r for r in result.reasons)


def test_blacklisted_rejected():
    result = screen_pool(make_pool(blacklisted=True), cfg())
    assert result.status == REJECT


def test_stable_pair_not_treated_as_meme():
    # token_x 也是计价币 → 非 meme 池
    pool = make_pool(quote=SOL_MINT, meme=USDC_MINT)
    result = screen_pool(pool, cfg())
    assert result.status == REJECT
    assert any("非 meme" in r for r in result.reasons)


def test_missing_core_data_is_review_not_pass():
    pool = make_pool()
    del pool["tvl"]
    result = screen_pool(pool, cfg())
    assert result.status == REVIEW
    assert any("TVL 缺失" in r for r in result.reasons)


def test_market_cap_optional_by_default():
    assert screen_pool(make_pool(market_cap=0.0), cfg()).status == PASS
    strict = screen_pool(make_pool(market_cap=0.0), cfg(min_market_cap_usd=1_000_000.0))
    assert strict.status == REVIEW


def test_strict_risk_unknown_downgrades_to_review():
    assert screen_pool(make_pool(), cfg(strict_risk_unknown=True)).status == REVIEW


def test_tvl_drop_rejected():
    result = screen_pool(make_pool(tvl=40_000.0), cfg(
        min_pool_tvl_usd=10_000.0, min_volume_24h_usd=10_000.0,
        min_volume_tvl_ratio=0.0), previous_tvl=200_000.0)
    assert result.status == REJECT
    assert any("骤降" in r for r in result.reasons)


def test_dedupe_keeps_highest_tvl_per_meme():
    low = make_pool(tvl=100_000.0)
    high = make_pool(tvl=900_000.0)
    high["address"] = "HTvjzsfXsomeOtherPoolAddressxxxxxxxxxxxx"
    deduped = dedupe_by_meme_mint([low, high])
    assert len(deduped) == 1
    assert deduped[0]["tvl"] == 900_000.0

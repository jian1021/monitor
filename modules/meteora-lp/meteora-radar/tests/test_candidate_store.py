from config import RadarConfig
from src.candidate_store import CandidateStore
from src.token_filter import screen_pool
from tests.test_token_filter import MEME_MINT, make_pool


def _make_store(tmp_path):
    path = tmp_path / "candidates.json"
    store = CandidateStore(path).load()
    result = screen_pool(make_pool(), RadarConfig())
    store.upsert_from_screen(result, now=1_000_000)
    return store, path


def test_upsert_and_persist_roundtrip(tmp_path):
    store, path = _make_store(tmp_path)
    token = store.all()[0]["token_address"]
    assert token == MEME_MINT
    assert store.save() is True

    reloaded = CandidateStore(path).load()
    assert reloaded.get(token)["pool_address"] == store.get(token)["pool_address"]


def test_alert_cooldown(tmp_path):
    store, _ = _make_store(tmp_path)
    token = MEME_MINT
    assert store.should_alert(token, "rsi", now=1000, cooldown_minutes=60) is True
    store.mark_alert(token, "rsi", now=1000)
    assert store.should_alert(token, "rsi", now=1000 + 30 * 60, cooldown_minutes=60) is False
    assert store.should_alert(token, "rsi", now=1000 + 61 * 60, cooldown_minutes=60) is True


def test_followups_computed_at_horizons(tmp_path):
    store, _ = _make_store(tmp_path)
    token = MEME_MINT
    store.mark_signal_baseline(token, price=1.0, now=0)

    # 14 分钟时还没到 15m 回看点
    assert store.pending_followups(now=14 * 60, horizons=(15, 60)) == []
    pending = store.pending_followups(now=16 * 60, horizons=(15, 60))
    assert (token, 15, 1.0) in pending

    store.record_followup(token, 15, price=1.2, now=16 * 60)
    record = store.get(token)
    assert record["followups"]["15"]["pct"] == 20.0
    # 已记录的不再重复
    assert store.pending_followups(now=20 * 60, horizons=(15,)) == []


def test_tvl_history_and_prune(tmp_path):
    store, _ = _make_store(tmp_path)
    token = MEME_MINT
    store.update_tvl_history({store.get(token)["pool_address"]: 12345.0}, now=100)
    assert store.tvl_snapshot()[store.get(token)["pool_address"]] == 12345.0

    # 候选很旧且没有信号 → 被清理
    store.get(token)["last_update_ts"] = 0
    store.get(token)["first_seen_ts"] = 0
    stale = store.prune(now=100 * 3600, ttl_hours=48)
    assert token in stale


def test_missing_file_loads_empty(tmp_path):
    store = CandidateStore(tmp_path / "nope.json").load()
    assert store.all() == []

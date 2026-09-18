from pathlib import Path


ROOT = Path(__file__).parent


def test_mainstream_crypto_table_has_alarm_columns_and_inline_actions():
    source = (ROOT / "pages/st_mainstream_crypto.py").read_text(encoding="utf-8")
    assert '"alarm_active"' in source
    assert '"last_alert_at"' in source
    assert "render_asset_grid" in source


def test_mainstream_crypto_reset_alarm_is_a_table_column_without_row_operations():
    source = (ROOT / "pages/st_mainstream_crypto.py").read_text(encoding="utf-8")
    assert "render_asset_grid" in source
    assert 'st.caption("行级操作")' not in source


def test_mainstream_crypto_reset_is_not_an_external_button():
    source = (ROOT / "pages/st_mainstream_crypto.py").read_text(encoding="utf-8")
    assert 'st.button("🔔 重置报警"' not in source
    assert "render_asset_grid" in source


def test_mainstream_crypto_reset_is_inside_table_without_external_controls():
    source = (ROOT / "pages/st_mainstream_crypto.py").read_text(encoding="utf-8")
    assert "render_asset_grid" in source
    assert "选择要重置报警的资产" not in source
    assert 'st.button("🔔 重置报警"' not in source
    assert 'st.caption("行级操作")' not in source


def test_crypto_monitor_uses_shared_alert_state_processor():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    crypto_block = source.split("def run_crypto_monitor", 1)[1].split(
        "def run_onchain_token_monitor", 1
    )[0]
    assert "process_rsi_alert" in crypto_block


def test_onchain_and_oversold_pages_use_the_same_table_alarm_columns():
    onchain = (ROOT / "pages/st_onchain_token.py").read_text(encoding="utf-8")
    oversold = (ROOT / "pages/app.py").read_text(encoding="utf-8")
    for source in (onchain, oversold):
        assert "render_asset_grid" in source
        assert '"alarm_active"' in source
        assert '"last_alert_at"' in source

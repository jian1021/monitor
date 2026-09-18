from pathlib import Path


ROOT = Path(__file__).parent


def test_shared_asset_grid_declares_inline_actions_and_confirmation_contract():
    source = (ROOT / "asset_grid.py").read_text(encoding="utf-8")
    assert "AgGrid" in source
    assert "重置报警" in source
    assert "删除" in source
    assert "pending_delete" in source
    assert "JsCode" in source


def test_inline_action_buttons_return_through_cell_value_changes():
    source = (ROOT / "asset_grid.py").read_text(encoding="utf-8")
    assert "setDataValue" in source
    assert '"cellValueChanged"' in source
    assert "dispatchEvent" not in source


def test_action_columns_are_returned_as_input_rows():
    source = (ROOT / "asset_grid.py").read_text(encoding="utf-8")
    assert "DataReturnMode.AS_INPUT" in source
    assert 'update_on=["cellValueChanged"]' in source


def test_delete_action_renders_confirmation_before_any_rerun():
    source = (ROOT / "asset_grid.py").read_text(encoding="utf-8")
    assignment = 'st.session_state["pending_delete_asset"] = asset_id'
    assert assignment in source
    after_assignment = source.split(assignment, 1)[1].split(
        'pending = st.session_state.get("pending_delete_asset")', 1
    )[0]
    assert "st.rerun()" not in after_assignment


def test_pending_delete_confirmation_is_handled_before_grid_rerenders():
    source = (ROOT / "asset_grid.py").read_text(encoding="utf-8")
    confirmation = 'pending = st.session_state.get("pending_delete_asset")'
    grid_call = "response = AgGrid("
    assert source.index(confirmation) < source.index(grid_call)
    assert "delete_ok" in source


def test_three_pages_use_shared_asset_grid():
    for path in (
        ROOT / "pages/st_mainstream_crypto.py",
        ROOT / "pages/st_onchain_token.py",
        ROOT / "pages/app.py",
    ):
        source = path.read_text(encoding="utf-8")
        assert "render_asset_grid" in source


def test_oversold_and_onchain_pages_pass_delete_callback_to_shared_grid():
    for path in (ROOT / "pages/st_onchain_token.py", ROOT / "pages/app.py"):
        source = path.read_text(encoding="utf-8")
        assert "render_asset_grid" in source
        assert "delete_asset=delete_asset" in source
        assert "reset_asset_alert=reset_asset_alert" in source


def test_aggrid_dependency_is_declared():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "streamlit-aggrid" in requirements

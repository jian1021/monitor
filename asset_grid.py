"""Shared AgGrid renderer for asset configuration pages."""

from collections.abc import Callable

import pandas as pd
import streamlit as st

try:
    from st_aggrid import AgGrid, DataReturnMode, GridOptionsBuilder, JsCode
except ImportError:  # pragma: no cover - exercised only on deployments without the optional dependency
    AgGrid = None
    DataReturnMode = None
    GridOptionsBuilder = None
    JsCode = None


ACTION_RENDERER = """
class ActionRenderer {
  init(params) {
    this.params = params;
    this.eGui = document.createElement('button');
    this.eGui.innerText = params.colDef.headerName;
    this.eGui.className = 'stButton';
    this.eGui.onclick = (event) => {
      event.stopPropagation();
      params.api.stopEditing();
      params.node.setDataValue(params.colDef.field, true);
      params.api.refreshCells({rowNodes: [params.node], force: true});
    };
  }
  getGui() { return this.eGui; }
}
"""


def render_asset_grid(
    data: pd.DataFrame,
    *,
    key: str,
    reset_asset_alert: Callable[[int], bool],
    delete_asset: Callable[[int], bool],
    update_asset_status: Callable[[int, bool], bool],
) -> None:
    """Render a shared asset grid and process inline actions."""
    if AgGrid is None or GridOptionsBuilder is None or JsCode is None:
        st.error("缺少 streamlit-aggrid 依赖，请重新部署并安装 requirements.txt。")
        return

    pending = st.session_state.get("pending_delete_asset")
    if pending is not None:
        st.warning(f"确定要删除 ID {pending} 的资产吗？此操作不可恢复。")
        confirm, cancel = st.columns(2)
        if confirm.button("确认删除", key=f"confirm_delete_{key}", type="primary"):
            delete_ok = bool(delete_asset(int(pending)))
            if delete_ok:
                st.session_state.pop("pending_delete_asset", None)
                st.success("✅ 删除成功")
                st.rerun()
            else:
                st.error("❌ 删除失败：数据库未确认删除，请检查数据库连接。")
        if cancel.button("取消", key=f"cancel_delete_{key}"):
            st.session_state.pop("pending_delete_asset", None)
            st.rerun()
        return

    table = data.copy()
    table["操作"] = False
    table["删除"] = False
    gb = GridOptionsBuilder.from_dataframe(table)
    gb.configure_default_column(resizable=True, sortable=True, filter=True)
    gb.configure_column("ID", editable=False, width=80)
    gb.configure_column("名称", editable=False)
    gb.configure_column("交易对", editable=False)
    gb.configure_column("合约地址", editable=False)
    gb.configure_column("时间级别", editable=False, width=100)
    gb.configure_column("报警中", editable=False, width=100)
    gb.configure_column("最近报警时间", editable=False, width=180)
    gb.configure_column("启用", editable=True, width=90)
    gb.configure_column(
        "操作",
        header_name="重置报警",
        editable=True,
        width=90,
        cellRenderer=JsCode(ACTION_RENDERER),
    )
    gb.configure_column(
        "删除",
        editable=True,
        width=80,
        cellRenderer=JsCode(ACTION_RENDERER),
        header_name="删除",
    )
    grid_options = gb.build()
    response = AgGrid(
        table,
        gridOptions=grid_options,
        data_return_mode=DataReturnMode.AS_INPUT,
        update_on=["cellValueChanged", "rowValueChanged"],
        allow_unsafe_jscode=True,
        height=min(600, 105 + len(table) * 38),
        key=key,
    )

    original_status = {int(row["ID"]): bool(row["启用"]) for row in table.to_dict("records")}
    for row in response.data.to_dict("records"):
        asset_id = int(row["ID"])
        if original_status.get(asset_id) != bool(row["启用"]):
            update_asset_status(asset_id, bool(row["启用"]))

    for row in response.data.to_dict("records"):
        asset_id = int(row["ID"])
        if row.get("操作"):
            reset_asset_alert(asset_id)
            st.toast("✅ 报警状态已重置")
            st.rerun()
        if row.get("删除"):
            st.session_state["pending_delete_asset"] = asset_id

# -*- coding: utf-8 -*-
"""页面文件的结构性守卫（不需要网络/数据库/Streamlit 运行时）."""
import ast
import glob
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TERMINATORS = (ast.Continue, ast.Return, ast.Break, ast.Raise)


def _unreachable_after_terminator(tree):
    """找出与 continue/return/break/raise 同属一个语句块、却被放在其后的语句.

    这类语句永远执行不到：既不会报错，也不会生效（例如把整段渲染逻辑
    误缩进进 `if 空: continue` 分支里），是最难靠运行发现的静默故障。
    """
    found = []
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for index, stmt in enumerate(body[:-1]):
            if isinstance(stmt, TERMINATORS):
                nxt = body[index + 1]
                found.append((stmt.lineno, nxt.lineno, type(nxt).__name__))
                break
    return found


@pytest.mark.parametrize("path", sorted(glob.glob("pages/*.py")))
def test_pages_have_no_statements_after_terminator(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    offenders = _unreachable_after_terminator(tree)
    assert offenders == [], (
        f"{path} 中 {offenders} 位于 continue/return 之后，永远不会执行")


def test_detector_actually_catches_the_pattern():
    """守卫本身要有效：构造一个含该模式的源码，必须被检出."""
    tree = ast.parse(
        "for x in y:\n"
        "    if x:\n"
        "        print('a')\n"
        "        continue\n"
        "        print('永远不会执行')\n"
    )
    assert _unreachable_after_terminator(tree) != []


def test_onchain_token_page_does_not_rerun_for_unalerted_rows():
    source = open("pages/st_onchain_token.py", encoding="utf-8").read()
    assert 'st.caption("—")\n                st.rerun()' not in source


def test_monitor_settings_page_owns_module_controls():
    settings_source = open("pages/monitor_settings.py", encoding="utf-8").read()
    assets_source = open("pages/app.py", encoding="utf-8").read()
    assert "监控模块启停" in settings_source
    assert "监控模块执行间隔" in settings_source
    assert "监控模块启停" not in assets_source
    assert "监控模块执行间隔" not in assets_source


def test_monitor_settings_page_is_registered_under_system_management():
    source = open("index.py", encoding="utf-8").read()
    assert 'pages/monitor_settings.py' in source
    assert "系统管理" in source


def test_mainstream_crypto_assets_render_as_a_table():
    source = open("pages/st_mainstream_crypto.py", encoding="utf-8").read()
    assert "render_asset_grid" in source
    assert "for _, row in sub_df.iterrows()" not in source

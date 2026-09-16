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

"""界面与功能一致性的源码守卫测试。

不回溯的历史教训：
1. onchain_hub 内嵌的子页面各自再渲染 st.title → 同屏出现两个大标题；
   render 入口必须带 embedded 参数，嵌入时跳过标题。
2. `st.success(...)` 紧跟 `st.rerun()` → 提示信息被 rerun 冲掉，用户看不到任何反馈；
   统一改用 st.toast(...)（toast 跨 rerun 存活）。
3. 链选项列表/标签曾在 4 个页面各写一份且文案不一 → 唯一来源是
   app/interfaces/asset_config.py 的 TOKEN_CHAINS / CHAIN_LABELS。
4. 池子价格模拟器：price 为 None 时直接进 simulate() 会 TypeError 崩页。
"""

import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGES = ROOT / "pages"

# 可内嵌进 onchain_hub 标签页的页面与其渲染入口
EMBEDDED_PAGES = {
    "st_onchain_token.py": "render_page",
    "meme_rsi_batch.py": "render_page",
    "fee_screen.py": "render_page",
    "meteora_scanner.py": "main",
}


def _parse(name: str) -> ast.Module:
    return ast.parse((PAGES / name).read_text(encoding="utf-8"))


def _func_def(tree: ast.Module, name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def test_embedded_pages_accept_embedded_param():
    for filename, func in EMBEDDED_PAGES.items():
        fn = _func_def(_parse(filename), func)
        assert fn is not None, f"{filename} 缺少 {func}()"
        args = [a.arg for a in fn.args.args]
        assert "embedded" in args, (
            f"{filename}::{func}() 缺少 embedded 参数——"
            f"嵌入 onchain_hub 时会与外层标题重复渲染"
        )


def test_no_success_message_swallowed_by_rerun():
    """st.success 之后紧跟 st.rerun 会让用户看不到提示，必须改用 st.toast。"""
    pattern = re.compile(r"st\.success\([\s\S]{0,300}?\)\s*\n\s*st\.rerun\(\)")
    offenders = []
    for path in sorted(PAGES.glob("*.py")):
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(path.name)
    assert not offenders, f"以下页面仍存在 st.success + st.rerun（提示会被吞掉）: {offenders}"


def test_chain_options_single_source():
    """链选项唯一来源是 app/interfaces/asset_config.py，页面不得再各自硬编码。"""
    literal = '"sol", "bsc", "base", "eth", "robinhood", "arc", "stable"'
    offenders = []
    for path in sorted(PAGES.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        if literal in src or literal.replace('"', "'") in src:
            offenders.append(path.name)
    assert not offenders, (
        f"以下页面仍硬编码链列表（应与 asset_config.TOKEN_CHAINS 统一）: {offenders}"
    )
    # 各使用方确实引用了共享常量
    for name in ("dashboard.py", "st_monitor_price.py", "st_pool_simulator.py", "lp_bands.py"):
        src = (PAGES / name).read_text(encoding="utf-8")
        assert "TOKEN_CHAINS" in src and "CHAIN_LABELS" in src, f"{name} 未复用共享链常量"


def test_pool_simulator_guards_none_price():
    """拉取失败但无 error 时 price 可能为 None，必须在进入模拟前拦截。"""
    src = (PAGES / "st_pool_simulator.py").read_text(encoding="utf-8")
    assert 'data["price"] is None' in src, "st_pool_simulator 缺少 price=None 的崩页保护"
    # 死逻辑清理：sim_submit 曾经永远为 False
    assert "sim_submit" not in src, "st_pool_simulator 仍残留无效的 sim_submit 分支"

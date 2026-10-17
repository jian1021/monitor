"""界面与功能一致性的源码守卫测试。

不回溯的历史教训：
1. onchain_hub 内嵌的子页面各自再渲染 st.title → 同屏出现两个大标题；
   render 入口必须带 embedded 参数，嵌入时跳过标题。
2. `st.success(...)` 紧跟 `st.rerun()` → 提示信息被 rerun 冲掉，用户看不到任何反馈；
   统一改用 st.toast(...)（toast 跨 rerun 存活）。
3. 链选项列表/标签曾在 4 个页面各写一份且文案不一 → 唯一来源是
   app/interfaces/asset_config.py 的 TOKEN_CHAINS / CHAIN_LABELS。
4. 池子价格模拟器：price 为 None 时直接进 simulate() 会 TypeError 崩页。
5. 内嵌进「全链扫描与链上代币」工作区的页面，标题由外层标签页提供：
   子页面不得再输出 st.header/st.title 与标签名重复的标题
   （Meteora 页曾出现「tab 名 + header 同名」的双标题，且每个二级 tab
   顶部堆了 1~4 条说明 caption）。
6. 页面标题用 st.title，分区标题用 st.subheader；不得用
   st.markdown('### ...') 冒充标题（层级与其余页面不一致）。
"""

import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGES = ROOT / "pages"
INTERFACES = ROOT / "app" / "interfaces"

# 可内嵌进 onchain_hub 标签页的页面与其渲染入口
EMBEDDED_PAGES = {
    "st_onchain_token.py": "render_page",
    "meme_rsi_batch.py": "render_page",
    "fee_screen.py": "render_page",
    "meteora_scanner.py": "render_page",
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


def test_section_headings_use_subheader():
    """分区标题统一用 st.subheader，不用 st.markdown('#...')（页面内混用会层级不一）。

    只匹配「紧跟左括号的标题行」，避免误伤 st.markdown 里的多行 markdown 正文。
    """
    pattern = re.compile(r'st\.markdown\([ \t]*f?["\']#{1,6}[ \t]')
    offenders = []
    for path in sorted(PAGES.glob("*.py")) + sorted(INTERFACES.glob("*.py")):
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert not offenders, (
        f"以下文件仍用 markdown 当标题（页面用 st.title / 分区用 st.subheader）: {offenders}"
    )


def test_price_range_accepts_embedded_param():
    """价格区间计算器也作为工作区标签页呈现，必须支持 embedded。"""
    fn = _func_def(_parse("meteora_scanner.py"), "render_price_range")
    assert fn is not None, "meteora_scanner.py 缺少 render_price_range()"
    assert "embedded" in [a.arg for a in fn.args.args], (
        "render_price_range() 缺少 embedded 参数——嵌入时会与外层标签名重复标题"
    )


def test_hub_passes_embedded_to_every_tab():
    """工作区挂载的每个子页面都必须显式传 embedded=True。"""
    src = (PAGES / "onchain_hub.py").read_text(encoding="utf-8")
    calls = re.findall(r"render_\w+\((.*?)\)", src)
    assert calls, "onchain_hub.py 未挂载任何子页面"
    missing = [c for c in calls if "embedded=True" not in c]
    assert not missing, f"以下调用未传 embedded=True，会渲染重复标题: {missing}"


def test_meteora_tab_labels_match_descriptions():
    """二级标签名必须与其说明一一对应，否则 render_page 里取描述会 KeyError 崩页。"""
    tree = _parse("meteora_scanner.py")
    tables = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in ("PAGES", "PAGE_DESCRIPTIONS"):
                tables[node.targets[0].id] = {
                    k.value for k in node.value.keys if isinstance(k, ast.Constant)
                }
    assert tables.get("PAGES"), "未找到 PAGES 映射"
    assert tables["PAGES"] == tables.get("PAGE_DESCRIPTIONS"), (
        "PAGES 与 PAGE_DESCRIPTIONS 的标签名不一致："
        f"{tables['PAGES'] ^ tables.get('PAGE_DESCRIPTIONS', set())}"
    )


def test_embedded_pages_render_no_duplicate_heading():
    """嵌入工作区时（embedded=True）不得再输出 st.title/st.header。

    标题由外层标签页提供，子页面再输出一次就会出现同屏双标题。
    """
    pytest.importorskip("streamlit.testing.v1")
    import sys
    import warnings
    from streamlit.testing.v1 import AppTest

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    warnings.filterwarnings("ignore")

    def make_call(filename, func):
        def _run():
            module = __import__(f"pages.{filename[:-3]}", fromlist=[func])
            getattr(module, func)(embedded=True)

        return _run

    offenders = []
    for filename, func in EMBEDDED_PAGES.items():
        at = AppTest.from_function(make_call(filename, func), default_timeout=90).run()
        headings = [t.value for t in at.title] + [h.value for h in at.header]
        if headings:
            offenders.append(f"{filename}: {headings}")
    assert not offenders, (
        f"以下页面嵌入时仍渲染了大标题（会与标签名重复）: {offenders}"
    )

    # 价格区间计算器同样是工作区标签页
    at = AppTest.from_function(make_call("meteora_scanner.py", "render_price_range"),
                               default_timeout=90).run()
    headings = [t.value for t in at.title] + [h.value for h in at.header]
    assert not headings, f"render_price_range 嵌入时仍渲染了大标题: {headings}"


def test_base_chain_supported_by_token_module():
    """链上代币模块必须支持 Base 链：链选项、标签与两个行情映射都要有 base。"""
    tree = ast.parse((INTERFACES / "asset_config.py").read_text(encoding="utf-8"))
    consts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in ("TOKEN_CHAINS", "CHAIN_LABELS"):
                consts[node.targets[0].id] = node.value
    chains = [e.value for e in consts["TOKEN_CHAINS"].elts]
    assert "base" in chains, "TOKEN_CHAINS 缺少 base 链"
    labels = {k.value for k in consts["CHAIN_LABELS"].keys}
    assert "base" in labels, "CHAIN_LABELS 缺少 base 的中文标签"

    from app.infrastructure.market_data.chains import (
        _to_dexscreener_chain, _to_gecko_network,
    )
    assert _to_dexscreener_chain("base") == "base"
    assert _to_gecko_network("base") == "base"


def test_onchain_token_table_shows_chain_column():
    """链上代币是唯一跨多链的类别，表格必须显示所属链，否则分不清同名跨链代币。"""
    src = (PAGES / "st_onchain_token.py").read_text(encoding="utf-8")
    assert "所属链" in src, "链上代币表格缺少「所属链」列（多链标的无法区分）"
    assert "CHAIN_LABELS" in src, "所属链的显示标签必须取自共享的 CHAIN_LABELS"
    # 表格组件也要把该列设为只读
    grid_src = (INTERFACES / "asset_grid.py").read_text(encoding="utf-8")
    assert 'configure_column("所属链"' in grid_src, "asset_grid 未配置「所属链」列为只读"


def test_no_circled_number_tab_labels():
    """一级标签用 emoji 前缀，二级标签曾混用 ①②③ 编号，风格不统一。"""
    pattern = re.compile(r"[①②③④⑤⑥⑦⑧⑨⑩]")
    offenders = []
    for path in sorted(PAGES.glob("*.py")):
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(path.name)
    assert not offenders, f"以下页面仍用圆圈数字当标签编号（应与 emoji 前缀统一）: {offenders}"


def test_pool_simulator_guards_none_price():
    """拉取失败但无 error 时 price 可能为 None，必须在进入模拟前拦截。"""
    src = (PAGES / "st_pool_simulator.py").read_text(encoding="utf-8")
    assert 'data["price"] is None' in src, "st_pool_simulator 缺少 price=None 的崩页保护"
    # 死逻辑清理：sim_submit 曾经永远为 False
    assert "sim_submit" not in src, "st_pool_simulator 仍残留无效的 sim_submit 分支"

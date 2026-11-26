"""第二轮检查修正的回归测试。"""

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_legacy_mode_does_not_depend_on_bak_file():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "main.py.bak" not in src.split("def run_legacy", 1)[1].split('"""', 2)[2]
    assert "runpy" not in src
    assert "run_legacy(once=args.once)" in src


def test_no_module_hardcodes_global_webhook():
    offenders = []
    for path in list((ROOT / "app").rglob("*.py")) + [ROOT / "main.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            func = getattr(node, "func", None)
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name == "get_module_webhook":     # 回落参数本来就是全局 Webhook
                continue
            if isinstance(node, ast.Call) and any(
                    isinstance(a, ast.Name) and a.id == "FEISHU_WEBHOOK" for a in node.args):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == [], offenders


def test_close_lane_runs_after_baostock_update():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "dtime(18, 0)" in src and "dtime(18, 10)" in src

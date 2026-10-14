import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _session_subscript_keys(path):
    tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"))
    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            target = node.value
            if (isinstance(target, ast.Attribute)
                    and target.attr == "session_state"):
                arg = node.slice
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    keys.add(arg.value)
    return keys


def test_dashboard_logout_uses_canonical_session_key():
    assert _session_subscript_keys("pages/dashboard.py") <= {"logged_in"}


def test_price_alert_ddl_single_sourced():
    page_src = (REPO_ROOT / "pages" / "st_monitor_price.py").read_text(
        encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS price_alert" not in page_src
    assert "from app.application.monitors.price import" in page_src
    assert "CREATE_TABLE_SQL" in page_src

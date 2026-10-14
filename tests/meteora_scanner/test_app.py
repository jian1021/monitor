"""应用整体渲染测试：确保 9 个页面均可无异常运行。

使用 Streamlit 官方测试框架 AppTest 在无头模式下执行 app.py。
测试使用临时数据库，避免污染真实数据。
"""

import os
import tempfile
import unittest

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "meteora_apptest.db"))

try:
    from streamlit.testing.v1 import AppTest

    HAS_APPTEST = True
except Exception:  # noqa: BLE001
    HAS_APPTEST = False

APP_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "pages", "meteora_scanner.py")


@unittest.skipUnless(HAS_APPTEST, "streamlit.testing.v1 不可用")
class TestAppRendering(unittest.TestCase):
    def test_all_pages_render_without_exception(self):
        at = AppTest.from_file(APP_PATH, default_timeout=90)
        at.run()
        self.assertFalse(at.exception, f"首页异常: {at.exception}")
        labels = [tab.label for tab in at.tabs]
        self.assertEqual(len(labels), 9)
        self.assertEqual(labels, [
            "① 全池扫描", "② 筛选条件", "③ 筛选结果", "④ 重点池监控",
            "⑤ 历史数据", "⑥ 飞书报警设置", "⑦ API 状态与错误日志",
            "⑧ 使用说明", "⑨ 价格区间计算",
        ])


if __name__ == "__main__":
    unittest.main()

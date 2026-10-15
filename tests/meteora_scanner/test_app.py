"""Meteora 扫描器页面渲染测试：确保 5 个子标签均可无异常运行。

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
        self.assertEqual(len(labels), 5)
        # 标签前缀与「全链扫描与链上代币」工作区的一级标签统一用 emoji，不用圆圈数字
        self.assertEqual(labels, [
            "▶️ 全池扫描", "⚙️ 筛选条件", "📋 筛选结果", "🔌 API 状态与错误日志",
            "📖 使用说明",
        ])
        # 标签名即标题：子页面不得再输出同名大标题（否则同屏双标题）
        self.assertEqual([t.value for t in at.title],
                         ["🌊 Meteora 全池扫描与筛选器"])
        for heading in at.header:
            self.assertNotIn(heading.value, labels,
                             f"子页面重复渲染了与标签名相同的标题: {heading.value}")


if __name__ == "__main__":
    unittest.main()

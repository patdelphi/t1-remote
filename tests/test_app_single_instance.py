"""程序说明：验证主前台重复启动时会唤起已有窗口。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.t1_app import MAIN_WINDOW_TITLE, run_app


class AppSingleInstanceTests(unittest.TestCase):
    def test_duplicate_start_activates_existing_window(self) -> None:
        guard = type("Guard", (), {"acquire": lambda self: False})()
        with patch("tools.t1_app.SingleInstanceGuard", return_value=guard), patch(
            "tools.t1_app.activate_window_by_title", return_value=True
        ) as activate:
            result = run_app()

        self.assertEqual(result, 0)
        activate.assert_called_once_with(MAIN_WINDOW_TITLE)


if __name__ == "__main__":
    unittest.main()

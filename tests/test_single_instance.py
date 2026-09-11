"""程序说明：验证 T1 Windows 进程单实例保护。"""

from __future__ import annotations

import os
import sys
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from t1remote.windows.single_instance import SingleInstanceGuard, activate_window_by_title


class SingleInstanceTests(unittest.TestCase):
    def test_only_one_guard_can_hold_the_same_name(self) -> None:
        name = f"T1RemoteTest-{os.getpid()}-{uuid.uuid4()}"
        first = SingleInstanceGuard(name)
        second = SingleInstanceGuard(name)
        try:
            self.assertTrue(first.acquire())
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
        finally:
            first.release()
            second.release()

    def test_empty_name_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            SingleInstanceGuard(" ")

    def test_existing_hidden_window_is_restored_and_activated(self) -> None:
        calls: list[tuple[object, ...]] = []

        def enum_windows(callback, extra) -> None:
            callback(101, extra)

        fake_gui = SimpleNamespace(
            EnumWindows=enum_windows,
            GetWindowText=lambda hwnd: "T1 Remote Mapping",
            IsWindowVisible=lambda hwnd: False,
            ShowWindow=lambda hwnd, mode: calls.append(("show", hwnd, mode)),
            BringWindowToTop=lambda hwnd: calls.append(("top", hwnd)),
            SetForegroundWindow=lambda hwnd: calls.append(("foreground", hwnd)),
        )
        fake_con = SimpleNamespace(SW_SHOW=5, SW_RESTORE=9)
        with patch.dict(sys.modules, {"win32gui": fake_gui, "win32con": fake_con}), patch(
            "t1remote.windows.single_instance.os.name", "nt"
        ):
            self.assertTrue(
                activate_window_by_title(
                    "T1 Remote Mapping",
                    timeout_seconds=0,
                )
            )

        self.assertEqual(calls, [("show", 101, 5), ("top", 101), ("foreground", 101)])


if __name__ == "__main__":
    unittest.main()

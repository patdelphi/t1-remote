"""程序说明：验证 T1 Windows 进程单实例保护。"""

from __future__ import annotations

import os
import unittest
import uuid

from t1remote.windows.single_instance import SingleInstanceGuard


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


if __name__ == "__main__":
    unittest.main()

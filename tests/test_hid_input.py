"""程序说明：验证 T1 HID 直读监听器的设备路径筛选和 Collection 规则。"""

import unittest
from pathlib import Path
from types import SimpleNamespace
import threading
from unittest.mock import Mock

from t1remote.windows.hid_input import (
    HidInputListener,
    filter_target_hid_paths,
    normalize_target_collections,
)


class HidInputPathTests(unittest.TestCase):
    """覆盖不依赖 Windows API 的 HID 路径逻辑。"""

    def test_filters_t1_collection_paths_and_deduplicates(self) -> None:
        col02 = (
            r"\\?\HID#{00001812-0000-1000-8000-00805f9b34fb}_Dev_VID&01620A_PID&0407_REV&0000_"
            r"f7426d57fba1&Col02#b&39f0e088&0&0001#{4d1e55b2-f16f-11cf-88cb-001111000030}"
        )
        col03 = col02.replace("Col02", "Col03").replace("0001}", "0002}")
        col01 = col02.replace("Col02", "Col01")
        other = col02.replace("01620A", "004C00")

        self.assertEqual(
            filter_target_hid_paths(
                [col02, col03, col02, col01, other],
                ("COL02", "COL03"),
            ),
            [("COL02", col02), ("COL03", col03)],
        )

    def test_normalizes_and_rejects_invalid_collection_names(self) -> None:
        self.assertEqual(
            normalize_target_collections(("col03", "COL02", "COL03")),
            ("COL03", "COL02"),
        )
        with self.assertRaises(ValueError):
            normalize_target_collections(("COL2",))
        with self.assertRaises(ValueError):
            normalize_target_collections(("COL00",))
        with self.assertRaises(ValueError):
            normalize_target_collections(("COL32",))

    def test_stop_waits_for_reader_before_closing_handles(self) -> None:
        """取消异步读后必须等待读取线程退出，再释放句柄。"""

        lifecycle: list[str] = []

        class _FakeThread:
            def join(self, *args, **kwargs) -> None:
                lifecycle.append("join")
                self.args = args
                self.kwargs = kwargs

        reader = SimpleNamespace(thread=_FakeThread())
        listener = object.__new__(HidInputListener)
        listener._stop_requested = threading.Event()
        listener._lock = threading.Lock()
        listener._lifecycle_lock = threading.RLock()
        listener._readers = [reader]
        listener._cancel_reader = lambda _reader: lifecycle.append("cancel")
        listener._close_reader = lambda _reader: lifecycle.append("close")

        listener.stop()

        self.assertEqual(lifecycle, ["cancel", "join", "close"])
        self.assertEqual(reader.thread.args, ())
        self.assertEqual(reader.thread.kwargs, {})

    def test_cancel_reader_does_not_manually_signal_overlapped_event(self) -> None:
        """完成事件应由 Windows I/O 设置，避免提前唤醒未完成请求。"""

        kernel32 = SimpleNamespace(
            CancelIoEx=Mock(return_value=True),
            SetEvent=Mock(),
        )
        listener = object.__new__(HidInputListener)
        listener._kernel32 = kernel32

        listener._cancel_reader(SimpleNamespace(handle=7, event=8))

        kernel32.CancelIoEx.assert_called_once()
        kernel32.SetEvent.assert_not_called()

    def test_listener_serializes_start_and_stop_lifecycle(self) -> None:
        """独立使用监听器时，启动和停止也必须共享同一生命周期锁。"""

        source = (
            Path(__file__).resolve().parents[1]
            / "t1remote"
            / "windows"
            / "hid_input.py"
        ).read_text(encoding="utf-8")

        self.assertIn("self._lifecycle_lock = threading.RLock()", source)
        self.assertGreaterEqual(source.count("with self._lifecycle_lock:"), 2)


if __name__ == "__main__":
    unittest.main()

"""程序说明：验证 T1 设备级拦截桥接协议，不依赖真实驱动或 Windows 硬件。"""

from __future__ import annotations

import ctypes
import unittest

from t1remote.windows.driver_bridge import (
    ERROR_NO_MORE_ITEMS,
    BridgeCapabilities,
    BridgeError,
    BridgeProtocolError,
    BridgeStatus,
    BridgeStats,
    BridgeUnavailable,
    build_default_interception_policy,
    format_bridge_diagnostics,
    HidFieldRule,
    HidUsage,
    InterceptionPolicy,
    NativeBridgeCapabilities,
    NativeBridgeEvent,
    NativeBridgeStatus,
    NativePreparsedData,
    NativeReportDescriptor,
    NativeBridgeStats,
    T1BridgeClient,
)


class _FakeFunction:
    """模拟 ctypes 导出函数，记录调用并返回预设结果。"""

    def __init__(self, result: int = 0) -> None:
        self.result = result
        self.calls: list[tuple[object, ...]] = []

    def __call__(self, *args: object) -> int:
        self.calls.append(args)
        return self.result


class _FakeOpenFunction(_FakeFunction):
    """为打开函数写入一个非空的模拟句柄。"""

    def __call__(self, *args: object) -> int:
        result = super().__call__(*args)
        ctypes.cast(args[1], ctypes.POINTER(ctypes.c_void_p)).contents.value = 1
        return result


class _ScriptedReadEventFunction(_FakeFunction):
    """按脚本输出事件序列，用尽后返回 ERROR_NO_MORE_ITEMS。

    每个条目是 (usage_page, usage, collection, timestamp_100ns)。
    """

    def __init__(self, items: list[tuple[int, int, int, int]]) -> None:
        super().__init__(0)
        self.items = list(items)
        self._sent = 0

    def __call__(self, *args: object) -> int:
        if not self.items:
            return ERROR_NO_MORE_ITEMS
        usage_page, usage, collection, timestamp = self.items.pop(0)
        result = super().__call__(*args)
        event = ctypes.cast(args[1], ctypes.POINTER(NativeBridgeEvent)).contents
        event.size = ctypes.sizeof(NativeBridgeEvent)
        event.abi_version = 2
        # sequence 必须严格递增：真实设备连发帧的序列相邻（差 1）。
        event.sequence = 1000 + self._sent
        self._sent += 1
        event.usage_page = usage_page
        event.usage = usage
        event.collection = collection
        event.report_length = 3
        event.report[0] = 0x02
        event.report[1] = usage & 0xFF
        event.report[2] = 0x00
        event.timestamp_100ns = timestamp
        return result


class _FakeReadEventFunction(_FakeFunction):
    """向输出结构写入一条模拟的驱动拦截事件。"""

    def __call__(self, *args: object) -> int:
        result = super().__call__(*args)
        event = ctypes.cast(args[1], ctypes.POINTER(NativeBridgeEvent)).contents
        event.size = ctypes.sizeof(NativeBridgeEvent)
        event.abi_version = 2
        event.sequence = 7
        event.usage_page = 0x0C
        event.usage = 0x223
        event.collection = 2
        event.report_length = 3
        event.report[0] = 0x02
        event.report[1] = 0x23
        event.report[2] = 0x02
        return result


class _FakeStatusFunction(_FakeFunction):
    """向状态结构写入完整的固定 ABI 头。"""

    def __call__(self, *args: object) -> int:
        result = super().__call__(*args)
        output = ctypes.cast(args[1], ctypes.POINTER(NativeBridgeStatus)).contents
        output.size = ctypes.sizeof(NativeBridgeStatus)
        output.abi_version = 2
        output.state = 1
        return result


class BridgeDiagnosticsTests(unittest.TestCase):
    """验证采集窗口使用的驱动诊断文本包含关键计数。"""

    def test_format_bridge_diagnostics_decodes_collection_mask(self) -> None:
        status = BridgeStatus(
            state="running",
            last_error=0,
            dropped_reports=0,
            abi_version=2,
            attached_collections=12,
        )
        stats = BridgeStats(
            received_reports=5,
            blocked_reports=4,
            queued_events=4,
            dropped_events=0,
            buffer_errors=0,
            queue_depth=1,
            abi_version=2,
            forwarded_reports=1,
            completion_errors=0,
        )

        text = format_bridge_diagnostics(status, stats)

        self.assertIn("COL02,COL03", text)
        self.assertIn("收到:5", text)
        self.assertIn("拦截:4", text)
        self.assertIn("队列:1", text)


class _FakeCapabilitiesFunction(_FakeFunction):
    """向能力结构写入一组固定的驱动能力。"""

    def __call__(self, *args: object) -> int:
        result = super().__call__(*args)
        output = ctypes.cast(
            args[1], ctypes.POINTER(NativeBridgeCapabilities)
        ).contents
        output.size = ctypes.sizeof(NativeBridgeCapabilities)
        output.abi_version = 2
        output.flags = 0x0000000F
        output.max_blocked_usages = 32
        output.max_target_collections = 8
        output.max_report_bytes = 64
        output.event_queue_capacity = 64
        return result


class _FakeStatsFunction(_FakeFunction):
    """向统计结构写入一组固定的驱动计数。"""

    def __call__(self, *args: object) -> int:
        result = super().__call__(*args)
        output = ctypes.cast(args[1], ctypes.POINTER(NativeBridgeStats)).contents
        output.size = ctypes.sizeof(NativeBridgeStats)
        output.abi_version = 2
        output.received_reports = 10
        output.blocked_reports = 3
        output.queued_events = 3
        output.dropped_events = 1
        output.buffer_errors = 0
        output.queue_depth = 2
        return result


class _FakeReportDescriptorFunction(_FakeFunction):
    """向描述符结构写入一段模拟的 HID Report Descriptor。"""

    def __call__(self, *args: object) -> int:
        result = super().__call__(*args)
        output = ctypes.cast(
            args[1], ctypes.POINTER(NativeReportDescriptor)
        ).contents
        output.size = ctypes.sizeof(NativeReportDescriptor)
        output.abi_version = 2
        output.collection = 2
        descriptor = bytes.fromhex(
            "05 0C 09 01 A1 01 85 02 75 01 95 01 09 E9 81 02 C0"
        )
        output.descriptor_length = len(descriptor)
        output.descriptor[: len(descriptor)] = descriptor
        return result


class _FakePreparsedDataFunction(_FakeFunction):
    """向 preparsed data 结构写入一段模拟的 HID 能力数据。"""

    def __call__(self, *args: object) -> int:
        result = super().__call__(*args)
        output = ctypes.cast(
            args[1], ctypes.POINTER(NativePreparsedData)
        ).contents
        output.size = ctypes.sizeof(NativePreparsedData)
        output.abi_version = 2
        output.collection = 2
        data = b"preparsed-fixture"
        output.data_length = len(data)
        output.data[: len(data)] = data
        return result


class _FakeBridgeLibrary:
    """模拟 t1bridge.dll 的最小 ABI。"""

    def __init__(self) -> None:
        self.abi = _FakeFunction(2)
        self.open = _FakeOpenFunction(0)
        self.set_policy = _FakeFunction(0)
        self.start = _FakeFunction(0)
        self.heartbeat = _FakeFunction(0)
        self.stop = _FakeFunction(0)
        self.close = _FakeFunction(0)
        self.status = _FakeStatusFunction(0)
        self.read_event = _FakeReadEventFunction(0)
        self.capabilities = _FakeCapabilitiesFunction(0)
        self.stats = _FakeStatsFunction(0)
        self.flush_events = _FakeFunction(0)
        self.report_descriptor = _FakeReportDescriptorFunction(0)
        self.preparsed_data = _FakePreparsedDataFunction(0)
        self.T1Bridge_GetAbiVersion = self.abi
        self.T1Bridge_Open = self.open
        self.T1Bridge_SetPolicy = self.set_policy
        self.T1Bridge_Start = self.start
        self.T1Bridge_Heartbeat = self.heartbeat
        self.T1Bridge_Stop = self.stop
        self.T1Bridge_Close = self.close
        self.T1Bridge_GetStatus = self.status
        self.T1Bridge_ReadEvent = self.read_event
        self.T1Bridge_GetCapabilities = self.capabilities
        self.T1Bridge_GetStats = self.stats
        self.T1Bridge_FlushEvents = self.flush_events
        self.T1Bridge_GetReportDescriptor = self.report_descriptor
        self.T1Bridge_GetPreparsedData = self.preparsed_data


class DriverBridgeTests(unittest.TestCase):
    """覆盖协议校验、生命周期和无驱动时的安全失败行为。"""

    def test_policy_serializes_only_t1_and_unique_usages(self) -> None:
        policy = InterceptionPolicy(
            blocked_usages=(HidUsage(0x0C, 0x223), HidUsage(0x0C, 0x0E9)),
            target_collections=("COL02",),
        )

        native = policy.to_native()

        self.assertEqual(native.vid, 0x620A)
        self.assertEqual(native.pid, 0x0407)
        self.assertEqual(native.usage_count, 2)
        self.assertEqual(native.target_collection_count, 1)
        self.assertEqual(native.target_collections[0], 2)
        self.assertEqual(native.usages[0].usage_page, 0x0C)
        self.assertEqual(native.usages[0].usage, 0x223)
        self.assertEqual(native.usages[0].collection, 2)

    def test_policy_serializes_lease_and_descriptor_rule_settings(self) -> None:
        policy = InterceptionPolicy(
            blocked_usages=(HidUsage(0x0C, 0x223),),
            target_collections=("COL02",),
            lease_required=True,
            lease_timeout_ms=5000,
        )

        native = policy.to_native()

        self.assertEqual(native.abi_version, 2)
        self.assertEqual(native.lease_timeout_ms, 5000)
        self.assertEqual(native.field_rule_count, 0)
        self.assertTrue(native.flags & 0x0008)

    def test_policy_serializes_unmapped_report_capture(self) -> None:
        policy = InterceptionPolicy(
            target_collections=("COL02", "COL03"),
            drop_unmapped=True,
        )

        native = policy.to_native()

        self.assertEqual(native.usage_count, 0)
        self.assertEqual(native.target_collection_count, 2)
        self.assertTrue(native.flags & 0x0002)

    def test_policy_serializes_descriptor_field_rule(self) -> None:
        policy = InterceptionPolicy(
            target_collections=("COL02",),
            field_rules=(
                HidFieldRule(
                    0x0C,
                    "COL02",
                    0x223,
                    byte_offset=1,
                    byte_length=2,
                    mapped_usage=0x224,
                ),
            ),
        )

        native = policy.to_native()

        self.assertEqual(native.field_rule_count, 1)
        self.assertEqual(native.field_rules[0].collection, 2)
        self.assertEqual(native.field_rules[0].byte_offset, 1)
        self.assertEqual(native.field_rules[0].mapped_usage, 0x224)

    def test_policy_rejects_invalid_lease_timeout(self) -> None:
        with self.assertRaises(ValueError):
            InterceptionPolicy(lease_required=True, lease_timeout_ms=99)

    def test_policy_rejects_empty_or_zero_collections(self) -> None:
        with self.assertRaises(ValueError):
            InterceptionPolicy(target_collections=())
        with self.assertRaises(ValueError):
            InterceptionPolicy(target_collections=("COL00",))

    def test_policy_rejects_zero_collection_field_rules(self) -> None:
        with self.assertRaises(ValueError):
            InterceptionPolicy(
                target_collections=("COL02",),
                field_rules=(
                    HidFieldRule(
                        0x0C,
                        "COL00",
                        0x223,
                        byte_offset=1,
                        byte_length=1,
                    ),
                ),
            )

    def test_client_rejects_out_of_range_descriptor_collection(self) -> None:
        """用户态查询接口不能把 COL00 或超范围编号传给驱动。"""

        library = _FakeBridgeLibrary()
        client = T1BridgeClient(
            library_loader=lambda _path: library,
            is_windows=True,
        )
        client.open(InterceptionPolicy(target_collections=("COL02",)))

        with self.assertRaises(ValueError):
            client.get_report_descriptor("COL00")
        with self.assertRaises(ValueError):
            client.get_preparsed_data("COL32")
        client.close()

    def test_client_rejects_invalid_status_abi(self) -> None:
        """驱动返回的状态头不完整时，桥接层必须拒绝继续解析。"""

        library = _FakeBridgeLibrary()
        library.status = _FakeFunction(0)
        library.T1Bridge_GetStatus = library.status
        client = T1BridgeClient(
            library_loader=lambda _path: library,
            is_windows=True,
        )
        client.open(InterceptionPolicy(target_collections=("COL02",)))

        with self.assertRaises(BridgeProtocolError):
            client.status()
        client.close()

    def test_policy_rejects_conflicting_field_rule_flags(self) -> None:
        with self.assertRaises(ValueError):
            InterceptionPolicy(
                target_collections=("COL02",),
                field_rules=(
                    HidFieldRule(
                        0x0C,
                        "COL02",
                        0x223,
                        byte_offset=1,
                        byte_length=1,
                        remap=True,
                        drop=True,
                    ),
                ),
            )

    def test_policy_serializes_consumer_usage_remapping(self) -> None:
        policy = InterceptionPolicy(
            blocked_usages=(
                HidUsage(0x0C, 0x223, "COL02", mapped_usage=0x224),
            ),
            target_collections=("COL02",),
        )

        native = policy.to_native()

        self.assertEqual(native.usages[0].usage, 0x223)
        self.assertEqual(native.usages[0].mapped_usage, 0x224)
        self.assertEqual(native.flags & 0x0004, 0x0004)

    def test_policy_rejects_duplicate_usage(self) -> None:
        with self.assertRaises(ValueError):
            InterceptionPolicy(
                blocked_usages=(HidUsage(0x0C, 0x0E9), HidUsage(0x0C, 0x0E9))
            )

    def test_policy_rejects_ambiguous_source_remapping(self) -> None:
        with self.assertRaises(ValueError):
            InterceptionPolicy(
                blocked_usages=(
                    HidUsage(0x0C, 0x223, "COL02", mapped_usage=0x224),
                    HidUsage(0x0C, 0x223, "COL02", mapped_usage=0x221),
                ),
                target_collections=("COL02",),
            )

    def test_policy_rejects_wildcard_and_collection_specific_overlap(self) -> None:
        with self.assertRaises(ValueError):
            InterceptionPolicy(
                blocked_usages=(
                    HidUsage(0x0C, 0x223),
                    HidUsage(0x0C, 0x223, "COL02"),
                ),
                target_collections=("COL02", "COL03"),
            )

    def test_client_calls_native_lifecycle(self) -> None:
        library = _FakeBridgeLibrary()
        client = T1BridgeClient(
            library_loader=lambda _path: library,
            is_windows=True,
        )
        policy = InterceptionPolicy(
            blocked_usages=(HidUsage(0x0C, 0x223),),
            target_collections=("COL02",),
        )

        client.open(policy)
        client.start()
        client.set_policy(policy)
        client.heartbeat()
        status = client.status()
        client.stop()
        client.close()

        self.assertIsInstance(status, BridgeStatus)
        self.assertEqual(len(library.open.calls), 1)
        self.assertEqual(len(library.start.calls), 1)
        self.assertEqual(len(library.heartbeat.calls), 1)
        self.assertEqual(len(library.set_policy.calls), 1)
        self.assertEqual(len(library.status.calls), 1)
        self.assertEqual(len(library.stop.calls), 1)
        self.assertEqual(len(library.close.calls), 1)

    def test_client_reads_report_descriptor_through_driver_bridge(self) -> None:
        """Report Descriptor 必须通过驱动桥接读取，不能从用户态 HID 句柄猜测。"""

        library = _FakeBridgeLibrary()
        client = T1BridgeClient(
            library_loader=lambda _path: library,
            is_windows=True,
        )

        client.open(InterceptionPolicy(target_collections=("COL02",)))
        descriptor = client.get_report_descriptor("COL02")

        self.assertEqual(
            descriptor,
            bytes.fromhex(
                "05 0c 09 01 a1 01 85 02 75 01 95 01 09 e9 81 02 c0"
            ),
        )
        self.assertEqual(len(library.report_descriptor.calls), 1)
        client.close()

    def test_client_reads_preparsed_data_through_driver_bridge(self) -> None:
        """HID Collection 能力数据使用独立 API，不能与原始 Descriptor 混淆。"""

        library = _FakeBridgeLibrary()
        client = T1BridgeClient(
            library_loader=lambda _path: library,
            is_windows=True,
        )

        client.open(InterceptionPolicy(target_collections=("COL02",)))

        self.assertEqual(client.get_preparsed_data("COL02"), b"preparsed-fixture")
        self.assertEqual(len(library.preparsed_data.calls), 1)
        client.close()

    def test_access_denied_reports_admin_and_owner_hint(self) -> None:
        """控制设备拒绝写权限时给出管理员/占用提示，不能只回裸错误码。"""

        library = _FakeBridgeLibrary()
        library.open = _FakeOpenFunction(5)
        library.T1Bridge_Open = library.open
        client = T1BridgeClient(
            library_loader=lambda _path: library,
            is_windows=True,
        )

        with self.assertRaises(BridgeError) as context:
            client.open(InterceptionPolicy(target_collections=("COL02",)))

        self.assertIn("管理员", str(context.exception))
        self.assertFalse(client.is_open)

    def test_unavailable_bridge_fails_closed(self) -> None:
        client = T1BridgeClient(
            library_loader=lambda _path: (_ for _ in ()).throw(OSError("missing")),
            is_windows=True,
        )

        with self.assertRaises(BridgeUnavailable):
            client.open(InterceptionPolicy())

    def test_loader_finds_x64_build_output_by_default(self) -> None:
        library = _FakeBridgeLibrary()
        attempted: list[str] = []

        def load_library(path: str) -> object:
            attempted.append(path)
            if "native\\t1bridge\\x64\\Release\\t1bridge.dll" in path:
                return library
            raise OSError("not this candidate")

        client = T1BridgeClient(
            library_loader=load_library,
            is_windows=True,
        )

        client.open(InterceptionPolicy())

        self.assertTrue(attempted)
        self.assertIn("native\\t1bridge\\x64\\Release\\t1bridge.dll", attempted[-1])
        client.close()

    def test_loader_prefers_current_cmake_release_output(self) -> None:
        """运行时优先加载包含最新 ABI 导出的 CMake Release 产物。"""

        library = _FakeBridgeLibrary()
        attempted: list[str] = []

        def load_library(path: str) -> object:
            attempted.append(path)
            if "native\\t1bridge\\build-vs2022\\Release\\t1bridge.dll" in path:
                return library
            raise OSError("not this candidate")

        client = T1BridgeClient(
            library_loader=load_library,
            is_windows=True,
        )

        client.open(InterceptionPolicy())

        self.assertEqual(len(attempted), 1)
        self.assertIn(
            "native\\t1bridge\\build-vs2022\\Release\\t1bridge.dll",
            attempted[0],
        )
        client.close()

    def test_native_status_layout_is_pointer_safe(self) -> None:
        status = BridgeStatus.from_native(
            type(
                "NativeStatus",
                (),
                {
                    "size": ctypes.sizeof(ctypes.c_uint32),
                    "abi_version": 2,
                    "state": 2,
                    "last_error": 0,
                    "dropped_reports": 3,
                },
            )()
        )

        self.assertEqual(status.state, "running")
        self.assertEqual(status.dropped_reports, 3)

    def test_native_event_layout_is_fixed_size(self) -> None:
        self.assertEqual(ctypes.sizeof(NativeBridgeEvent), 96)

    def test_client_read_event_dedupes_device_frame_repeats(self) -> None:
        library = _FakeBridgeLibrary()
        # T1 一次按压连发 3 个相同帧（同 usage 同时间戳），随后 3 个释放帧；
        # 超过 100ms 窗口的再次按压必须保留（长按重复）。
        library.read_event = library.T1Bridge_ReadEvent = _ScriptedReadEventFunction(
            [
                (0x0C, 0xE9, 2, 1_000),        # 按下帧 1（保留）
                (0x0C, 0xE9, 2, 1_000),        # 按下帧 2（合并）
                (0x0C, 0xE9, 2, 100_000),      # 按下帧 3（仍在 100ms 内）
                (0x0C, 0x0000, 2, 100_000),    # 抬起帧 1（保留，usage 不同）
                (0x0C, 0x0000, 2, 100_000),    # 抬起帧 2（合并）
                (0x0C, 0xE9, 2, 2_000_000),    # 200ms 后再次按压（保留）
            ]
        )
        client = T1BridgeClient(
            library_loader=lambda _path: library,
            is_windows=True,
        )
        client.open(InterceptionPolicy())

        first = client.read_event()
        second = client.read_event()
        third = client.read_event()

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNotNone(third)
        assert first is not None and second is not None and third is not None
        self.assertEqual((first.usage, first.timestamp_100ns), (0xE9, 1_000))
        self.assertEqual((second.usage, second.timestamp_100ns), (0x0000, 100_000))
        self.assertEqual((third.usage, third.timestamp_100ns), (0xE9, 2_000_000))
        self.assertIsNone(client.read_event())
        client.close()

    def test_client_reads_original_blocked_event(self) -> None:
        library = _FakeBridgeLibrary()
        client = T1BridgeClient(
            library_loader=lambda _path: library,
            is_windows=True,
        )
        client.open(
            InterceptionPolicy(
                blocked_usages=(HidUsage(0x0C, 0x223, "COL02"),),
                target_collections=("COL02",),
            )
        )

        event = client.read_event()

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.sequence, 7)
        self.assertEqual(event.usage_page, 0x0C)
        self.assertEqual(event.usage, 0x223)
        self.assertEqual(event.collection, "COL02")
        self.assertEqual(event.report, b"\x02\x23\x02")
        client.close()

    def test_client_reads_runtime_capabilities_and_stats(self) -> None:
        library = _FakeBridgeLibrary()
        client = T1BridgeClient(
            library_loader=lambda _path: library,
            is_windows=True,
        )
        client.open(InterceptionPolicy())

        capabilities = client.capabilities()
        stats = client.stats()
        client.flush_events()

        self.assertIsInstance(capabilities, BridgeCapabilities)
        self.assertEqual(capabilities.event_queue_capacity, 64)
        self.assertEqual(capabilities.max_report_bytes, 64)
        self.assertIsInstance(stats, BridgeStats)
        self.assertEqual(stats.received_reports, 10)
        self.assertEqual(stats.blocked_reports, 3)
        self.assertEqual(stats.queue_depth, 2)
        self.assertEqual(len(library.capabilities.calls), 1)
        self.assertEqual(len(library.stats.calls), 1)
        self.assertEqual(len(library.flush_events.calls), 1)
        client.close()

    def test_default_policy_targets_installed_filter_collections(self) -> None:
        policy = InterceptionPolicy()

        self.assertEqual(policy.target_collections, ("COL01", "COL02", "COL03"))

    def test_default_interception_policy_covers_confirmed_remote_buttons(self) -> None:
        policy = build_default_interception_policy()
        usages = {(item.usage_page, item.usage, item.collection) for item in policy.blocked_usages}

        self.assertEqual(len(usages), 8)
        self.assertIn((0x0C, 0x223, "COL02"), usages)
        self.assertIn((0x01, 0x81, "COL03"), usages)
        # 键盘集合只拦 Menu：正面 OK/方向键与背面 Return/方向键共享 HID usage
        # （0x28、0x4F-0x52），按“优先背面键盘”的取舍放行给系统，不再映射。
        self.assertIn((0x07, 0x65, "COL01"), usages)
        self.assertNotIn((0x07, 0x28, "COL01"), usages)
        self.assertNotIn((0x07, 0x4F, "COL01"), usages)
        self.assertNotIn((0x07, 0x52, "COL01"), usages)
        # 常驻拦截：默认不依赖 App 会话租约。
        self.assertFalse(policy.lease_required)
        # 方案 B：只拦确认键，键盘面其余键放行给系统（背面键盘可打字）。
        self.assertFalse(policy.drop_unmapped)

    def test_default_interception_policy_can_be_disabled_for_dry_run(self) -> None:
        policy = build_default_interception_policy(enabled=False, lease_required=False)

        self.assertFalse(policy.enabled)
        self.assertFalse(policy.lease_required)
        self.assertEqual(policy.vid, 0x620A)
        self.assertEqual(policy.pid, 0x0407)


if __name__ == "__main__":
    unittest.main()

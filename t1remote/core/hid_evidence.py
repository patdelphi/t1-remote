"""程序说明：把 HID 报告转换为可审计的类型、字段和按键时序证据。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from t1remote.core.hid_report_descriptor import (
    HidReportDescriptorError,
    HidReportField,
    parse_hid_report_descriptor,
)


HID_REPORT_CATEGORIES: tuple[str, ...] = (
    "keyboard",
    "consumer",
    "system",
    "mouse",
    "vendor",
    "unknown",
)


@dataclass(frozen=True)
class HidReportSample:
    """一条已经由 Raw Input 或驱动层提供的原始 HID 报告。"""

    collection: str
    report: bytes
    usage_page: int | None = None
    usage: int | None = None
    state: str = "unknown"
    report_id: int | None = None
    timestamp_utc: str | None = None
    timestamp_100ns: int | None = None
    sequence: int | None = None


def classify_hid_report(
    *,
    collection: str | None,
    usage_page: int | None,
    usage: int | None,
) -> str:
    """依据已提供的 Usage Page、Usage 和 Collection 分类报告。"""

    normalized_collection = (collection or "").upper()
    if usage_page is not None and 0xFF00 <= usage_page <= 0xFFFF:
        return "vendor"
    if usage_page == 0x07:
        return "keyboard"
    if usage_page == 0x0C:
        return "consumer"
    if usage_page == 0x01:
        if normalized_collection == "COL04" or usage == 0x02:
            return "mouse"
        if normalized_collection == "COL03" or usage in {
            0x80,
            0x81,
            0x82,
            0x83,
            0x84,
        }:
            return "system"

    # Collection 只有在没有更具体的 Usage Page 冲突时作为辅助证据。
    if usage_page is None:
        return {
            "COL01": "keyboard",
            "COL02": "consumer",
            "COL03": "system",
            "COL04": "mouse",
            "COL05": "vendor",
        }.get(normalized_collection, "unknown")
    return "unknown"


def _descriptor_report_ids(
    report_descriptor: bytes | None,
    report_fields: Iterable[HidReportField] | None,
) -> tuple[str, tuple[int, ...]]:
    """读取输入报告的 Report ID，并区分描述符缺失和描述符损坏。"""

    if report_descriptor is None and report_fields is None:
        return "descriptor_unavailable", ()
    try:
        fields = (
            parse_hid_report_descriptor(report_descriptor).fields
            if report_descriptor is not None
            else tuple(report_fields or ())
        )
    except (HidReportDescriptorError, TypeError, ValueError):
        return "descriptor_invalid", ()
    report_ids = sorted(
        {
            int(field.report_id)
            for field in fields
            if field.report_type == "input" and int(field.report_id) > 0
        }
    )
    return "descriptor_available", tuple(report_ids)


def _resolve_report_id(
    sample: HidReportSample,
    descriptor_report_ids: tuple[int, ...],
) -> tuple[int | None, str | None]:
    """只在描述符确认 Report ID 前缀时从原始报告提取 Report ID。"""

    if sample.report_id is not None:
        return int(sample.report_id), "explicit"
    if descriptor_report_ids and sample.report:
        report_id = int(sample.report[0])
        if report_id in descriptor_report_ids:
            return report_id, "descriptor"
    return None, None


def _normalized_state(value: str) -> str:
    """限制状态集合，避免把业务动作名称混入原始报告证据。"""

    return value if value in {"down", "up"} else "unknown"


def _duration_ms(start: HidReportSample, end: HidReportSample) -> int | None:
    """优先用驱动单调时钟计算按下到抬起的时长。"""

    if start.timestamp_100ns is not None and end.timestamp_100ns is not None:
        return max(0, round((end.timestamp_100ns - start.timestamp_100ns) / 10_000))
    if not start.timestamp_utc or not end.timestamp_utc:
        return None
    try:
        milliseconds = (
            datetime.fromisoformat(end.timestamp_utc)
            - datetime.fromisoformat(start.timestamp_utc)
        ).total_seconds() * 1000
    except ValueError:
        return None
    return max(0, round(milliseconds))


def _release_candidate(
    active: dict[tuple[str, int | None, int | None, int | None], int],
    sample: HidReportSample,
    report_id: int | None,
) -> tuple[str, int | None, int | None, int | None] | None:
    """寻找释放报告对应的最近一次按下，零 Usage 只在单一候选时配对。"""

    exact_key = (sample.collection, report_id, sample.usage_page, sample.usage)
    if exact_key in active:
        return exact_key
    candidates = [
        key
        for key in active
        if key[:3] == (sample.collection, report_id, sample.usage_page)
    ]
    if sample.usage not in (None, 0):
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda key: active[key])


def build_hid_evidence_report(
    samples: Iterable[HidReportSample],
    *,
    report_descriptor: bytes | None = None,
    report_fields: Iterable[HidReportField] | None = None,
) -> dict[str, Any]:
    """生成原始报告清单和按下/重复/抬起的配对时序报告。"""

    descriptor_status, descriptor_report_ids = _descriptor_report_ids(
        report_descriptor,
        report_fields,
    )
    events: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    active: dict[tuple[str, int | None, int | None, int | None], int] = {}
    sample_list = tuple(samples)

    for event_index, sample in enumerate(sample_list, start=1):
        state = _normalized_state(sample.state)
        report_id, report_id_source = _resolve_report_id(
            sample,
            descriptor_report_ids,
        )
        category = classify_hid_report(
            collection=sample.collection,
            usage_page=sample.usage_page,
            usage=sample.usage,
        )
        event: dict[str, Any] = {
            "event_index": event_index,
            "timestamp_utc": sample.timestamp_utc,
            "timestamp_100ns": sample.timestamp_100ns,
            "sequence": sample.sequence,
            "collection": sample.collection.upper(),
            "report_category": category,
            "report_id": report_id,
            "report_id_source": report_id_source,
            "usage_page": sample.usage_page,
            "usage": sample.usage,
            "raw_report_hex": sample.report.hex(" "),
            "state": state,
            "event_kind": "unknown",
            "action_id": None,
            "down_up_duration_ms": None,
        }
        key = (sample.collection, report_id, sample.usage_page, sample.usage)

        if state == "down":
            if key in active:
                action_id = active[key] + 1
                actions[active[key]]["repeat_count"] += 1
                actions[active[key]]["raw_event_indexes"].append(event_index)
                event["event_kind"] = "repeat"
                event["action_id"] = action_id
            else:
                action_index = len(actions)
                actions.append(
                    {
                        "action_id": action_index + 1,
                        "state": "press_only",
                        "collection": sample.collection.upper(),
                        "report_category": category,
                        "report_id": report_id,
                        "usage_page": sample.usage_page,
                        "usage": sample.usage,
                        "down_event_index": event_index,
                        "up_event_index": None,
                        "repeat_count": 0,
                        "duration_ms": None,
                        "raw_event_indexes": [event_index],
                    }
                )
                active[key] = action_index
                event["event_kind"] = "down"
                event["action_id"] = action_index + 1
        elif state == "up":
            matching_key = _release_candidate(active, sample, report_id)
            if matching_key is None:
                actions.append(
                    {
                        "action_id": len(actions) + 1,
                        "state": "release_only",
                        "collection": sample.collection.upper(),
                        "report_category": category,
                        "report_id": report_id,
                        "usage_page": sample.usage_page,
                        "usage": sample.usage,
                        "down_event_index": None,
                        "up_event_index": event_index,
                        "repeat_count": 0,
                        "duration_ms": None,
                        "raw_event_indexes": [event_index],
                    }
                )
                event["event_kind"] = "up"
                event["action_id"] = len(actions)
            else:
                action_index = active.pop(matching_key)
                action = actions[action_index]
                duration_ms = _duration_ms(
                    sample_list[action["down_event_index"] - 1],
                    sample,
                )
                action["state"] = "press_release"
                action["up_event_index"] = event_index
                action["duration_ms"] = duration_ms
                action["raw_event_indexes"].append(event_index)
                event["event_kind"] = "up"
                event["action_id"] = action_index + 1
                event["down_up_duration_ms"] = duration_ms
        events.append(event)

    return {
        "descriptor_status": descriptor_status,
        "descriptor_report_ids": list(descriptor_report_ids),
        "events": events,
        "actions": actions,
    }


__all__ = [
    "HID_REPORT_CATEGORIES",
    "HidReportSample",
    "build_hid_evidence_report",
    "classify_hid_report",
]

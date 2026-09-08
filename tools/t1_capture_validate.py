"""程序说明：离线检查 T1 遥控区域 JSON 夹具的覆盖率和物理映射表。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from t1remote.core.capture_scope import (
    CaptureEvent,
    build_capture_coverage,
    build_logical_actions,
    build_physical_mapping_table,
)
from tools.t1_inspector import _load_capture


def build_validation_report(events: list[CaptureEvent]) -> dict[str, Any]:
    """构造不修改输入夹具的离线验收报告。"""

    coverage = build_capture_coverage(events)
    return {
        "event_count": len(events),
        "logical_action_count": len(build_logical_actions(events)),
        "complete": bool(coverage["complete"]),
        "capture_coverage": coverage,
        "physical_mapping": build_physical_mapping_table(events),
    }


def main() -> int:
    """输出离线夹具验收报告。"""

    parser = argparse.ArgumentParser(description="验收 T1 遥控区域 JSON 夹具")
    parser.add_argument("input", type=Path, help="Inspector 生成的 JSON 夹具")
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="缺少任一已启用按键时返回失败状态",
    )
    args = parser.parse_args()
    if not args.input.exists():
        print(f"夹具不存在：{args.input}")
        return 1
    events = _load_capture(args.input)
    report = build_validation_report(events)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.require_complete and not report["complete"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""程序说明：只读列出当前 Raw Input HID 传输类型候选。"""

from __future__ import annotations

import argparse
import json

from t1remote.windows.raw_input import (
    enumerate_raw_input_devices,
    summarize_raw_input_devices,
)


def main() -> int:
    """执行 BLE HID、USB HID 和未知 HID 的脱敏清单探测。"""

    parser = argparse.ArgumentParser(description="列出当前 HID 传输候选")
    parser.add_argument(
        "--t1-only",
        action="store_true",
        help="只输出 T1 VID/PID 设备；默认输出全部 Raw Input 设备",
    )
    args = parser.parse_args()
    try:
        devices = enumerate_raw_input_devices(target_only=args.t1_only)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"传输探测失败：{error}")
        return 1
    print(json.dumps(summarize_raw_input_devices(devices), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

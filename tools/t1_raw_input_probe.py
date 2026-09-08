"""程序说明：只读打印当前系统 Raw Input 设备的脱敏清单。"""

from __future__ import annotations

import argparse
import json

from t1remote.windows.raw_input import (
    enumerate_raw_input_devices,
    summarize_raw_input_devices,
)


def main() -> int:
    """执行 Raw Input 设备清单探测。"""

    parser = argparse.ArgumentParser(description="列出当前系统 Raw Input 设备")
    parser.add_argument(
        "--all",
        action="store_true",
        help="输出全部设备；默认只输出 T1-Remote",
    )
    args = parser.parse_args()
    try:
        devices = enumerate_raw_input_devices(target_only=not args.all)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Raw Input 设备探测失败：{error}")
        return 1
    print(json.dumps(summarize_raw_input_devices(devices), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

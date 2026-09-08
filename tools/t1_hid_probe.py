"""程序说明：只读打印当前 T1 HID Collection 的 Usage 和报告长度摘要。"""

from __future__ import annotations

import json

from t1remote.windows.hid_descriptor import (
    inspect_hid_collections,
    summarize_hid_collections,
)


def main() -> int:
    """执行只读 HID 能力探测。"""

    try:
        infos = inspect_hid_collections()
    except (OSError, RuntimeError, ValueError) as error:
        print(f"HID 探测失败：{error}")
        return 1
    print(json.dumps(summarize_hid_collections(infos), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

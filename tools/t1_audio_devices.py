"""程序说明：只读打印可输出的 sounddevice 音频端点。"""

from __future__ import annotations

import json

from t1remote.core.pcm_sink import PcmSinkError
from t1remote.core.sounddevice_sink import enumerate_output_devices


def main() -> int:
    """执行音频输出端点枚举。"""

    try:
        devices = enumerate_output_devices()
    except (PcmSinkError, OSError, RuntimeError, ValueError) as error:
        print(f"音频设备枚举失败：{error}")
        return 1
    print(
        json.dumps(
            [
                {
                    "index": device.index,
                    "name": device.name,
                    "max_output_channels": device.max_output_channels,
                    "default_samplerate": device.default_samplerate,
                }
                for device in devices
            ],
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""程序说明：以非 Shell 方式执行 Key Mapping 中配置的命令行动作。"""

from __future__ import annotations

import subprocess


class CommandExecutionError(RuntimeError):
    """命令行动作启动失败。"""


class WindowsCommandExecutor:
    """使用参数数组启动进程，不解释 Shell 元字符。"""

    def run(self, argv: tuple[str, ...]) -> None:
        """启动一次命令；命令退出状态由被启动进程自行管理。"""

        if not argv or any(not isinstance(item, str) or not item.strip() for item in argv):
            raise CommandExecutionError("命令行 argv 不能为空")
        try:
            subprocess.Popen(list(argv), shell=False)
        except OSError as exc:
            raise CommandExecutionError(f"启动命令失败：{argv[0]}") from exc


__all__ = ["CommandExecutionError", "WindowsCommandExecutor"]

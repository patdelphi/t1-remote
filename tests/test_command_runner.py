"""程序说明：验证命令行动作使用参数数组且不经过 Shell。"""

import unittest
from unittest.mock import patch

from t1remote.windows.command_runner import WindowsCommandExecutor


class CommandRunnerTests(unittest.TestCase):
    @patch("t1remote.windows.command_runner.subprocess.Popen")
    def test_command_uses_shell_false_and_argument_array(self, popen) -> None:
        WindowsCommandExecutor().run(("notepad.exe", "C:\\temp\\note.txt"))

        popen.assert_called_once_with(
            ["notepad.exe", "C:\\temp\\note.txt"],
            shell=False,
        )


if __name__ == "__main__":
    unittest.main()

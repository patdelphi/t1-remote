# T1 Key Mapping MVP 测试说明

程序说明：使用真实 T1 遥控器验证当前 Key Mapping MVP。测试入口会启动驱动租约、读取驱动事件和 Raw Input，并按 JSON 配置调用 `SendInput`。

## 运行

需要查看设备状态、实时计数和最近事件时，启动主前台：

```powershell
python -m tools.t1_app
```

主前台默认勾选 Dry-run。确认驱动状态、租约和事件链路正常后，再取消勾选执行实际输出。

在项目根目录执行：

```powershell
python -m tools.t1_mapping_test --dry-run
```

`--dry-run` 只打印输出，不注入系统按键。确认配置无误后执行真实模式：

```powershell
python -m tools.t1_mapping_test
```

按 `Ctrl+C` 退出。程序会释放活动输出键、停止桥接会话并关闭租约。

设备移除或系统睡眠时，运行时会先释放活动映射；系统恢复后重新确认桥接心跳。若桥接无法恢复，程序会停止当前会话并在诊断日志中保留错误。

## 修改映射

编辑 [config/t1-key-mapping.json](../config/t1-key-mapping.json)。例如把 Home 改成 `Alt+Tab`：

```json
"Home": {
  "type": "shortcut",
  "key": "TAB",
  "modifiers": ["ALT"]
}
```

也可以启动 Tkinter 配置前台：

```powershell
python -m tools.t1_mapping_gui
```

可以用“导入/导出”管理备份配置；保存后运行中的测试会话会自动监视并热加载有效配置。

前台左侧选择物理按键，中间选择动作类型和触发方式，右侧填写参数。支持单键、组合键、HID 特殊功能、命令行、未映射、长按、双击和按住重复；“预览动作”不会调用 `SendInput`，命令行动作也不会启动程序。点击“保存配置”后，已经运行的测试会话会自动监视并热加载有效配置。

当前支持：

- `key`：键盘单键，例如 `A`、`ENTER`、`UP`、`F1`；
- `combo`/`shortcut`：组合键，例如 `ALT+TAB`、`CTRL+C`；
- `special`/`media`：HID 预设功能键，例如 `VOLUME_UP`、`MEDIA_PLAY_PAUSE`、`BROWSER_HOME`、`SLEEP`；
- `command`：命令行参数数组，例如：

```json
"Voice": {
  "type": "command",
  "argv": ["notepad.exe", "C:\\temp\\note.txt"]
}
```

命令使用 `shell=False` 启动，不经过 Shell 展开；配置中的命令会以当前用户权限运行。

- `none`：只观察，不输出。

## 建议测试顺序

优先测试已经由设备级驱动拦截的 Home、Return、Mute、Volume Plus、Volume Minus。Power 和 Voice 默认不输出，便于避免误触发系统动作。

当前 `COL01` 键盘集合仍未由过滤驱动阻断。方向键、OK、Menu 的默认映射可以测试；如果把这些键改成不同输出，Windows 仍可能收到原始键，完整无泄露重映射需要后续扩展 `COL01` 驱动过滤。

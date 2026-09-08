# T1 Remote 宏设计

程序说明：定义按键映射中的安全宏边界、配置格式和编辑器交互。宏只发送键位，不启动外部程序。

## 设计结论

宏由一组有序步骤组成。每个步骤是一个普通虚拟键或媒体/系统虚拟键，可附带 `ALT`、`CTRL`、`SHIFT`、`WIN` 修饰键；步骤发送按下和抬起后，等待该步骤的 `delay_ms` 再执行下一步。

示例：

```json
{
  "type": "macro",
  "steps": [
    {"type": "key", "key": "C", "modifiers": ["CTRL"], "delay_ms": 120},
    {"type": "key", "key": "V", "modifiers": ["CTRL"]}
  ]
}
```

这表示 `Ctrl+C`，等待 120 毫秒，再发送 `Ctrl+V`。最后一步没有等待需求时省略 `delay_ms`。

## UX

- 普通键位、媒体/系统功能、命令行、宏分组展示，避免把不同安全边界混在一个下拉框里。
- 宏使用步骤列表，不要求用户编写脚本语法。
- 每行编辑：步骤类型、键位、修饰键、本步完成后的等待时间。
- 支持添加、更新、删除、上移和下移。
- 预览只展示步骤，不发送按键。
- 宏不支持命令行步骤，也不支持按住重复触发，避免并发宏和外部进程副作用。
- 配置热加载、设备断开和应用退出时取消宏；当前键击仍会完成抬起，防止粘键。

## 参考

- AutoHotkey `Send` 支持组合键、显式 key-down/key-up 和 `Sleep` 间隔：<https://doggy8088.github.io/AutoHotkeyDocs/docs/lib/Send.htm>
- AutoHotkey 对延时的说明：<https://doggy8088.github.io/AutoHotkeyDocs/docs/howto/SendKeys.htm>
- Kanata 的 `macro` 是键或 chord 加可选毫秒延时，并与保持多个键按下的动作区分：<https://github.com/jtroo/kanata/wiki/Configuration-guide>
- Input Remapper 使用多行宏编辑器，并支持 `key`、`key_down`、`key_up`、`wait`：<https://github.com/sezanzeb/input-remapper/blob/main/readme/macros.md>

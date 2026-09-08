# T1RemoteFilter

程序说明：T1 `COL02` Consumer Control 的设备专属 KMDF 下层过滤驱动能力层。

驱动只匹配以下硬件 ID：

```text
HID\{00001812-0000-1000-8000-00805f9b34fb}_Dev_VID&01620A_PID&0407_REV&0000&Col02
```

驱动不保存 Home、音量等最终业务配置。Python 通过 `T1Bridge_SetPolicy` 运行时下发源 Usage、目标 Usage 和拦截范围。命中后，驱动把原始报告写入 `T1BRIDGE_EVENT` 队列；没有目标 Usage 时清零报告，有目标 Usage 时只改写当前 Consumer Control 报告的 16 位 Usage。Python 通过 `T1Bridge_ReadEvent` 读取原始事件并执行更高层的动作映射。

因此，驱动解决的是“能否在 HIDClass 之前拦截和改写”的能力问题，前端 Python 才是最终配置的来源。跨 Usage Page、键盘组合键、鼠标动作和应用快捷键仍由 Python 处理。

## 构建条件

需要 Visual Studio C++ 驱动工具集、Windows Driver Kit 和匹配版本的 KMDF。当前构建环境已经具备这些组件；安装后的设备栈仍需重启一次并完成真机回归。

## 安装边界

`t1filter.inf` 使用 Windows 10 1903 及以上支持的设备专属 `AddFilter` 指令，只向 T1 的 `COL02` 添加下层过滤器，不修改 HIDClass 全局过滤器。微软对设备专属过滤器和 `AddFilter` 的说明：

- https://learn.microsoft.com/en-us/windows-hardware/drivers/install/installing-a-filter-driver
- https://learn.microsoft.com/en-us/windows-hardware/drivers/install/inf-addfilter-directive

驱动包已经生成 `.sys`、`.cat` 和 INF 安装包，并通过本机测试签名和 Inf2Cat 校验。正式发布仍需要正式代码签名证书。

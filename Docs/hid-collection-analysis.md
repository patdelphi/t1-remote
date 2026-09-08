# T1 HID Collection 分析记录

程序说明：记录 Windows 当前枚举到的 T1 HID Collection、已验证的功能和仍需确认的协议细节。本文只记录已获得的系统证据，不根据 Collection 编号推测业务用途。

## 1. 设备范围

- 蓝牙父设备：`T1-Remote`
- VID：`0x620A`
- PID：`0x0407`
- 当前系统枚举到：`COL01`、`COL02`、`COL03`、`COL04`、`COL05`
- Collection 数量：5 个

这些 Collection 属于同一个 T1 BLE HID 父设备，Windows 为每个 Top-Level Collection 建立独立的 HID 子设备实例。

## 传输候选探测

可以运行 `python -m tools.t1_transport_probe` 查看当前 Raw Input 设备的脱敏传输提示。输出中的 `ble-hid` 表示 BLE HID 路径，`usb-hid` 只表示 USB HID 候选，可用于后续检查 2.4GHz 接收器；它不会证明接收器属于 T1，也不会自动加入映射策略。使用 `--t1-only` 可只查看当前 VID/PID 的 T1 设备。

`python -m tools.t1_hid_probe` 现在还会读取可用的 `HidP_GetButtonCaps` 输入字段摘要，包括 Report ID、Usage Page、Usage 范围、ReportCount 和 LinkCollection。字段数组读取失败时仍保留基础报告长度信息，不把不完整摘要当成完整报告描述符。

## 2. 当前证据

| Collection | 当前确认结果 | 证据来源 | 确认程度 |
| --- | --- | --- | --- |
| `COL01` | 键盘设备 | PnP 类别为 `Keyboard`，服务为 `kbdhid`；Hardware ID 为 `UP:0001_U:0006` | 已确认 |
| `COL02` | Consumer Control | PnP 硬件 ID 含 `HID\VID_620A&UP:000C_U:0001`、`HID_DEVICE_SYSTEM_CONSUMER`；`T1RemoteFilter` 已挂载；Home 行为已验证可以被拦截 | Top-Level Usage 已确认，Descriptor 字段待补 |
| `COL03` | System Control | PnP 硬件 ID 含 `HID\VID_620A&UP:0001_U:0080`、`HID_DEVICE_SYSTEM_CONTROL`；`T1RemoteFilter` 已挂载；Power 行为已验证可以被拦截 | Top-Level Usage 已确认，Descriptor 字段待补 |
| `COL04` | 鼠标设备 | PnP 类别为 `Mouse`，服务为 `mouhid`；Hardware ID 为 `UP:0001_U:0002` | 已确认 |
| `COL05` | Vendor Defined HID | PnP 硬件 ID 含 `HID\VID_620A&UP:FF00_U:0000`；`HidP_GetCaps` 返回 Usage Page `0xFF00`、Usage `0x0000`、Input Report 长度 `256` | HID 类型已确认，业务用途未确认 |

驱动状态中的 `attached_collections=12` 是位掩码：

```text
12 = 0b01100 = (1 << 2) | (1 << 3)
```

它表示当前过滤器已附着 `COL02` 和 `COL03`，不是已经附着 12 个 Collection。

## 3. 已完成的设备级取证

### 3.1 父设备和驱动栈

当前 T1 HID 父设备的注册信息为：

- 硬件 ID 包含 `BTHLEDevice\{00001812-...}_Dev_VID&01620A_PID&0407`。
- 父 HID 服务为 `mshidumdf`，下层为 `WUDFRd`。
- `0x1812` 对应 Bluetooth LE HID Service；这是设备类型证据，不代表每个 Collection 的业务用途。

### 3.2 Collection 子设备服务

当前五个 Collection 的 PnP 结果如下：

| Collection | PnP 类别 | PnP 服务 | 当前设备名 |
| --- | --- | --- | --- |
| `COL01` | `Keyboard` | `kbdhid` | `HID Keyboard Device`；Hardware ID 为 `UP:0001_U:0006` |
| `COL02` | `HIDClass` | 空 | `T1 Remote HID Filter`；Hardware ID 为 `UP:000C_U:0001` |
| `COL03` | `HIDClass` | 空 | `T1 Remote HID Filter`；Hardware ID 为 `UP:0001_U:0080` |
| `COL04` | `Mouse` | `mouhid` | `HID-compliant mouse`；Hardware ID 为 `UP:0001_U:0002` |
| `COL05` | `HIDClass` | 空 | `符合 HID 标准的供应商定义设备`；Hardware ID 为 `UP:FF00_U:0000` |

`COL02`、`COL03` 的 Top-Level Usage 已由 PnP Hardware ID 确认；`COL05` 仍只能确认是 Vendor Defined，不能仅凭名称判断它是飞鼠或麦克风。

### 3.3 Report Descriptor 读取边界

已对当前 T1 的 `COL02`、`COL03`、`COL05` 设备接口做过只读查询：

- `COL05` 的 `HidD_GetPreparsedData` 和 `HidP_GetCaps` 成功，得到 `Usage Page=0xFF00`、`Usage=0x0000`、`InputReportByteLength=256`。
- `COL02`、`COL03` 的 `HidD_GetPreparsedData` 返回 `ERROR_INVALID_FUNCTION (1)`；这不影响 PnP Hardware ID 对它们 Top-Level Usage 的确认。
- 直接请求 `IOCTL_HID_GET_REPORT_DESCRIPTOR` 也返回 `ERROR_INVALID_FUNCTION (1)`，没有得到原始 Descriptor 字节。

因此当前可以确认 Collection 数量、PnP 类别、Top-Level Usage、过滤器附着关系和部分行为；还不能确认每个物理按键在 Report 中的字段位置、Report ID、按下值和释放值。

### 3.4 当前抓不到 HID 报告的根因

Inspector 会话期间直接读取驱动统计得到：

- `state=running`、`attached_collections=12`、租约有效。
- `received_reports=0`、`blocked_reports=0`、`device_control_reports=0`、`internal_device_control_reports=0`。
- `pnputil /enum-devices /instanceid ... /stack` 显示设备栈为 `T1RemoteFilter -> mshidumdf`。

这说明问题发生在 Python 事件表之前：当前过滤器没有收到输入报告请求。原驱动只向 HID DeviceControl 和 InternalDeviceControl 队列注册回调，没有注册 `EvtIoRead`；针对 BLE HID/UMDF 的持续报告路径不够。

已补充 `EvtIoRead` 转发和共用完成回调，并成功构建新的 `t1filter.sys`/CAT 包。新包尚未安装；安装后需要重启设备栈才能验证 `received_reports` 是否开始增长。

### 3.5 Raw Input 设备清单诊断

已增加只读清单命令：

```powershell
python -m tools.t1_raw_input_probe
python -m tools.t1_raw_input_probe --all
```

默认命令只输出 T1；`--all` 输出当前用户会话登记的全部设备，但始终只输出设备类型、Collection 和脱敏归属。当前主机实测默认结果为空，`--all` 可以看到 26 条其他 Raw Input 设备，没有 `VID_620A/PID_0407`。这说明本次会话的 Raw Input 设备清单中没有 T1，问题早于按键报文解析；需要先恢复 T1 HID 子设备或重新建立用户会话，再复测 HID 接口和驱动队列。

### 3.6 COL02/COL03 驱动事件采集

Power、Home、Return、Voice、Mute 和音量键由过滤驱动队列提供，不能依赖 Raw Input Inspector。使用以下命令采集驱动事件：

```powershell
python -m tools.t1_driver_inspector --output captures/t1-driver-control-YYYYMMDD.json
```

该工具通过 `T1Bridge_ReadEvent` 读取事件，记录 Collection、Usage Page、Usage、原始报告、序号、驱动时间戳和按下/释放状态；设备路径和蓝牙地址不会写入夹具。每个实体键仍需人工单独按下并核对报告，空会话不会创建夹具文件。

## 4. 当前不能直接下结论的内容

- `COL05` 不能直接认定为 Air Mouse、麦克风或其他具体功能；目前只确认它是 Vendor Defined HID。
- `COL02`、`COL03` 的业务用途已有 Home/Power 行为和策略证据，但还没有成功读取到它们的完整 Report Descriptor。
- Mute、Volume、Voice、Return 等按键的实际 Collection、Usage 和 Report 格式尚未完成逐键确认。

## 5. 后续验证顺序

当前可以使用只读探测入口检查 T1 Collection 能力摘要：

```powershell
python -m tools.t1_hid_probe
```

工具只输出 Collection、Usage Page、Usage 和报告长度，不输出完整设备路径。当前主机实测返回空列表，说明系统虽然枚举了 `T1-Remote` GATT 服务，但本次会话没有可打开的 T1 HID Collection；这项结果需要在 T1 真机保持连接并完成 HID 子设备枚举后复测。

1. 安装新驱动并重启一次设备栈。
2. 运行 Raw Input 清单命令，确认 T1 的 `COL01` 至 `COL05` 已出现在当前用户会话。
3. 在 Raw Input 和驱动队列同时运行时，每次只按一个物理键。
4. 记录设备路径、Collection、Usage Page、Usage、Report ID、原始报告和按下/释放状态。
5. 只有在单键证据与 Descriptor 对齐后，才调整 Python 策略或过滤驱动。

## 6. 当前结论

当前 T1 在 Windows 中有 5 个 HID Collection。键盘和鼠标已通过 PnP 明确识别；Home 和 Power 的两个拦截路径已通过行为验证；Vendor Defined 的 `COL05` 仍需协议分析，暂不绑定具体业务功能。

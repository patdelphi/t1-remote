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

`t1_hid_probe.py` 只通过用户态 `HidD_GetPreparsedData` 和 `HidP_GetCaps`/`HidP_GetButtonCaps` 获取能力摘要，不从用户态直接发送内核专用的 `IOCTL_HID_GET_REPORT_DESCRIPTOR`。

原始 Report Descriptor 由过滤驱动向下层 HID minidriver 请求，再由 `t1_driver_inspector.py` 保存。取得后才交给纯 Python 解析器转换为字段规则：输入/输出/Feature 类型、Report ID、Usage、位偏移、位宽、数量、Flags 和 Collection 路径。解析失败时只保留原始描述符，不生成猜测字段。

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
- 旧现场记录中 `COL02`、`COL03` 的 `HidD_GetPreparsedData` 曾返回 `ERROR_INVALID_FUNCTION (1)`；这不影响 PnP Hardware ID 对它们 Top-Level Usage 的确认。系统重启后的连接窗口中，用户态探针和过滤器桥接均已成功取得 preparsed data。
- 旧实现曾从用户态直接请求内核专用的 `IOCTL_HID_GET_REPORT_DESCRIPTOR`，返回 `ERROR_INVALID_FUNCTION (1)`；该路径已移除。当前原始 Descriptor 等待通过新桥接 IOCTL 从过滤器下层取得。

因此当前可以确认 Collection 数量、PnP 类别、Top-Level Usage、过滤器附着关系、报告长度和部分 HIDP 能力；原始 Report Descriptor 仍不可用，不能据此确认每个物理按键的完整字段位置、Report ID、按下值和释放值。

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

### 3.7 2026-09-10 preparsed data parser 验证

系统重启后的 T1 连接窗口中，过滤器按 Microsoft 官方顺序取得两条 Collection 的 preparsed data，各为 268 字节。新增的 `inspect_preparsed_data()` 只在内存中临时包装这段 opaque bytes，然后调用 `HidP_GetCaps` 和 `HidP_GetButtonCaps`，不自行拆解保留结构；`parse_input_data()` 使用 `HidP_MaxDataListLength` 和 `HidP_GetData` 读取报告中的 DataIndex/值。

已实测的基础能力为：

| Collection | Usage Page/Usage | Report ID | 输入报告长度 | 输入 Usage 能力 |
| --- | --- | --- | --- | --- |
| `COL02` | `0x0C:0x01` | `2` | `3` | `0x0000`–`0x028C`，1 个绝对输入字段 |
| `COL03` | `0x01:0x80` | `3` | `2` | `0x0081`–`0x0083`，1 个绝对输入字段 |

这些是 HIDP 能力摘要，不是原始 Report Descriptor。原始 Descriptor 查询仍返回 Win32 `1`，过滤器内部记录 `0xC0000010 (STATUS_INVALID_DEVICE_REQUEST)`；不能仅凭能力范围生成通用字段重写规则。

已有真实报告样本的 HIDP 解析结果：`COL02` 的 `02 21 02` 返回 `DataIndex=545, RawValue=1`，对应 `0x0C:0x0221`；`COL03` 的 `03 01` 返回 `DataIndex=0, RawValue=1`，对应 `0x01:0x0081`。两个释放报告均返回空 DataIndex 列表。该结果支持逐报告验证，不代表整个 Usage 范围都已完成逐键验收。

## 4. 当前不能直接下结论的内容

- `COL05` 不能直接认定为 Air Mouse、麦克风或其他具体功能；目前只确认它是 Vendor Defined HID。
- `COL02`、`COL03` 的业务用途已有 Home/Power 行为和策略证据，HIDP 基础能力已取得，但还没有成功读取到它们的完整 Report Descriptor。
- Mute、Volume、Voice、Return 等按键的实际 Collection、Usage 和 Report 格式尚未完成逐键确认。

## 5. 后续验证顺序

当前可以使用只读探测入口检查 T1 Collection 能力摘要：

```powershell
python -m tools.t1_hid_probe
```

工具只输出 Collection、Usage Page、Usage 和报告长度，不输出完整设备路径。当前主机实测返回空列表，说明系统虽然枚举了 `T1-Remote` GATT 服务，但本次会话没有可打开的 T1 HID Collection；这项结果需要在 T1 真机保持连接并完成 HID 子设备枚举后复测。

1. 保持 T1 连接并确认 COL02/COL03 从 `CM_PROB_PHANTOM` 恢复为 `OK/Present`。
2. 运行 `inspect_preparsed_data()` 或 Driver Inspector，读取 HIDP 能力和原始驱动事件。
3. 在驱动队列运行时，每次只按一个物理键。
4. 用 `HidP_` 例程验证 Usage、DataIndex、Report ID 和按下/释放状态；不从 opaque bytes 自行推导结构。
5. 只有在单键证据与 HIDP 能力对齐后，才调整 Python 策略或过滤驱动；原始 Descriptor 若仍不可用，保持 `descriptor_unavailable`。

## 6. 当前结论

当前 T1 在 Windows 中有 5 个 HID Collection。键盘和鼠标已通过 PnP 明确识别；Home 和 Power 的两个拦截路径已通过行为验证；Vendor Defined 的 `COL05` 仍需协议分析，暂不绑定具体业务功能。

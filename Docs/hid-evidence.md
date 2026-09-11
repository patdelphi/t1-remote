# T1 HID 报告证据

## 用途

HID 报告证据用于回答三个独立问题：报告属于哪类 HID、报告携带了哪些协议字段、连续报告是否构成一次按键操作。它不会把物理按键名称写回映射配置。

## 记录字段

`tools/t1_hid_probe.py` 输出 Collection 的报告长度和 `HidP_GetButtonCaps` 能力摘要；它不从用户态直接发送内核专用的 `IOCTL_HID_GET_REPORT_DESCRIPTOR`。

`tools/t1_driver_inspector.py` 通过过滤驱动向下层 HID minidriver 读取原始 Report Descriptor，并在保存的 JSON 中记录 `hid_descriptors`；同一 Collection 的事件还会把描述符用于 `hid_evidence` 的 Report ID 校验：

桥接层还提供 `T1Bridge_GetPreparsedData`。过滤器按官方 HID 顺序向下层发送 `IOCTL_HID_GET_COLLECTION_INFORMATION`，取得 `HID_COLLECTION_INFORMATION.DescriptorSize` 后，再发送 `IOCTL_HID_GET_COLLECTION_DESCRIPTOR` 取得 opaque Collection Descriptor。这个结果只作为能力证据保存，不能当作原始 Report Descriptor 解析。

用户态 HID parser 还提供 `parse_input_data()` 和 `describe_input_data()`：前者调用 `HidP_GetData` 得到当前报告的 DataIndex/原始值，后者使用 `HidP_GetButtonCaps` 的范围关系生成 Usage 候选。内核过滤器也在缓存 preparsed data 时读取 Button/Value Caps，并在 Read 完成回调优先用同一类 DataIndex 能力解析唯一非零控制值。多候选和未匹配状态会保留，不会从报告字节位置或首字节猜测字段含义；无法唯一解析时才回退 T1 兼容布局。

- `report_category`：`keyboard`、`consumer`、`system`、`mouse`、`vendor` 或 `unknown`。
- `report_id`：只有 Report Descriptor 确认报告带 ID 前缀，或上游显式提供 ID 时才填写；否则为 `null`。
- `usage_page`、`usage`：上游报告提供的数字字段，未确认时保留 `null`。
- `raw_report_hex`：原始报告的十六进制副本。
- `event_kind`：`down`、`repeat`、`up` 或 `unknown`。
- `down_up_duration_ms`：当前抬起报告与对应按下报告之间的时长；缺少可用时间戳时为 `null`。

`actions` 会把同一按键的重复按下合并为一个动作，并记录 `repeat_count`、按下/抬起事件编号和 `duration_ms`。

## 分类规则

- Usage Page `0x07`：`keyboard`。
- Usage Page `0x0C`：`consumer`。
- Generic Desktop Page `0x01` 中，COL03 或系统控制 Usage 归为 `system`；COL04 或 Mouse Usage `0x02` 归为 `mouse`。
- Usage Page `0xFF00`–`0xFFFF`：`vendor`。
- 其余无法由已提供字段确认的报告：`unknown`。

分类只描述协议证据。当前 T1 的 `0x0C:0x0221` 的官方名称是 `AC Search`；项目把它映射为 `Voice` 是 T1 业务层名称，不是 HID 标准名称。

## 描述符状态

- `descriptor_available`：已取得并成功解析 Report Descriptor。
- `descriptor_unavailable`：当前过滤驱动或 HID 子接口无法取得原始描述符；不会从原始报告首字节猜测 Report ID。
- `descriptor_invalid`：取得了字节，但解析失败，需要保留原始描述符并单独排查。

没有描述符时仍可保存原始报告、上游提供的 Usage 和驱动时间戳。长按属于时间序列语义，只有按下与抬起报告配对后，才能由 `duration_ms` 与映射阈值比较。

## 现场验收边界

当前 COL02/COL03 的原始 Report Descriptor 仍为 `descriptor_unavailable`：过滤器查询返回 Win32 错误码 `1`，驱动记录的原始 NTSTATUS 为 `0xC0000010 (STATUS_INVALID_DEVICE_REQUEST)`。按 Microsoft 官方顺序取得的 preparsed data 则各为 268 字节，并已通过 `HidP_GetCaps`；它只能作为当前句柄生命周期内的 opaque HID parser 输入，不能当作原始 Report Descriptor 保存或解析。该状态不会阻止 Driver Inspector 保存报告，但不应据此固化 Report ID 或修改标准 HID 类型判断。真实描述符可取得后，再用 `report_id_source=descriptor` 的记录确认报告前缀。

当前已取得的现场样本：COL02 `02 21 02` → `DataIndex=545, RawValue=1`；COL03 `03 01` → `DataIndex=0, RawValue=1`；对应释放报告没有 active DataIndex。样本只证明这两个报告的 parser 输出，不能代表所有物理按键或证明内核字段偏移。

## 2026-09-11 现场 parser/DataIndex 复核

T1 重新连接后的只读桥接会话确认 COL02/COL03 均为 OK/Present，当前驱动包为 oem354.inf、版本 11.52.24.706。两条 Collection 的 preparsed data 各为 268 字节；HidP_GetCaps 报告输入长度分别为 3 和 2，HidP_GetButtonCaps 返回范围型能力：

- COL02：DataIndex 0–652 对应 Usage 0x0000–0x028C；已有样本的 0x0221/0x0223/0x0224/0x00E2/0x00E9/0x00EA 均得到唯一候选。
- COL03：DataIndex 0–2 对应 Usage 0x0081–0x0083；已有 03 01 样本得到 DataIndex 0、Usage 0x0081。

对应释放样本 02 00 00、03 00 没有 active DataIndex。该结果支持当前样本的 parser-first 解码和内核 DataIndex 缓存路径，仍不等于全部实体按键已逐键验收；未取得原始 Report Descriptor 时也不从字节偏移推导新的通用字段规则。

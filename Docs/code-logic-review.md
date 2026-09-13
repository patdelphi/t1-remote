# T1 Remote 代码逻辑复审（两轮合并）

程序说明：合并本轮两次代码审查的结论，范围限定为代码逻辑本身——Python 21,158 行与原生 C 3,472 行——不含安装、驱动包、发布产物和现场设备状态。判定依据是逐行读源码与 Microsoft Learn 官方文档对照；现场证据只作为代码行为的旁证，不单独作为结论。本文与 `Docs/microsoft-hid-review.md`（持续更新的官方文档对照日志）互补，不替换其现场记录。

## 2026-09-13 dev 分支第二批修复（优先于下方全部结论）

按已确认的"管理员 App"方案实施 C-2/C-6，并修复一处新发现的配置监视缺陷与测试隔离缺陷。

- C-2：控制设备 SDDL 改为 `SY/BA 全权 + BU 只读`；11 个 IOCTL 全部脱离 `FILE_ANY_ACCESS`，查询类用 `FILE_READ_DATA`、修改类用 `FILE_WRITE_DATA`（`t1bridge_protocol.h`）。桥接 DLL 仍以读写方式打开，因此 App 需要管理员权限；普通用户句柄只能读状态、能力和事件，不能改策略。
- C-6：控制会话按文件对象确定所有者。第一个下发策略的句柄成为所有者并持引用；非所有者的 SET_POLICY/START/STOP/HEARTBEAT/FLUSH_EVENTS 返回 `STATUS_ACCESS_DENIED`；所有者句柄关闭等同 STOP（停过滤、清租约与活动 Usage）。查询类请求不受限。
- W-1（新）：`mapping_watch` 原先以 `st_mtime_ns + st_size` 判断变化，Windows 写入时间未刷新时，等长内容重写会被漏检、热加载丢失。改为长度 + SHA-256 内容摘要，等长改动也能触发且不会误触发重载。
- T-1（新）：`test_mapping_session` 把假桥接返回的 `b"COL02"` 当作 preparsed data 交给真实原生解析器，越界读取随内存布局偶发长时间阻塞（表现为 `input_events == 0`、驱动线程卡住）。测试边界已补 `parse_input_data` 打桩，全量连跑 5 次稳定。
- 构建验证：WDK 10.0.19041 + VS2019 BuildTools 编译 KMDF 驱动通过（Level4 警告即错误，0 警告），Inf2Cat 可签名性 0 错误 0 警告；桥接 DLL 以 `/W4 /WX /utf-8` 编译 0 警告。
- 未完成：C-3/C-5（需要请求所有权设计与内核并发验证）、C-8（需要 ABI 兼容决策）、L-4、A-1；权限与会话行为尚未在真机（双客户端、异常关闭、无权限）验证。

## 2026-09-12 复核与首批修复（优先于下方历史结论）

下方审查保留为历史依据，当前执行状态见本节及 [optimization-plan.md](optimization-plan.md)。

- C-1：GET_INPUT_REPORT 入口拒绝 UserMode；完成辅助函数解引用 packet 前再次检查 RequestorMode。保留内核包及 MDL 范围检查。此方案不支持用户发起的 GET 报告，不在完成例程调用 ProbeForRead；用户页锁定支持仍未实现。
- C-4：属性查询 WDFMEMORY 明确以 Device 为父对象，早退路径随设备销毁回收。
- L-2：Power 统一使用 0x0081；零报告释放优先于旧 Usage 元数据。C/Python 兼容路径仅识别已确认的 COL03 两字节 Report ID 03、payload 00/01，不外推 Sleep/Wake。C 无 parser 时拒绝 System Control 字段改写。
- L-3：Mapping 在 START 前准备 COL02/COL03 parser，缺接口、空数据或查询/检查失败进入 error 并清理；重连 START 前重新准备。dry-run 不要求 parser。初始化成功不证明每条动态报告均可唯一解析。
- C-7：撤销缺陷判定。8 是策略目标数量上限，32 是按集合编号索引的状态表容量，分别校验数量与编号，不需对齐。
- C-3：锁内解析是性能风险；未测量持锁时间，不能认定已超过 25 微秒。缓存包含共享可写工作缓冲区，不能只复制指针就移到锁外。
- C-5：保留为并发风险；仅增加布尔标记不能证明请求独占和只完成一次。
- C-2：权限过宽，但具体提权利用尚未证明。收紧 SDDL 会影响普通用户 App；Access 位改变也会改变 IOCTL 控制码，必须同步桥接与驱动。
- A-1：kbdhid/mouhid 上滤示例不直接证明适用于 System Control；迁移位置仍需独立验证。

本次不更改 ACL、ABI、安装状态或启动配置。原生源码契约测试不代替 WDK 编译和真机验证。

## 1. 审查轮次与方法

| 轮次 | 范围 | 方法 |
| --- | --- | --- |
| 第一轮 | 分层架构、core/windows 边界、原生层资源与并发风险 | 通读源码 + 并行子代理审查 |
| 第二轮 | 驱动与 Python App 对 MS 官方契约的符合性、Power 拦截链路逐行追踪 | 逐行读 `t1filter.c` / `t1bridge.c` / `mapping_session.py`，官方文档对照 |

幂等校验：`python -m pytest -q` → `279 passed`（15.2s）。本次审查未修改任何源码。

## 2. 历史结论摘要（当前状态见开头修订）

| 编号 | 问题 | 位置 | 判定 |
| --- | --- | --- | --- |
| C-1 | GET_INPUT_REPORT 用户缓冲区在 RequestorMode 检查前解引用，无 ProbeForRead | `t1filter.c:1583-1600` | 违规，可 bugcheck |
| C-2 | 控制面 IOCTL 全部 FILE_ANY_ACCESS，SDDL 给 BU 读写 | `t1bridge_protocol.h:57-78`、`t1filter.c:2357` | 违规，本地权限越界 |
| L-1 | Power 拦截在纯逻辑上有三处断裂（策略值、parser 静默降级、位域解码） | 见 §3 | 功能缺陷 |
| L-2 | `_decode_system` 用报告字节覆盖事件携带的权威 Usage | `input_mapping.py:161-162` | 语义错误（Power 靠巧合命中） |
| C-3 | 自旋锁内做 HID 解析 + 688B 策略拷贝 + 多重规则扫描 | `t1filter.c:1060-1184`、`1284-1326` | 违反锁持有 ≤ 25µs |
| C-4 | WDFMEMORY 默认父对象为 Driver，早退路径不释放 | `t1filter.c:2448-2477` | 生命周期缺陷 |
| C-5 | EvtIoStop 与完成例程在 Untrack→Complete 窗口存在竞争 | `t1filter.c:1533-1540`、`1952-1976` | 边界风险 |
| C-6 | 全局单策略/单租约，任一客户端 Close 即全局 STOP | `t1filter.c:2009-2338`、`t1bridge.c:443-444` | 会话模型缺陷 |
| L-3 | parser 能力不可用时静默降级，界面仍显示运行 | `mapping_session.py:425-446` | 与项目文档声称不符 |
| L-4 | 采集路径先解析 Usage 再决定入队，未知布局可能漏报 | `microsoft-hid-review.md` P1-2 | 能力限制 |
| C-7 | ABI 常量不对称（目标集合 8 vs 32） | `t1bridge_protocol.h:29`、`t1filter.h:14` | 次要 |
| C-8 | 事件统计语义混用，队列 64 容量静默丢最旧 | `t1filter.c:1223-1231`、`2217` | 可观测性 |
| A-1 | 过滤器挂载位置官方不推荐；System Control 为 OS 保留集合 | `t1filter.inf:50-51` | 架构级未闭环 |

## 3. Power 键拦截链路的纯逻辑分析

### 3.1 判定链

驱动的拦截是一个四与门（`t1filter.c:1181-1183`）：

```
blocked = filtering_enabled && lease_valid && target_collection && (matched || drop_unmapped)
```

命中后行为（`t1filter.c:1704-1719`、`1835-1850`）：需要重映射时改写字段，否则调用 `T1FilterClearReportPayload` 只清零 payload、保留首字节 Report ID（`t1filter.c:1543-1555`）。清零保留 Report ID 与官方 `Obtaining HID Reports` 的契约一致，这部分是合规的。

Python 侧对应的条件链是：会话 start 下发 `FLAG_ENABLED` 策略（`mapping_session.py:200-211`）→ 请求 COL02/COL03 preparsed data（`mapping_session.py:216`）→ 心跳线程每 1 秒刷新 3 秒租约（`mapping_session.py:498-517`）。

### 3.2 三处代码级断裂

**断裂 1：策略源 Usage。** 默认策略现为 `HidUsage(0x01, 0x81, "COL03")`（`driver_bridge.py:423`），与 parser 解码出的 `0x0081` 一致，代码层面已闭合。但它的历史成因是"同一个 Usage 在 fallback 与 parser 两条解码路径下取值不同"，即断裂 3。

**断裂 2：parser 静默降级。** `_prime_hid_parser` 在获取或解析失败时只记日志并 `continue`（`mapping_session.py:439-441`），会话照常进入 `running`。此时驱动没有 parser 缓存，只能走断裂 3 的 fallback 解码，Power 不会命中策略，但界面仍显示"驱动 running、租约有效"。项目文档称"接口不可用时会拒绝进入有效拦截态"，该拒绝在 mapping 会话路径并未实现（仅捕获页有相应处理）。

**断裂 3：System Control 位域解码语义错误。** COL03 是 System Control 位域集合，payload 字节值 `0x01` 表示 bit0，对应 Usage `0x0081`（`0x80 + bit 序号`），这也是现场样本 `03 01` 经 parser 得到 `0x0081` 的原因。但两条 fallback 都直接把字节值当 Usage：

- C：`decoded_usage = (USHORT)Report[1]`（`t1filter.c:1092-1105`）→ 得到 `0x0001`；
- Python：`report_usage = report[1]; parsed_usage = report_usage or ...`（`input_mapping.py:161-162`）→ 得到 `0x0001`。

结果：parser 缓存缺失时，Power 报告的 Usage 被解成 `0x0001`，与策略 `0x0081` 不匹配 → `matched = FALSE` → 报告原样放行 → 系统仍收到 `0x0081`。同源问题还影响无 parser 时的字段改写分支：`t1filter.c:1317-1320` 把 Usage 值直接写回 payload 字节，对位域布局同样不成立（当前默认 `remap_enabled=False`，该分支未被触发）。

对照：COL02（Consumer Control）的 fallback 是成立的，因为其报告 payload 直接携带 Usage 小端值（样本 `02 21 02` → `0x0221`），`_little_endian_usage`（`input_mapping.py:181-184`）语义正确。只有位域集合（COL03）会被解错。

**附带窗口：** 会话先 `bridge.start()`（`mapping_session.py:211`）再请求 parser 数据（`:216`），两者之间 filtering 已启用但 parser 为空，同断裂 3 的后果。

### 3.3 位置级疑问（架构，代码层无法判定）

- 当前过滤器位于 HIDCLASS 与传输 minidriver 之间。官方 HID 客户端驱动文档明确写 **"Filter drivers aren't recommended as a filter between HIDCLASS and HID transport minidrivers"**；官方认可的位置示例是 kbdhid/mouhid 或 kbdclass/mouclass 的上滤。
- System Control 集合（Usage Page 0x01、Usage 0x80）对应 `HID_DEVICE_SYSTEM_CONTROL`，官方定义为"操作系统打开自用的保留集合，vendor INF 不得匹配"。本仓库 INF 匹配的是 vendor 格式硬件 ID（`t1filter.inf:26-27`），未违反该条。
- `0x0081` 在官方 Button Reporting 中被定义为电源按钮输入，其后果由系统电源策略处理，而官方未公开说明 hidclass 在哪一层完成该转换。
- 因此"清零 payload 能否阻止电源动作"属于代码层无法判定的问题：若系统在 HIDCLASS 内部或更低层消费 `0x81`，下层过滤器改写报告就来不及。需要真机事件序列或内核调试确认（列为 §7 未闭环项，与安装状态无关）。

## 4. 违规与风险详情

### C-1 GET_INPUT_REPORT 用户缓冲区解引用（最高优先）

`T1FilterGetInputReportBuffer` 在 `t1filter.c:1583` 取得 `irp->UserBuffer` 后立即在 `:1584` 解引用 `HID_XFER_PACKET`，而 `RequestorMode` 检查在 `:1594`、且只在 `MdlAddress == NULL` 分支内。全函数没有 `ProbeForRead`，也没有 try/except。

官方要求：METHOD_NEITHER 的 `Irp->UserBuffer` 是未经验证的用户态地址，驱动必须先判 `RequestorMode`、在 try/except 内 `ProbeForRead` 再访问，且 `ProbeForRead` 要求 IRQL ≤ APC_LEVEL；WDF 文档也要求 Neither 模式由驱动自行验证可访问性。该函数由完成例程调用（`t1filter.c:1662`），而完成例程可运行在 DISPATCH_LEVEL，`IOCTL_HID_GET_INPUT_REPORT`（METHOD_OUT_DIRECT）与 `IOCTL_UMDF_HID_GET_INPUT_REPORT`（METHOD_NEITHER）共用同一处理器（`t1filter.c:1892-1900`）。

影响：正常路径（HIDCLASS 以 METHOD_OUT_DIRECT 下发、UserBuffer 为内核态 `HID_XFER_PACKET`）可用；一旦用户态或 METHOD_NEITHER 请求到达，即可能非法访问用户地址，导致 bugcheck 或任意内核读。
建议：先判 `RequestorMode` 与 `MdlAddress`；UserMode 分支一律 ProbeForRead + try/except（或在 DISPATCH 直接拒绝），再做二级指针解引用与 MDL 范围校验。

### C-2 控制面权限过宽

全部 11 个桥接 IOCTL 使用 `FILE_ANY_ACCESS`（`t1bridge_protocol.h:57-78`），控制设备 SDDL 为 `D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;BU)`（`t1filter.c:2357`），符号链接 `\DosDevices\T1RemoteFilter`，处理函数不校验调用者（`t1filter.c:2009-2338`）。

官方 `Defining I/O Control Codes` 明确：`FILE_ANY_ACCESS` 仅适用于"确信无限制访问不会给攻击者提供入侵路径"的场景；句柄权限检查只在 `FILE_READ_DATA` / `FILE_WRITE_DATA` 下发生。
影响：任意本地或域用户（BU 含所有本地与域用户）只要打开 `\\.\T1RemoteFilter`，即可 `SET_POLICY`（把拦截 Usage 重映射到同 UsagePage 的任意控制）、`START`、`STOP`，破坏设备行为或关闭过滤。控制设备的创建流程本身合规。
建议：管理类 IOCTL 改 `FILE_WRITE_DATA`，查询类改 `FILE_READ_DATA`；SDDL 收紧为 SY/BA 的 GA，去掉 BU 的 GW。

### C-3 自旋锁内的重工作

`T1FilterShouldBlockReport` 从 `t1filter.c:1060` 持锁到 `:1184`：锁内复制约 688 字节策略结构、调用 `HidP_GetData`（`:852`）与 `HidP_GetUsagesEx`（`:931`）、执行 data_map/usage/field 多重规则循环。`T1FilterRewriteReport` 同样在锁内跨 `HidP_UnsetUsages` / `HidP_SetUsages`（`:1284-1326`）。此外 `T1FilterEvtDeviceCleanup` 在锁内 `ExFreePoolWithTag`（`:2491-2505`）。

官方 `Introduction to Spin Locks`：持锁例程应尽快执行，**任何例程持锁不得超过 25 微秒**；KMDF 自旋锁在 DISPATCH_LEVEL 获取。四个 `HidP_*` 例程的 IRQL 上限本身是 DISPATCH_LEVEL，因此不构成 IRQL 违规，但锁粒度明显违反"尽可能快"条款，在高频 BLE 报告下会放大 DPC 延迟与争用。
建议：锁内只取策略快照（引用计数或双缓冲切换），把解析与规则匹配移到锁外。

### C-4 WDFMEMORY 生命周期

`T1FilterDetectCollection` 以 `WDF_NO_OBJECT_ATTRIBUTES` 调用 `WdfDeviceAllocAndQueryProperty`（`t1filter.c:2448-2454`），两个返回点（`:2460-2462` 缓冲区无效、`:2477` 正常）都没有 `WdfObjectDelete`。按官方 `Summary of Framework Objects`，未指定父对象时 WDFMEMORY 的默认父对象是 **Driver 对象**，因此每次设备增删都会遗留一个 memory 对象直到驱动卸载；反复插拔会累积。这不是即时泄漏，官方示例的做法是显式 `attributes.ParentObject = device`。
建议：设 `ParentObject = device`，或两个返回点前删除。

### C-5 EvtIoStop 边界竞争

六步取消同步实现正确：锁内查找 + `WdfObjectReference` → `WdfRequestCancelSentRequest` → `WdfObjectDereference`（`t1filter.c:1965-1967`、`610-641`），完成例程从集合移除（`:1533`）。空 `EvtIoResume`（`:1978-1987`）依赖"请求已在下层目标、由完成例程负责完成"的假设。

风险：`T1FilterUntrackSentRequest`（`:1533`）与 `WdfRequestCompleteWithInformation`（`:1536`）之间存在窗口，此时请求已不在集合中，并发的 EvtIoStop 会走 `else` 分支调用 `WdfRequestStopAcknowledge(FALSE)`，与正在完成的请求并发操作同一对象，不满足官方"EvtIoStop 必须对请求有独占访问"的要求。另外 Suspend 分支依赖的两条官方前置条件（下层在 Dx 停止全部未完成请求、本驱动完成例程能在低功耗态完成）属未经验证的设计假设。
建议：在请求上下文中增加"正在完成"标记，或让完成例程在锁内完成集合项状态迁移。

### C-6 全局单策略与多客户端模型

驱动只有一份全局 policy 与租约，没有 per-handle 引用计数；`T1Bridge_Close` 会发送 `STOP`（`t1bridge.c:443-444`），因此任一客户端关闭都会停掉所有客户端的过滤。Python 侧用 `SingleInstanceGuard`（`mapping_session.py:195-197`）回避了实际的多实例场景，但驱动契约本身不支持并发会话。
建议：把租约与策略改为按打开句柄计数，或明确在 ABI 文档中声明单会话约束。

### L-2 `_decode_system` 覆盖权威 Usage

`input_mapping.py:161-162`：

```
report_usage = report[1] if len(report) > 1 else 0
parsed_usage = report_usage or (usage if len(report) < 2 else 0)
```

当报告长度 ≥ 2（含 Report ID）时，报告的第二个字节**优先于**调用方传入的 `usage`，而调用方传入的正是驱动 parser 或 Python `_parser_usage_for_event` 解析出的权威值（`mapping_session.py:389-395`）。当前 Power 之所以仍能映射，是因为 `_SYSTEM_BUTTONS = {0x01: "Power"}`（`input_mapping.py:22`）恰好把字节值 `0x01` 当作 Power；同一集合的 Sleep（0x82，字节 0x02）、Wake（0x83，字节 0x04）会被解成未知 Usage 或错键。
建议：报告长度 ≥ 2 时以传入的 `usage`/`usage_page` 优先，仅在调用方未提供时回退报告字节。

### L-3 parser 静默降级（详见 §3.2 断裂 2）

建议：`_prime_hid_parser` 失败时记录失败集合，并在状态中暴露 `parser_ready=False`；`start()` 在 `parser_ready=False` 时要么拒绝进入 `running`，要么在界面明确标注"未命中拦截"。项目文档 `microsoft-hid-review.md` 第 13 节的表述与当前实现需对齐。

### L-4 采集路径先解析后入队

按项目自身 review 的 P1-2：`drop_unmapped` 仍需经过解析分支，未知长度或未知布局的报告可能直接漏报，独立 raw-capture 分支尚未实现。这属于能力限制而非违规，但与"不猜测未知报文"的设计目标相关。

### C-7 ABI 常量不对称

桥接允许 8 个目标集合（`t1bridge_protocol.h:29`），内核集合状态表容量为 32（`t1filter.h:14`）；校验分别用各自常量（`t1filter.c:977`、`985`）。当前不会越界，但两侧常量应对齐或在内核侧显式收紧。

### C-8 统计与队列语义

`T1FilterQueueEvent` 在队列满时丢最旧并置 `STATUS_BUFFER_OVERFLOW`（`t1filter.c:1224-1231`）；`dropped_reports` 直接镜像 `dropped_events`（`:1229`、`:2217`），两个语义不同的计数被混用；`queued_events` 在"入队即淘汰最旧"时仍然自增。诊断与验收数据会因此失真。空轮询 `READ_EVENT` 不会污染 `last_error`（`T1FilterRecordControlError` 只处理两个查询类 IOCTL，`:1346-1351`），这一条经核实无问题。

### A-1 挂载位置（架构，未闭环）

详见 §3.3。代码层面可做的验证：在最新策略与 parser 就绪的前提下，对 COL03 做一次"策略包含/排除 Power"的对照实验，观察系统动作是否随报告清零而消失；若消失，位置可用，若否则需改为 HIDCLASS 之上的 upper filter（官方支持的 kbdhid/mouhid 模型）。

## 5. 已核对为合规的部分

| 检查点 | 位置 | 依据 |
| --- | --- | --- |
| Read 完成例程只使用 `IoStatus`、缓冲区访问到完成为止、API IRQL ≤ DISPATCH | `t1filter.c:1526-1541`、`1785-1856` | `EVT_WDF_REQUEST_COMPLETION_ROUTINE`、`WdfRequestRetrieveOutputBuffer` |
| 转发前设置完成回调，Read / GET_INPUT_REPORT / 其他三类分流且两类缓冲契约不混用 | `t1filter.c:1880-1908` | `Forwarding I/O Requests`、`IOCTL_HID_GET_INPUT_REPORT` |
| 清零 payload 但保留 Report ID | `t1filter.c:1543-1555` | `Obtaining HID Reports` |
| `WdfFdoInitSetFilter` 先于 `WdfDeviceCreate`；声明式 `AddFilter` + `FilterPosition=Lower`；目标平台 ≥ Win10 1903 | `t1filter.c:2522`、`t1filter.inf:32-34,50-51` | `WdfFdoInitSetFilter`、`INF AddFilter Directive` |
| 同步取消六步模板与引用计数 | `t1filter.c:1965-1967`、`610-641` | `Synchronizing Cancellation of Sent Requests` |
| 策略代数 `policy_generation` 防止改写使用过期策略 | `t1filter.c:1285-1288` | 竞态防护，官方无专条 |
| parser-first 且只在 DataIndex/Usage 唯一时映射，多候选与范围长度不一致不猜测 | `t1filter.c:111-123`、`864-897` | `Data Indices`、`HidP_GetData`、`HIDP_BUTTON_CAPS` |
| HID 描述符/preparsed 查询走 PASSIVE_LEVEL 队列 | `t1filter.c:2403-2411` | `WdfIoQueueCreate` ExecutionLevel |
| 桥接层策略校验（size、abi_version、VID/PID、flag、边界、重复与冲突） | `t1bridge.c:18-83` | 防御性 ABI 校验 |

## 6. 修复优先级

| 顺序 | 项 | 理由 |
| --- | --- | --- |
| 1 | C-1 | 可导致 bugcheck / 越权内核访问，改动局部 |
| 2 | C-2 | 本地权限边界，发布前必须收紧 |
| 3 | L-2、L-3、§3.2 断裂 3（位域解码，C 与 Python 同步） | 决定 Power 拦截是否成立，其中位域解码改动最小 |
| 4 | C-3 | DPC 延迟与争用，影响报告吞吐 |
| 5 | C-5、C-4 | 电源状态与设备生命周期边界 |
| 6 | A-1 | 需真机对照实验确认，决定是否需要换拦截层 |
| 7 | C-6、C-7、C-8、L-4 | 契约与可观测性改进 |

## 7. 未闭环的代码级疑问

1. `0x0081` 的系统消费层未知（§3.3）：需要真机对照实验或内核调试，不能用文档推断替代。
2. `T1BRIDGE_FIELD_RULE` 只支持 byte offset + 1/2 字节，无法表达 bit field、数组、多 Report ID；在扩展前不应把字段规则用于 COL05 等新集合。
3. 报告缓冲区上限 64 字节，而本机 COL05 的 `InputReportByteLength` 为 256（`HidP_GetCaps` 证据）；扩展到 COL05 前必须同时改 ABI 与事件缓冲。
4. 采集路径的独立 raw-capture 分支（先存原始报告再解析）尚未实现。
5. Python 与驱动的双份位域/兼容解码逻辑没有共享单一真相源，修正时必须两处同步，否则会再次出现 §3.2 断裂 1 的策略-解码错配。

## 8. 官方依据

- [HID Architecture](https://learn.microsoft.com/en-us/windows-hardware/drivers/hid/hid-architecture)
- [HID Transport Overview](https://learn.microsoft.com/en-us/windows-hardware/drivers/hid/hid-transports)
- [Top-Level Collections](https://learn.microsoft.com/en-us/windows-hardware/drivers/hid/top-level-collections)
- [HIDClass Hardware IDs for Top-Level Collections](https://learn.microsoft.com/en-us/windows-hardware/drivers/hid/hidclass-hardware-ids-for-top-level-collections)
- [Obtaining HID Reports](https://learn.microsoft.com/en-us/windows-hardware/drivers/hid/obtaining-hid-reports)
- [IOCTL_HID_GET_INPUT_REPORT](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/hidclass/ni-hidclass-ioctl_hid_get_input_report)
- [Keyboard and Mouse HID Client Drivers](https://learn.microsoft.com/en-us/windows-hardware/drivers/hid/keyboard-and-mouse-hid-client-drivers)
- [Button Reporting](https://learn.microsoft.com/en-us/windows-hardware/drivers/gpiobtn/button-reporting)
- [Power button action](https://learn.microsoft.com/en-us/windows-hardware/customize/power-settings/power-button-and-lid-settings-power-button-action)
- [Defining I/O Control Codes](https://learn.microsoft.com/en-us/windows-hardware/drivers/kernel/defining-i-o-control-codes)
- [Controlling Device Access](https://learn.microsoft.com/en-us/windows-hardware/drivers/kernel/securing-device-objects)
- [ProbeForRead](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/wdm/nf-wdm-probeforread)
- [Using Neither Buffered Nor Direct I/O](https://learn.microsoft.com/en-us/windows-hardware/drivers/kernel/using-neither-buffered-nor-direct-i-o)
- [Introduction to Spin Locks](https://learn.microsoft.com/en-us/windows-hardware/drivers/kernel/introduction-to-spin-locks)
- [Summary of Framework Objects](https://learn.microsoft.com/en-us/windows-hardware/drivers/wdf/summary-of-framework-objects)
- [WdfDeviceAllocAndQueryProperty](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/wdfdevice/nf-wdfdevice-wdfdeviceallocandqueryproperty)
- [Synchronizing Cancellation of Sent Requests](https://learn.microsoft.com/en-us/windows-hardware/drivers/wdf/synchronizing-cancellation-of-sent-requests)
- [WdfFdoInitSetFilter](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/wdffdo/nf-wdffdo-wdffdoinitsetfilter)
- [INF AddFilter Directive](https://learn.microsoft.com/en-us/windows-hardware/drivers/install/inf-addfilter-directive)
- [Data Indices](https://learn.microsoft.com/en-us/windows-hardware/drivers/hid/data-indices)

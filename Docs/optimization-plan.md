# T1 Remote 代码优化执行计划

程序说明：承接代码逻辑评审，记录首批修改、剩余范围和验收边界。日期：2026-09-12。

## 已执行：内核边界与 Power 链路

| 文件 | 变更 | 验收 |
| --- | --- | --- |
| native/t1filter/t1filter.c | GET 报告拒绝 UserMode；完成回调提前检查；属性内存归属 Device | 源码契约回归；待原生编译 |
| native/t1filter/t1filter.c | 03 01 解码为 0x0081；未知布局不猜测；无 parser 不改写 System Control 位域 | 源码复核；待真机报告验证 |
| t1remote/core/input_mapping.py | Power Usage 统一；零报告释放优先；未知报文保持 unknown | Usage、释放、未知长度/Report ID/多位测试 |
| t1remote/windows/mapping_session.py | START 前准备两个 parser；失败清理；重连重新准备 | 缺接口、查询失败、初始化顺序和重连测试 |

用户发起的 GET_INPUT_REPORT 返回 STATUS_NOT_SUPPORTED。若要支持，须在适当上下文按 IOCTL 契约验证、锁页并管理请求生命周期，不能在 DISPATCH_LEVEL 完成例程补 ProbeForRead。内核请求路径保留。无 parser 的 System Control 重映射不执行，调用方沿用原有失败后清零报告行为。

兼容解码只覆盖已有 Power 样本，不新增 Sleep/Wake；parser 初始化成功不证明所有动态报告均可唯一解析。

## 已执行：权限模型与控制会话归属（2026-09-13，dev 分支）

方案确定为**管理员 App**（不使用服务代理）。

| 文件 | 变更 |
| --- | --- |
| native/t1bridge/t1bridge_protocol.h | 11 个 IOCTL 撤掉 `FILE_ANY_ACCESS`：查询类改 `FILE_READ_DATA`，修改类改 `FILE_WRITE_DATA` |
| native/t1filter/t1filter.c / .h | 控制设备 SDDL 改 `SY/BA 全权 + BU 只读`；按文件对象确定控制会话所有者；所有者关闭等同 STOP；注册 `EvtFileClose` 与文件对象配置 |
| t1remote/windows/driver_bridge.py | 错误码 5 映射为"需要管理员运行或会话被占用"提示 |

行为边界：桥接 DLL 仍以读写方式打开设备，所以主 App 必须管理员运行；普通用户句柄可做只读诊断（状态、能力、事件、描述符、preparsed data），不能修改策略、启停过滤或刷新租约。非所有者调用修改类 IOCTL 返回 `STATUS_ACCESS_DENIED`。

待现场验证：双客户端抢占、异常关闭、无权限、版本不匹配；源码契约测试不能替代真机行为。

## 已执行：配置监视与测试隔离（2026-09-13）

- `mapping_watch` 的签名从 `mtime + size` 改为长度 + SHA-256 内容摘要：Windows 写入时间未刷新时等长重写不再漏检，且不会因仅更新时间戳而误触发重载。
- `test_mapping_session` 补 `parse_input_data` 打桩，避免把伪造 preparsed data 交给原生解析器导致偶发阻塞；相关等待上限统一放宽到 5 秒。

## 已执行：原生编译验证（2026-09-13）

本机安装 WDK 10.0.19041（winget）并把 VSIX 工具集手动释放到 VS2019 BuildTools 实例（`VSIXInstaller` 对 BuildTools 返回 2003，改用手工复制 `WindowsKernelModeDriver10.0` 工具集文件）。随后：

- `MSBuild native/t1filter/t1filter.vcxproj /p:Configuration=Release /p:Platform=x64 /p:SpectreMitigation=false /p:SignMode=Off`：编译 0 警告 0 错误（Level4 + 警告即错误），Inf2Cat 可签名性测试 0 错误 0 警告。
- 桥接 DLL：`cl /LD /O2 /W4 /WX /utf-8 t1bridge.c` 0 警告。
- 未安装 Spectre 缓解库，编译用命令行覆盖关闭；未做签名、安装、设备验证（无证书、无设备、需管理员与重启）。

## 待执行：并发与性能

- C-5：设计请求发送、完成中、已完成的所有权转换，验证取消并发、发送失败、睡眠恢复。不能直接把完成 API 放进锁中。
- C-3：测量锁耗时；为 parser 缓存设计引用保护，为共享解析工作区设计隔离，再缩短全局锁，保留策略代数校验。
- 必须通过 WDK 编译、适用静态检查、Driver Verifier 和设备移除/恢复压力验证；字符串契约测试不能证明内核并发正确。

## 待执行：诊断和设备验收

- C-8：区分累计入队、当前长度、溢出和实际拦截计数，先明确 ABI 兼容策略。
- L-4：设计独立 raw-capture 与策略队列关系，当前不扩展 COL05 或 64 字节上限。
- A-1：新驱动在 parser 就绪后对比策略包含/排除 Power 的系统行为，源码测试不能替代。
- C-7：无需修改容量常量。

## 发布边界

本次不安装依赖或驱动，不修改测试签名或系统配置，不提交或推送。原生修改必须在可用 MSVC/WDK 环境编译并通过设备验收后才能交付 Release。

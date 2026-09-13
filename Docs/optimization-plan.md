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

## 待决：权限和单会话归属

收紧控制设备权限前，需要确定 App 以管理员运行还是引入受限服务代理。前者改动较少但改变启动权限，后者引入服务部署。当前保留 ACL 和 IOCTL 数值。

确定后同步修改桥接 DLL 与驱动，以句柄确定控制会话所有者；仅所有者可修改策略、刷新租约和 STOP。诊断句柄关闭不能停止其他会话。验收覆盖双客户端、异常关闭、无权限和版本不匹配；不能只依赖 Python 单实例锁或只删除 Close 的 STOP。

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

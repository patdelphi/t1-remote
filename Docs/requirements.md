
# T1 Remote Windows 需求基线

## 项目来源

本项目参考：

- `HD838A/remote-mic-app`：macOS 端产品结构、按键手势和语音桥接思路。
- `GetSayAll/remote-mic-app-windows`：Windows 端业务边界和设备生命周期参考，不作为本项目的技术栈基线。

本项目采用 Python + Tkinter，Windows 平台层通过 `pywin32`、`ctypes` 和 WinRT Python bindings 调用 Win32、HID、WinRT GATT 和 WASAPI；需要强设备级拦截时再单独增加 C++/KMDF 驱动。Python 负责首版主应用、Inspector、映射和配置，驱动保持独立生命周期。

参考项目采用 GPL-3.0-only。移植时需要保留来源和许可证边界，不能直接复制品牌资源或未经确认的设备专属协议实现。

## 目标设备

| 项目 | 当前值 |
| --- | --- |
| Windows 名称 | `T1-Remote` |
| BLE HID Service | `0x1812` |
| VID | `0x620A` |
| PID | `0x0407` |
| 已发现 Collection | `COL01`、`COL02`、`COL04`、`COL05` |
| GATT 服务线索 | `AB5E0001-5A21-4F05-BC7D-AF01F617B664` |

## 第一阶段范围：遥控区域报文采集

第一阶段只采集遥控区域的 14 个实体按键：正面 12 个、侧面音量加减 2 个。具体按键含义必须由真实 HID Report 采集确认，未知 Usage 只记录，不自动转换。键盘面 45 个按键、Fn 组合和空中鼠标连续移动暂不纳入本阶段。

遥控区域按键清单：Power、方向上、方向下、方向左、方向右、OK、Return、Voice、Mute、Home、Menu、`Air Mouse`、Volume Plus、Volume Minus。

采集数据分为两层：保留每一条原始 Report 用于协议分析，同时将按下/抬起配对为一条逻辑操作用于界面和后续映射。Power 和 Air Mouse 在采集阶段禁用，不要求触发这两个实体按键。

## 暂不承诺

- T1 语音输入和麦克风输出；
- T1 2.4GHz 接收器或 Wi-Fi 私有协议；
- 内核驱动、虚拟键盘驱动或需要管理员权限的增强拦截；
- 尚未经过 T1 真机验证的完整按键列表。

## 验证边界

本机已确认设备枚举和 Raw Input 路径存在。HID Report Descriptor、14 个遥控区域按键的具体 Usage、Air Mouse 切换后的鼠标报文、语音 GATT 报文格式和映射输出效果仍需 Windows 真机验证。

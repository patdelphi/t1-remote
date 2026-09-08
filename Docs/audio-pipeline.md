# 语音链路软件边界

程序说明：记录 GATT 音频链路已经实现的通用软件组件，以及仍需 T1 真机证据确认的协议边界。

## 已实现

- `t1remote.core.ima_adpcm.ImaAdpcmDecoder`：标准 IMA-DVI ADPCM nibble 解码，支持增量状态、采样值钳位和标准 WAV IMA block 头部。
- `t1remote.core.audio_buffer.PcmFrameQueue`：固定容量、线程安全的 PCM chunk 队列，满载时丢弃最旧 chunk，并提供丢弃计数。
- `t1remote.core.audio_buffer.PcmFormat`：采样率、声道数和采样宽度校验。
- `t1remote.core.gatt_session.GattAudioSession`：连接、服务发现、能力协商、流式接收、排空、断开、错误和旧回调 generation 隔离。

这些模块不依赖 `bleak`、WinRT 或 WASAPI，便于在没有设备协议样本时进行确定性测试。

## 未宣称兼容的部分

- 尚未确认 `AB5E0001` 的音频特征 UUID、通知载荷、能力协商命令、采样率、声道和帧头。
- `ImaAdpcmDecoder` 只实现标准算法，不能证明 T1 使用 IMA-DVI，也不能决定 T1 报文是否包含 block header、Report ID 或自定义前缀。
- 尚未接入真实 GATT transport、CCCD 写入、WASAPI 端点或虚拟麦克风。

## 接入顺序

1. 通过 GATT 适配器发现并记录目标服务和特征 UUID。
2. 保存脱敏的能力协商和通知帧夹具。
3. 根据夹具实现帧解析器，把有效音频样本送入 `ImaAdpcmDecoder` 或明确的其他解码器。
4. 将解码后的 PCM 写入 `PcmFrameQueue`，再接入可选音频端点。
5. 断连时递增 GATT session generation，丢弃旧通知并排空或丢弃队列。

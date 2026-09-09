# 语音链路软件边界

程序说明：记录 GATT 音频链路已经实现的通用软件组件，以及仍需 T1 真机证据确认的协议边界。

## 已实现

- `t1remote.core.ima_adpcm.ImaAdpcmDecoder`：标准 IMA-DVI ADPCM nibble 解码，支持增量状态、采样值钳位和标准 WAV IMA block 头部。
- `t1remote.core.audio_buffer.PcmFrameQueue`：固定容量、线程安全的 PCM chunk 队列，满载时丢弃最旧 chunk，并提供丢弃计数。
- `t1remote.core.audio_buffer.PcmFormat`：采样率、声道数和采样宽度校验。
- `t1remote.core.audio_pipeline.ImaPcmPipeline`：把已剥离私有帧头的 ADPCM 载荷转换为 PCM16LE 并送入队列。
- `t1remote.core.gatt_audio_pipeline.GattAudioProcessor`：把当前 GATT generation 的通知交给可注入帧适配器，再连接到 PCM 管线。
- `t1remote.core.pcm_sink.WaveFilePcmSink`：把 PCM16LE 保存为新的 WAV 文件，供离线听感和波形校验。
- `t1remote.core.sounddevice_sink.SoundDevicePcmSink`：通过可选 `sounddevice` 播放 PCM16LE，供输出端点验证。
- `t1remote.core.sounddevice_sink.WasapiPcmSink`：通过 `sounddevice` 的 WASAPI host 设置播放 PCM16LE，支持共享模式和可选独占模式。
- `t1remote.core.virtual_mic.VirtualMicrophonePcmSink`：将 PCM 写入用户明确选择的虚拟音频线输入端点，供 Codex、微信输入法等应用从对应录音端读取。
- `t1remote.core.pcm_output.PcmSinkWorker`：后台消费有界 PCM 队列，写入 WAV、WASAPI 或 sounddevice 端点，并在输出失败时关闭队列和报告错误。
- `tools.t1_audio_devices`：只读枚举输出端点和输入端点，不连接设备。
- `t1remote.core.gatt_session.GattAudioSession`：连接、服务发现、能力协商、流式接收、排空、断开、错误和旧回调 generation 隔离。
- `t1remote.windows.gatt.BleakGattAdapter`：可选 Bleak 传输边界和只读服务/特征摘要；`tools.t1_gatt_probe` 不执行特征写入。
- `t1remote.windows.gatt_audio.GattAudioController`：串联传输、目标服务发现、通知订阅和 PCM 管线；开始流式接收前要求调用方显式确认私有协议协商。
- `tools.t1_gatt_capture`：在显式指定 notify 特征后采集限定时长的脱敏通知帧夹具；不发送私有协商命令，也不覆盖已有输出文件。
- `t1remote.core.atvv_protocol`：加入公开 ATVV UUID、能力响应、命令编码、控制信号和 v0.4 IMA-DVI 音频帧解析。
- `t1remote.core.atvv_audio.AtvvV04AudioProcessor`：把 BLE 分片重组为完整音频帧，按帧初始化 ADPCM 状态，并将 PCM16LE 推入现有队列。

核心解码、队列和状态机不依赖 `bleak`、WinRT 或 WASAPI，便于在没有设备协议样本时进行确定性测试。

GATT 探测命令需要显式安装项目的可选 `ble` extra；当前开发环境没有自动安装该依赖。
音频端点枚举和播放需要项目的可选 `audio` extra；当前开发环境没有自动安装该依赖。

## 未宣称兼容的部分

- 项目已有 `AB5E0001` 服务记录，但尚未确认 T1 是否提供标准的 `AB5E0002/0003/0004` 特征组合，以及是否接受对应能力协商命令。
- 当前 ATVV 适配器覆盖公开 v0.4 的 8 kHz、134 字节帧格式；v1.0 的能力位、帧长和同步方式仍保持独立，不能混用。
- `ImaAdpcmDecoder` 只实现标准算法，不能证明 T1 使用 IMA-DVI，也不能决定 T1 报文是否包含 block header、Report ID 或自定义前缀。
- 尚未接入真实 GATT transport、CCCD 写入或虚拟麦克风；`WasapiPcmSink` 只覆盖现有输出端点。
- `VirtualMicrophonePcmSink` 只路由到已安装的虚拟音频线，不安装或创建 Windows 音频驱动；没有 VB-CABLE、VoiceMeeter 等输入端点时，不能让任意应用看到本项目的语音。

## 接入顺序

1. 通过 GATT 适配器发现并记录目标服务和特征 UUID。
2. 使用 `python -m tools.t1_gatt_capture <address> --characteristic <uuid> --output <path>` 保存脱敏通知帧夹具；能力协商命令仍需按设备版本确认。
3. 对已确认的 ATVV v0.4 设备使用 `AtvvAudioFrameAssembler` 和 `AtvvAudioFrame`；其他版本必须单独实现帧解析器。
4. 将解码后的 PCM 写入 `PcmFrameQueue`，再接入可选音频端点。
5. 断连时递增 GATT session generation，丢弃旧通知并排空或丢弃队列。

## 任意应用的麦克风路由

使用现有虚拟音频驱动时，路由方向必须成对理解：

1. 本程序把 PCM 写入虚拟线的播放端，例如 `CABLE Input`。
2. 目标应用把麦克风选择为同一虚拟线的录音端，例如 `CABLE Output`。
3. `python -m tools.t1_audio_devices` 可以同时查看两类端点；本程序不会默认选择真实扬声器，也不会修改目标应用的麦克风设置。

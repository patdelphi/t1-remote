# 语音链路软件边界

程序说明：记录 GATT 音频链路已经实现的通用软件组件，以及仍需 T1 真机证据确认的协议边界。

## 已实现

- `t1remote.core.ima_adpcm.ImaAdpcmDecoder`：标准 IMA-DVI ADPCM nibble 解码，支持增量状态、采样值钳位和标准 WAV IMA block 头部。
- `t1remote.core.audio_buffer.PcmFrameQueue`：固定容量、线程安全的 PCM chunk 队列，满载时丢弃最旧 chunk，并提供丢弃计数。
- `t1remote.core.audio_buffer.PcmFormat`：采样率、声道数和采样宽度校验。
- `t1remote.core.audio_pipeline.ImaPcmPipeline`：把已剥离私有帧头的 ADPCM 载荷转换为 PCM16LE 并送入队列。
- `t1remote.core.gatt_audio_pipeline.GattAudioProcessor`：把当前 GATT generation 的通知交给可注入帧适配器，再连接到 PCM 管线。
- `t1remote.core.pcm_sink.WaveFilePcmSink`：把 PCM16LE 保存为 WAV 文件，默认不覆盖已有文件，供离线听感和波形校验。
- `t1remote.core.pcm_sink.RecordingPcmSink`：把原始 16 kHz PCM 同时转发到 CABLE Input 并保存语音测试录音。
- `t1remote.core.pcm_sink.PcmWaveformSink`：从 PCM 输出泵提取归一化波形点，供前台实时诊断音频是否到达。
- `t1remote.core.voice_replay.play_wav_file`：在后台回放语音测试生成的 WAV，默认使用真实音频输出端点，不回写 CABLE Input。
- `t1remote.core.sounddevice_sink.SoundDevicePcmSink`：通过可选 `sounddevice` 播放 PCM16LE，供输出端点验证。
- `t1remote.core.sounddevice_sink.WasapiPcmSink`：通过 `sounddevice` 的 WASAPI host 设置播放 PCM16LE，支持共享模式和可选独占模式。
- `t1remote.core.virtual_mic.VirtualMicrophonePcmSink`：将 PCM 写入用户明确选择的虚拟音频线输入端点，供 Codex、微信输入法等应用从对应录音端读取。
- `t1remote.core.pcm_output.PcmSinkWorker`：后台消费有界 PCM 队列，写入 WAV、WASAPI 或 sounddevice 端点，并在输出失败时关闭队列和报告错误。
- `t1remote.windows.voice_session.VoiceSessionController`：在后台线程管理真实语音会话，支持 GUI 启动、停止和状态回调。
- `tools.t1_audio_devices`：只读枚举输出端点和输入端点，不连接设备。
- `t1remote.core.gatt_session.GattAudioSession`：连接、服务发现、能力协商、流式接收、排空、断开、错误和旧回调 generation 隔离。
- `t1remote.windows.gatt.BleakGattAdapter`：可选 Bleak 传输边界和只读服务/特征摘要；`tools.t1_gatt_probe` 不执行特征写入。
- `t1remote.windows.gatt_audio.GattAudioController`：串联传输、目标服务发现、通知订阅和 PCM 管线；开始流式接收前要求调用方显式确认私有协议协商。
- `tools.t1_gatt_capture`：在显式指定 notify 特征后采集限定时长的脱敏通知帧夹具；不发送私有协商命令，也不覆盖已有输出文件。
- `tools.t1_voice_test`：连接真实 T1，完成 ATVV 能力协商、`MIC_OPEN`、ADPCM 解码、PCM 重采样和 VB-CABLE 输出；`--duration 0` 表示持续收音，Ctrl+C 只设置停止事件，退出时按顺序关闭麦克风、断开 GATT、排空 PCM 输出。
- `tools.t1_app` 的“语音测试”页：启动时后台扫描 BLE 设备，并在广播扫描为空时回退读取 Windows 缓存的 T1-Remote；识别到 T1/Remote 后预填地址，也可以手动扫描并选择设备，再启动或停止真实语音会话、查看实时波形并播放最近一次录音；「持续收音（不限时长）」开关把会话时长置为 0，麦克风一直打开直到点击停止，期间状态显示“持续收音中”。
- `t1remote.core.atvv_protocol`：加入公开 ATVV UUID、能力响应、命令编码、控制信号和 v0.4 IMA-DVI 音频帧解析。
- `t1remote.core.atvv_audio.AtvvV04AudioProcessor`：把 BLE 分片重组为完整音频帧，按帧初始化 ADPCM 状态，并将 PCM16LE 推入现有队列。

核心解码、队列和状态机不依赖 `bleak`、WinRT 或 WASAPI，便于在没有设备协议样本时进行确定性测试。

GATT 探测和真实语音测试需要显式安装项目的可选 `ble` extra；当前开发环境已安装 `bleak`。
音频端点枚举和播放需要项目的可选 `audio` extra；当前开发环境没有自动安装该依赖。

## T1 实机验证结果

- T1 提供 `AB5E0001/0002/0003/0004`，并接受 `0A 00 01 00 01` 能力查询。
- 实机能力响应为 `0B 00 04 00 02 00 86 00 86`：v0.4、T1 使用的 ADPCM codec 标识为 `0x0002`、帧长和通知载荷均为 134 字节。
- T1 的 `codec=0x0002` 对应 16 kHz；录音 WAV 和实时 PCM 输出均使用 16 kHz，写入 CABLE Input 前再重采样到 48 kHz。
- `MIC_OPEN` 实机命令为 `0C 00 02`；控制通知返回 `04` 后，音频特征持续发送 134 字节帧，`0D` 后返回 `00 00`。
- 5 秒真实会话收到 219 个音频块；CABLE Output 同步采样峰值为 0.56284、RMS 为 0.38721，证明已从 `CABLE Input` 传到麦克风端。
- Windows 配对设备当前可能不广播；`BleakGattAdapter` 对 12 位 MAC 地址构造 `BLEDevice`，允许 WinRT 使用配对缓存直接建立 GATT 会话。
- `VirtualMicrophonePcmSink` 只路由到已安装的虚拟音频线，不安装或创建 Windows 音频驱动；没有 VB-CABLE、VoiceMeeter 等输入端点时，不能让任意应用看到本项目的语音。

## 接入顺序

1. 通过 GATT 适配器发现并记录目标服务和特征 UUID。
2. 使用 `python -m tools.t1_gatt_probe <address>` 做只读服务发现。
3. 对已确认的 ATVV v0.4 设备使用 `python -m tools.t1_voice_test <address>` 完成真实语音测试。
4. `AtvvAudioFrameAssembler` 将 134 字节通知重组后交给 IMA-DVI 解码器，再写入 `PcmFrameQueue` 和虚拟音频线。
5. 断连时递增 GATT session generation，丢弃旧通知并排空或丢弃队列。

## 任意应用的麦克风路由

使用现有虚拟音频驱动时，路由方向必须成对理解：

1. 本程序把 PCM 写入虚拟线的播放端，例如 `CABLE Input`。
2. 目标应用把麦克风选择为同一虚拟线的录音端，例如 `CABLE Output`。
3. `python -m tools.t1_audio_devices` 可以同时查看两类端点；本程序不会默认选择真实扬声器，也不会修改目标应用的麦克风设置。

长时间给其他应用供声时使用持续收音：

1. 前台“语音测试”页勾选「持续收音（不限时长）」，或命令行执行 `python -m tools.t1_voice_test <地址> --duration 0`。
2. 该模式下 `MIC_OPEN` 后音频流连续，不依赖按住 Voice 键；`CABLE Input` 会一直有 PCM，目标应用只需把麦克风设为 `CABLE Output`。
3. 结束时点击“停止语音测试”（CLI 按 Ctrl+C），会话按固定顺序关闭麦克风、断开 GATT 并排空 PCM 输出；停止前其他应用的麦克风会一直收到 T1 语音。

# Research

存放尚未转化为工程决策的外部调研材料。

每份硬件参考调研至少应包含：

- 项目名称和原始链接。
- 主要功能、主控和关键器件。
- 原理图、PCB、BOM、固件和结构文件的完整性。
- 开源许可证及商用限制。
- 是否有实物验证、版本记录和社区反馈。
- 可复用部分、不可复用部分和技术风险。

## Software Research

- [`2026-08-14-local-voice-stack.md`](2026-08-14-local-voice-stack.md) — Linux
  本地语音栈与 Voice Gateway 调研。
- [`2026-08-17-agent-io-design-lessons.md`](2026-08-17-agent-io-design-lessons.md)
  — 公开 Agent I/O 项目的流式协议、adapter、fixture、可观测性和可靠性经验。
- [`2026-08-18-pipewire-preflight.md`](2026-08-18-pipewire-preflight.md) — 本机
  PipeWire 工具、默认设备和 Provider 实现前置约束。
- [`2026-08-18-esp32-s3-audio-transport.md`](2026-08-18-esp32-s3-audio-transport.md)
  — ESP32-S3 的 Wi-Fi/BLE 控制分工、Opus 参数建议和本地 USB 音频路径。
- [`2026-08-18-vad-candidates-and-first-benchmark.md`](2026-08-18-vad-candidates-and-first-benchmark.md)
  — WebRTC 与 Silero ONNX 的候选依据、外部音频清单和首轮真实观测。
- [`2026-08-18-paraformer-first-benchmark.md`](2026-08-18-paraformer-first-benchmark.md)
  — Paraformer 流式中文/英文样本、首个 partial 估算、资源占用和准确率边界。
- [`2026-08-18-piper-kokoro-first-benchmark.md`](2026-08-18-piper-kokoro-first-benchmark.md)
  — Piper 与 Kokoro 的首轮延迟、资源、中断、许可和 ASR 可懂度代理对比。
- [`2026-08-19-local-audio-diagnostics.md`](2026-08-19-local-audio-diagnostics.md)
  — PipeWire 默认/手工设备选择、隐私安全实机输入输出诊断与启动边界。
- [`2026-08-19-local-voice-runtime-and-live-path.md`](2026-08-19-local-voice-runtime-and-live-path.md)
  — Paraformer/Piper 统一运行时、容器 PipeWire 边界与真实 PTT-ASR-TTS
  播放链路测量。
- [`2026-08-19-controlled-live-asr-diagnostic.md`](2026-08-19-controlled-live-asr-diagnostic.md)
  — 公开短语麦克风/ASR 诊断契约、两次未说话负对照，以及首轮用户确认
  朗读后的 VAD 活动、关键词、CER、延迟与同步证据。
- [`2026-08-19-sensevoice-second-asr-baseline.md`](2026-08-19-sensevoice-second-asr-baseline.md)
  — SenseVoice/sherpa-onnx 与 whisper.cpp 选择、不可变制品、许可边界和
  首轮公开音频对比。

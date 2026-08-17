# Handoff

日期：2026-08-14

项目：`agent-io` / `next_keyboard`

状态：研究和架构交接，尚未开始 Voice Gateway 实现。

## 先读这些

1. [本地 ASR/TTS 调研](docs/research/2026-08-14-local-voice-stack.md)
2. [多模态 Agent Console 架构](docs/architecture/multimodal-agent-console.md)
3. [Phase 0 软件验证](docs/architecture/phase-0.md)
4. [Agent Console 竞品和硬件调研](docs/research/2026-07-16-agent-macropad-landscape.md)
5. [当前 Bridge README](bridge/README.md)

## 已确认的方向

`next_keyboard` 不再只按“键盘”理解，而是 Agent Console 的硬件和 Bridge 基础。语音终端是它的第二种输入输出面：

```text
agent-io
├── Agent Core Bridge
├── Voice Gateway
└── Physical Surface
```

职责边界：

- Bridge：Agent adapter、状态、会话、目标解析和控制命令。
- Voice Gateway：PipeWire、VAD、ASR、提示词处理、TTS、播报和语音通知。
- Physical Surface：HID、按键、旋钮、RGB、屏幕和高确定性的控制动作。

## 当前技术建议

### ASR

- 实时输入：FunASR Paraformer + VAD。
- 最终校正：Fun-ASR-Nano 或 faster-whisper，按固定语料实测选择。
- CPU/低依赖备选：SenseVoiceSmall 或 sherpa-onnx。

### TTS

- 通知：Piper 或 Kokoro。
- Agent 长回答：CosyVoice 3 0.5B。
- 声音克隆实验：VoxCPM 1.5，暂不把 VoxCPM 2 作为常驻默认。

### Agent

- 首版语音输入：`codex exec --json`。
- 长期会话：之后评估 Codex app-server。
- Agent 状态：Codex hooks 和 `notify` 写入 Bridge。
- MCP：作为 Agent 调用 `speak`、`notify`、`stop_speaking` 的附加接口。

## 协议边界

保持当前 `AgentEvent v1` 作为硬件和状态面板的最小状态协议，不把完整提示词、代码、音频或 Agent 回答塞入其中。

后续新增：

- `StateEvent`：硬件和状态投影。
- `InteractionEvent`：语音、TUI、桌面客户端使用的富文本事件。
- `ControlCommand`：中断、批准、拒绝、聚焦、发送提示词和停止播报。

Agent 身份使用 `agent_id + session_id + project_id`。`slot` 只是展示层映射，由 `SessionRegistry` 动态分配。

## 下一步顺序

### 1. 固化 Phase 0

- 给当前项目创建第一个基线 commit。
- 记录现有协议和 Codex hook 行为。
- 保持当前零运行依赖 Bridge 可独立工作。

### 2. 重构 Bridge 内部边界

- 把 `SlotPanel` 与状态存储分开。
- 增加 `StateStore`。
- 增加本地订阅或发布接口。
- 增加 `SessionRegistry`，不要让 `slot` 充当 Agent 身份。

### 3. 写 Voice Gateway POC

首版只完成：

```text
PTT -> PipeWire -> VAD -> Paraformer -> codex exec --json -> Piper
```

先不做：

- 常驻唤醒词。
- 自动提示词优化。
- 自动批准命令。
- 硬件依赖。
- 多个大模型同时常驻 GPU。

### 4. 接入 InteractionEvent

- 保留 raw transcript。
- 增加 normalized transcript。
- 优化模式下才生成 optimized prompt。
- 将 Agent 输出映射成可取消的 TTS 队列。
- 用户开始新的 PTT 时中断当前播报。

### 5. 再接实体设备

优先考虑：

- 语音输入键。
- 停止播报/中断键。
- 当前 Agent 选择键。
- 批准/拒绝的长按或组合键。

审批动作必须绑定目标 Agent、会话、`request_id`、命令摘要和有效期。

## 当前不要做的事

- 不要把 PyTorch、CUDA、模型权重加入核心 Bridge 的依赖。
- 不要直接把 ASR/TTS 的富文本事件发送给硬件。
- 不要使用剪贴板或 Wayland 键盘模拟作为主要 Agent 输入通道。
- 不要把 Codex hook 当作语音输入 API；它主要是 Agent 到外部系统的事件通道。
- 不要在协议和状态机稳定前定型复杂外观、屏幕和无线硬件。

## 验证命令

当前 Phase 0 Bridge：

```bash
PYTHONPATH=bridge python3 -m agent_io_bridge bridge --plain
PYTHONPATH=bridge python3 -m agent_io_bridge simulate
PYTHONPATH=bridge python3 -m unittest discover -s tests -v
```

提交前至少执行：

```bash
git diff --check
find docs -name '*.md' -type f
```

Voice Gateway 完成后需要增加固定语料和以下指标：首个 partial 延迟、final 延迟、关键字准确率、TTS 首包延迟、可打断时间、设备断开恢复和 Agent 退出恢复。

## 已知限制

- 当前项目尚无 commit，所有文件都属于未提交的 Phase 0 工作树。
- 当前 Bridge 是顺序处理连接的最小原型，接入持续事件流前需要重新评估并发模型。
- 当前 `AgentEvent` 是状态快照，不足以表达富文本、审批详情和语音控制。
- 模型质量、显存占用、中文专有名词准确率和 TTS 首包延迟尚未在本机完成基准测试。
- 语音模型和 voice 文件的许可证必须在部署前逐项复核。

## 完成标准

本阶段交接完成的判断标准：

- 研究和综合架构已经进入项目文档。
- Phase 0 Bridge 仍可独立运行和测试。
- 新协议不会破坏现有 `AgentEvent v1`。
- Voice Gateway 可以在没有硬件的情况下完成 PTT、识别、Agent 调用和播报。
- 实体设备只消费状态投影和安全控制命令。

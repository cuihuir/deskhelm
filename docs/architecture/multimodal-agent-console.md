# Multimodal Agent Console

日期：2026-08-14

状态：架构草案，待拆分为正式 ADR。

## 1. 目标

DeskHelm 不应只是一块宏键盘，也不应只是一套语音输入法。它应成为一个本地优先的 Agent Console：

- Bridge 统一不同 Agent 的状态、会话和控制语义。
- Voice Gateway 提供本地语音输入、提示词处理、播报和通知。
- Physical Surface 提供按键、旋钮、RGB、屏幕和高确定性的控制动作。
- TUI 和桌面客户端可以复用同一套本地协议。

三种交互方式的分工：

```text
语音       高带宽输入输出、长描述、结果播报
实体控制   低延迟、强确认、停止/批准/拒绝/切换
屏幕与 TUI 详细文本、diff、命令和完整上下文
```

## 2. 推荐系统边界

```text
Codex / Claude / Gemini / OpenCode
              |
        Agent Adapters
              |
        Agent Core Bridge
       /        |         \
 StateStore  SessionRegistry  ControlRouter
      |             |              |
 Device Projection  Voice Gateway  TUI/API
      |                              |
 Physical Surface               ASR/TTS/Prompt
```

### Agent Core Bridge

负责：

- Agent adapter 生命周期。
- Agent、项目、会话和槽位的映射。
- 状态快照和订阅。
- 控制命令路由。
- 权限和目标确认。

不负责：

- ASR/TTS 模型推理。
- 硬件灯效细节。
- 解析某个 Agent 的彩色终端文本。

### Voice Gateway

负责：

- PipeWire 采集和设备恢复。
- PTT、VAD、实时 ASR 和最终转写。
- 原文、规范化文本和优化提示词的生命周期。
- Agent 输出分句、TTS、播放、取消和通知。

不负责：

- 直接维护硬件槽位。
- 绕过 Bridge 直接批准命令。
- 绑定 Codex 的内部协议。

### Physical Surface

负责：

- 标准 HID 输入。
- RGB、屏幕、蜂鸣器等状态投影。
- 选择、停止、批准、拒绝和语音输入等实体动作。

设备不接收完整提示词、代码或音频，默认只接收最小化状态和安全的展示摘要。

## 3. 协议留口子

当前 `AgentEvent v1` 是状态投影协议，应保持兼容：

```json
{
  "protocol_version": 1,
  "agent_id": "project-a:codex:1",
  "slot": 0,
  "state": "waiting_approval",
  "label": "backend",
  "progress": null,
  "updated_at": 1784210000000
}
```

不要把语音内容直接加入这个状态事件。建议新增两类协议，具体字段进入 ADR 前仍可调整。

### InteractionEvent

面向 Voice Gateway、TUI 和桌面客户端，允许携带文本但不默认下发到硬件：

```json
{
  "protocol_version": 1,
  "kind": "assistant_message",
  "agent_id": "project-a:codex:1",
  "session_id": "session-42",
  "project_id": "project-a",
  "text": "任务已经完成",
  "speak": true,
  "priority": "normal",
  "interruptible": true
}
```

建议的 `kind` 包括：

- `assistant_message`
- `approval_request`
- `user_input_required`
- `task_completed`
- `task_failed`
- `speech_started`
- `speech_stopped`

### ControlCommand

面向目标 Agent 或 Voice Gateway：

- `interrupt`
- `approve`
- `reject`
- `focus`
- `submit_prompt`
- `speak`
- `stop_speaking`
- `mute`

审批类命令必须包含 `request_id`、目标会话、命令摘要和有效期。不能把“按键/语音动作”直接映射成无目标的全局批准。

## 4. Agent 接入策略

### 语音到 Agent

首版优先支持由 Voice Gateway 自己管理的 Codex 会话：

```text
voice transcript
  -> codex exec --json
  -> JSONL event stream
  -> InteractionEvent
```

长期会话可以接入 Codex app-server，但它当前是实验性接口，需要按本机 CLI 版本验证。

### Agent 到语音和设备

- `codex exec --json`：适合语音终端拥有的会话和流式结果。
- `hooks.json`：适合把生命周期事件推送到 Bridge。
- `notify`：适合轻量通知通道。
- MCP：可作为 Agent 主动调用 `speak`、`notify`、`stop_speaking` 的附加工具。

hooks 是 Agent 到外部系统的事件通道，不应被当作稳定的语音输入注入 API。

## 5. 会话和槽位

Agent 身份不应只依赖 `slot`。推荐使用：

```text
agent_id + session_id + project_id
```

`slot` 是展示层映射，由 `SessionRegistry` 动态分配。这样语音终端可以操作当前会话，硬件仍可以展示四个或八个槽位，二者不互相绑定。

## 6. 实体交互和安全

一个典型审批流程：

```text
Agent 请求权限
  -> Bridge 生成 approval_request
  -> RGB/屏幕显示等待批准
  -> Voice Gateway 播报目标和命令摘要
  -> 用户按住确认键或明确语音确认
  -> Bridge 校验 request_id 和有效期
  -> Adapter 执行 approve/reject
```

语音播报只提供上下文，默认不执行批准。硬件批准键应避免误触，最好使用长按、组合键或二次确认。

## 7. 推荐目录边界

```text
deskhelm/
├── bridge/       Agent Core、StateStore、SessionRegistry、ControlRouter
├── adapters/     Codex、Claude、Gemini、OpenCode
├── protocol/     状态、交互、控制和设备传输规范
├── voice/        Voice Gateway 与 ASR/TTS provider 接口
├── hardware/     PCB、结构、制造资料
├── firmware/     HID、CDC、RGB 和设备协议
├── configurator/ 设备配置和可视化
└── tests/        协议、Bridge、adapter、voice 和硬件联调测试
```

模型权重和大体积运行时不进入 Git；`voice/` 只保留 provider、配置、下载、启动和测试代码。

## 8. 演进顺序

1. 固化当前 Phase 0 的 `AgentEvent v1` 和 Codex hook 行为。
2. 将 Bridge 的显示逻辑拆成 `StateStore` 和订阅机制。
3. 定义 `InteractionEvent` 和 `ControlCommand` 的最小版本。
4. 在没有硬件的情况下完成 Voice Gateway PTT、ASR、TTS 和 `codex exec --json` POC。
5. 将 Voice Gateway 接入 Bridge，并验证状态、播报和中断行为。
6. 再接入实体语音键、停止键、Agent 选择键和批准/拒绝控制。

当前不应优先做：

- 将所有 ASR/TTS 模型依赖塞进 Bridge。
- 直接解析彩色终端文本作为主适配方式。
- 一开始就实现自动批准。
- 在协议未稳定前设计复杂屏幕和硬件外观。

## 9. 待决策事项

- `InteractionEvent` 是否与 `ControlCommand` 共用 Unix socket，还是使用独立 socket。
- Voice Gateway 使用 Python 作为首版实现，还是 Bridge 与 Voice 分别使用 Python/Rust。
- `session_id`、`project_id` 的来源和生命周期。
- 硬件设备如何发现、认证和协商协议版本。
- 审批命令的 UI、长按时长和过期策略。

这些事项进入实现前应分别记录 ADR。

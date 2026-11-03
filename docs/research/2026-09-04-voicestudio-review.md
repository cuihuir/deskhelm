# VoiceStudio 项目调研

调研日期：2026-09-04

状态：研究输入，作为 DeskHelm 后续 ASR/TTS 选型、部署和应用改进的参考；
本次不引入 VoiceStudio 代码、依赖、模型或运行时。

原始项目：<https://github.com/debpalash/VoiceStudio>

## 1. 调研范围

本次重点查看了 VoiceStudio 的：

- ASR/TTS 引擎清单及其适用场景。
- 本地语音平台、流式 WebSocket 和原生控制面的边界。
- 模型下载、运行时隔离、sidecar、缓存和资源回收策略。
- Docker、远程 GPU worker 和本地部署方式。
- 应用许可证、模型许可证和商用部署风险。

## 2. 项目概况

VoiceStudio 不是单一语音模型，而是一个本地语音工作台和服务平台：

```text
Tauri desktop shell
    -> React/Vite UI
    -> FastAPI backend
        -> ASR/TTS engine registry
        -> batch / dubbing / streaming pipelines
        -> OpenAI-compatible API and MCP
        -> SQLite and model catalogue
```

截至调研时，公开仓库主分支约有 1,907 个提交、16,306 个 stars，应用代码
许可证为 AGPL-3.0。其默认产品目标包含听写、配音、声音克隆和本地/远程
语音服务，范围明显大于 DeskHelm 当前的 PTT、ASR、通知 TTS 和 Agent 控制链路。

## 3. ASR 选型观察

### 3.1 最有价值的候选：Sherpa-ONNX 双语流式模型

VoiceStudio 将 Sherpa-ONNX 定位为 CPU live dictation 引擎，并列出：

| 模型 | 类型 | 语言 | 下载大小 |
| --- | --- | --- | --- |
| `sherpa-paraformer-bilingual-zh-en` | streaming | 中文 + 英文 | 约 0.24 GB |
| `sherpa-zipformer-bilingual-zh-en` | streaming | 中文 + 英文 | 约 0.20 GB |
| `sherpa-zipformer-zh-14m` | streaming | 中文 | 约 0.025 GB |
| `sherpa-zipformer-en-20m` | streaming | 英文 | 约 0.044 GB |

这些模型体积小、CPU 可运行，并且可以逐帧输出 partial，符合 DeskHelm
未来降低 Paraformer PyTorch 运行时成本、改善中英混合 PTT 体验的方向。

但这只是候选发现，不是质量结论。VoiceStudio 的公开 benchmark 页面在调研时
没有已验证的引擎/硬件结果，因此不能用其文档中的延迟或实时倍速直接替代
DeskHelm 自己的中英混合编码语料评估。

### 3.2 FunASR / SenseVoice 的定位

VoiceStudio 将 FunASR/SenseVoice 描述为：

- 50+ 语言、标点和 inverse text normalization。
- 可选的 cam++ 内置说话人标注。
- 句子级时间戳，而不是 lip-sync 所需的高精度词级时间戳。
- 明确 opt-in，不由自动选择逻辑隐式启用。

这与 DeskHelm 当前将 SenseVoice 作为 final-only、显式选择的候选基本一致。
它不应被宣传为真正的实时 partial ASR，除非采用实际的流式模型和流式推理路径。

### 3.3 Whisper 路线

VoiceStudio 的 WhisperX/Faster-Whisper 主要面向批量转写、字幕、配音和词级
时间戳。其文档也记录了 CTranslate2、cuDNN、显存不足、CPU fallback 和
进程崩溃等运维问题。

对 DeskHelm 的启示是：Whisper 仍适合作为最终转写或通用 fallback，但不应
为了获得 PTT partial 而把批量转写引擎包装成“实时流式”。

## 4. TTS 选型观察

### 4.1 不建议直接采用 OmniVoice 默认路线

VoiceStudio 默认的 OmniVoice 主打 600+ 语言、零样本克隆和声音设计，文档称
专用 GPU 约需要 6 GB 以上显存。它更适合语音工作台的高质量克隆/配音，不适合
DeskHelm 当前的短通知默认路径，原因包括：

- 与本机 8 GiB GPU 上的 ASR、IndexTTS 和其他任务存在显存竞争。
- 默认权重标注为 CC-BY-NC，音频 tokenizer 还有独立的社区条款。
- VoiceStudio 应用本身是 AGPL-3.0，不能把其实现代码直接复制进 DeskHelm。

### 4.2 PocketTTS 对中文 DeskHelm 帮助有限

PocketTTS 约 100M 参数，CPU-only，支持零样本克隆和低延迟合成，但当前文档
列出的语言是英语、法语、德语、葡萄牙语、意大利语和西班牙语，不覆盖中文。
其 Hugging Face 权重还需要 gated access 和额外使用协议。

因此它可以作为英语 CPU 语音的外部参考，但不应成为 DeskHelm 中文通知 TTS
候选。

### 4.3 Sherpa-ONNX TTS 是运行时方向，不是现成音色方案

Sherpa-ONNX 可以用统一 ONNX runtime 承载 VITS、MeloTTS、Piper、Kokoro、
Matcha 等多类 TTS 模型，具备跨平台和 CPU 友好的优点。但它本身不提供统一
的中文音色质量，也不提供声音克隆；具体效果完全取决于模型和音色文件。

它适合作为未来降低 TTS 运行时复杂度的研究方向，不足以推翻 DeskHelm 已有的：

- Piper：低延迟通知基线。
- Kokoro：质量导向候选。
- IndexTTS-2.5：外部 sidecar、零样本克隆和 opt-in 实验。

### 4.4 CosyVoice / IndexTTS 的部署经验

VoiceStudio 文档明确区分“模型已经下载”和“当前 Python 运行时能够导入并
执行引擎”。CosyVoice 在共享后端环境中容易产生依赖冲突，打包版本也不提供
完整的一键运行时安装路径。

这支持 DeskHelm 将 IndexTTS 放在独立运行时和外部进程中，而不是把大型 TTS
依赖加入核心 Bridge 或共用的轻量运行时。

## 5. 可复用的部署经验

### 5.1 Lazy load、warm model 和显式卸载

VoiceStudio 的模型通常在第一次实际请求时加载，并在后续请求中保持 warm；
同时提供 idle timeout、模型卸载、显存预检查和单引擎常驻策略。

DeskHelm 可以继续沿用以下原则：

- Bridge 启动不导入或加载大型 ASR/TTS 模型。
- 用语音请求作为模型运行时的加载边界。
- 对 8 GiB GPU 限制同时常驻和并发运行的模型数量。
- ASR/TTS 出现显存竞争时，优先显式卸载或切换到 CPU，而不是隐式重试。

### 5.2 Crash-isolated sidecar

VoiceStudio 为 PocketTTS、OmniVoice 和部分 ASR 提供独立子进程路径，原因是
模型推理可能卡死、native runtime 可能崩溃，进程内异常无法可靠回收显存。
其 sidecar 具备：

- 长度前缀的有界消息帧。
- lazy import 和 ready handshake。
- 推理期间 heartbeat，避免健康任务被 watchdog 误杀。
- 单次请求、输出、队列和接收超时。
- 崩溃后仅重启当前引擎，而不是整个后端。

DeskHelm 当前的 IndexTTS 外部 wrapper 已采用类似方向。未来如果 Paraformer、
Qwen3-ASR 或其他 native/torch 引擎出现阻塞、崩溃或依赖冲突，应优先增加
ASR sidecar，而不是扩大 Bridge 的异常处理范围。

### 5.3 模型制品与运行时可用性分离

VoiceStudio 的 Model Catalogue 会分别展示权重下载状态、依赖安装状态、平台
可用性和实际 engine readiness。这一分离值得加入 DeskHelm 后续配置工具：

```text
model artifact present
    != optional runtime installed
    != provider importable
    != selected device usable
    != real request verified
```

但 DeskHelm 仍应保持“外部模型路径显式配置、启动前预检、缺失时 fail closed”，
不应直接采用 VoiceStudio 的首次使用自动下载作为默认行为。

## 6. 可复用的应用和协议经验

VoiceStudio 将本地语音拆成两个边界：

```text
capture / audio data plane
    -> PCM or WebSocket stream
    -> partial and final transcript

native control plane
    -> start / stop / toggle
    -> focused target reservation
    -> final text insertion
```

其数据面提供 OpenAI-compatible HTTP、版本化 WebSocket 和 MCP；原生控制面单独
负责麦克风启停、目标窗口和文本插入。一个 output session 只能持有一个 focused
target，过期或陈旧的 session ID 会被拒绝。

这与 DeskHelm 当前的 session-targeted controls、PTT activation ID、PipeWire
capture 和 Agent project/session 路由高度同向。后续 GUI 或外部客户端可以借鉴：

- 为 ASR stream 定义明确的 `session_id`、`partial`、`final` 和终止消息。
- 将“谁拥有麦克风”和“最终文本写入哪个 Agent/session”分开。
- 通过 capability discovery 暴露可用的 ASR/TTS 能力，而不是硬编码引擎名称。
- 将目标绑定、过期、取消和插入权限放在控制边界，不放进模型 provider。

当前不建议把 3900/3902 HTTP 服务或 MCP 直接加入 DeskHelm 核心。DeskHelm
已有本地 Unix socket、Bridge control protocol 和 Voice Gateway；若以后需要
浏览器/GUI 访问，应增加一个可选的薄适配层。

## 7. 部署方式和安全边界

VoiceStudio 提供 CPU、NVIDIA、ROCm 的 Docker Compose profile，持久化 SQLite
数据和 Hugging Face cache，并通过 healthcheck、API key 和 server mode 支持
headless GPU 主机。它还支持让远程 GPU worker 只承担模型计算，不暴露 UI。

这些做法可作为 DeskHelm 未来远程语音服务的参考，但不适合直接替换当前的
本机音频路径：

- PTT 和 PipeWire 设备应留在宿主机，避免容器设备权限和音频会话复杂化。
- 远程 ASR/TTS 应由客户端边缘负责采集，再通过明确认证的音频接口发送。
- 本地服务默认绑定 loopback；离开本机时必须使用 API key 加密传输或可信的
  WireGuard/Tailscale 网络。
- 管理面和消费面应分离，API key 不能自动获得任意文件路径或宿主机执行能力。

## 8. 许可证和供应链风险

必须分开审查三层许可证：

1. VoiceStudio 应用：AGPL-3.0。
2. 引擎代码：可能是 Apache-2.0、MIT 或其他上游许可证。
3. 模型权重、tokenizer、voice 文件和 gated access 条款：由各自模型发布者
   单独决定。

特别注意：

- OmniVoice 权重和 tokenizer 不能由 VoiceStudio 的应用许可证统一覆盖。
- PocketTTS 的权重虽然标注 CC-BY-4.0，但还存在 gated access 和 acceptable-use
  条款。
- CosyVoice、IndexTTS 等模型/运行时的代码许可证与模型使用许可证也必须分别
  记录。
- DeskHelm 不应复制 VoiceStudio 的 AGPL 后端实现；可以借鉴公开的架构思想，
  或将 VoiceStudio 作为明确隔离的外部服务进行实验。

## 9. 对 DeskHelm 的结论

### 立即保留

- 模块化的 `ASR -> Agent -> TTS` 边界。
- Piper/Kokoro/IndexTTS 的当前分层定位。
- Bridge 无重型依赖，模型 runtime 位于 `voice/runtime` 或外部目录。
- PipeWire/PTT 在宿主机，模型推理按需启动。
- notification-only TTS，不因接入语音平台就自动播报 Agent 私有回答。

### 后续可纳入工程计划

- 将 `sherpa-paraformer-bilingual-zh-en` 和 `sherpa-zipformer-bilingual-zh-en`
  作为额外 ASR 候选记录在 benchmark 计划中。
- 为重型或容易触发 native 崩溃的 ASR 增加可选 sidecar 运行形态。
- 在配置/诊断层区分模型制品、运行时、provider readiness 和真实请求验证。
- 为未来 GUI 或外部客户端定义独立的音频数据面和目标控制面。
- 借鉴 engine acceptance：每个候选必须有明确 job、许可证、平台范围、资源
  预算、失败策略和维护责任。
- 记录模型 warm、卸载、并发和显存预算，而不是只记录模型下载大小。

### 当前不采用

- 不引入 VoiceStudio AGPL 应用代码作为 DeskHelm 依赖。
- 不以 OmniVoice 取代现有通知 TTS 默认。
- 不以 PocketTTS 作为中文 TTS 候选。
- 不把 VoiceStudio 的 Docker/远程 worker 作为当前本机 PTT 运行方式。
- 不把离线模型按固定时间重算缓冲区的做法宣传为真正 streaming ASR。

## 10. 主要来源

- 项目主页和 README：<https://github.com/debpalash/VoiceStudio>
- 本地语音平台：<https://github.com/debpalash/VoiceStudio/blob/main/docs/speech-platform.md>
- 引擎清单：<https://github.com/debpalash/VoiceStudio/blob/main/docs/engines/README.md>
- Sherpa-ONNX ASR：<https://github.com/debpalash/VoiceStudio/blob/main/docs/engines/sherpa-onnx-asr.md>
- FunASR/SenseVoice：<https://github.com/debpalash/VoiceStudio/blob/main/docs/engines/funasr.md>
- OmniVoice：<https://github.com/debpalash/VoiceStudio/blob/main/docs/engines/omnivoice.md>
- PocketTTS：<https://github.com/debpalash/VoiceStudio/blob/main/docs/engines/pockettts.md>
- 引擎准入标准：<https://github.com/debpalash/VoiceStudio/blob/main/docs/engine-acceptance.md>
- Docker 部署：<https://github.com/debpalash/VoiceStudio/blob/main/docs/install/docker.md>
- API 认证：<https://github.com/debpalash/VoiceStudio/blob/main/docs/api-auth.md>
- Benchmark 状态：<https://github.com/debpalash/VoiceStudio/blob/main/docs/benchmarks.md>

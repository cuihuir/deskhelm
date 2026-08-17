# Linux 本地语音栈调研

调研日期：2026-08-14

状态：研究输入，尚未替代正式 ADR。

## 1. 目标

为 DeskHelm 增加一个可独立运行的 Voice Gateway，提供：

- 本地中文和中英混合语音输入。
- 可选的口语清理和提示词优化。
- Agent 回答的本地语音播报。
- 任务完成、等待批准、失败和需要用户输入时的桌面通知与语音提示。
- 不依赖某一个 Agent，也不要求硬件设备在线。

语音服务是 Agent Console 的一种 I/O 面，不是 Bridge、固件或 Agent adapter 的替代品。

## 2. 开发机约束

2026-08-14 在当前 Fedora 开发机观察到：

- NVIDIA RTX 5060 Laptop GPU，8 GiB 显存。
- 32 GiB 系统内存。
- PipeWire 1.6.8，默认采样率 48 kHz。
- Fcitx5/Rime 已运行。

因此，实时 ASR 应优先使用 CPU 友好的小模型；GPU 只按需租给最终识别、提示词优化或高质量 TTS 中的一项。不要同时常驻多个大模型。

## 3. ASR 候选

| 方案 | 主要用途 | 优点 | 注意事项 |
|---|---|---|---|
| FunASR Paraformer | 实时中文听写 | 中文、中英混合、低延迟流式路线成熟 | 需要验证具体模型和 Python/CUDA 组合 |
| SenseVoiceSmall | CPU 常驻和短句识别 | 轻量，支持中英日韩粤语及部分音频事件 | 最终专有名词准确率需要实测 |
| Fun-ASR-Nano-2512 | 松开按键后的最终转写 | 800M 级，面向中文、英文、日文和部分中文口音 | GPU 模型，不应作为首版常驻实时模型 |
| faster-whisper | 最终转写和混合语言校正 | CTranslate2 后端，支持 GPU、CPU INT8 和 Silero VAD | 原生实时输入需要额外的流式策略 |
| whisper.cpp | 可移植运行时 | C API、CPU、CUDA、Vulkan、VAD，适合低依赖部署 | 官方实时示例是基础实现，产品级流式需要自行设计 |
| sherpa-onnx | 长期维护的本地推理层 | 同时覆盖流式/非流式 ASR、VAD、TTS 和多种平台 | 质量取决于具体 ONNX 模型，模型筛选工作较多 |

### 推荐的双阶段 ASR

```text
按住 PTT
  -> PipeWire 音频环形缓冲
  -> VAD
  -> FunASR Paraformer 实时 partial
  -> 松开按键得到 final transcript
  -> 仅在长句、低置信度或明确要求时运行最终模型
```

首轮比较 `Fun-ASR-Nano-2512` 和 `faster-whisper turbo`。如果最终模型对桌面交互延迟影响明显，保留 Paraformer 单阶段路线。

VibeVoice-ASR 更适合长音频、说话人、时间戳和结构化转录，不作为当前 PTT 短句输入的首选。

## 4. TTS 候选

| 方案 | 主要用途 | 优点 | 注意事项 |
|---|---|---|---|
| Piper | 通知、状态、短句 | 本地、快、CPU 友好、启动简单 | 中文音色质量中等；每个 voice 的许可证要单独检查 |
| Kokoro-82M | 轻量高质量播报 | 82M，Apache 权重，支持中文、英文和多种语言 | 需要按句切分；中文自然度需和 CosyVoice 对比 |
| CosyVoice 3 0.5B | Agent 长回答和中文播报 | 支持多语言、中文方言、音色克隆及文本/音频流式 | PyTorch 环境较重，实际显存和首包延迟需本机验证 |
| VoxCPM 1.5 | 高质量中英播报和克隆 | 约 0.6B，支持中文/英文、流式和声音克隆 | 8 GiB 显存环境需要限制并发和上下文 |
| VoxCPM 2 | 多语言和声音设计 | 2B、30 种语言、48 kHz 输出 | 项目报告约 8 GiB 级显存，不适合当前机器首版常驻 |
| F5-TTS / Fish Speech | 音色实验和高质量生成 | 音色表现强，适合后续研究 | 显存、延迟、模型许可证或商业限制更复杂 |

### 推荐的双层 TTS

```text
状态通知：Piper 或 Kokoro
Agent 回答：CosyVoice 3 0.5B
高质量音色实验：VoxCPM 1.5，再评估 VoxCPM 2
```

Agent 输出不能逐 token 直接播报。应先按中文标点、英文句号和长度切分，形成可取消的音频队列。用户开始新的 PTT 输入时，默认停止当前播报。

## 5. 本地服务边界

Voice Gateway 应该提供稳定的本地接口，不把模型依赖泄漏到 Bridge：

```text
voice.submit_input(text, target_session)
voice.submit_control(command, target_session)
voice.speak(text, priority, interruptible)
voice.stop_speaking()
voice.get_status()
```

模型文件、CUDA 环境和大体积依赖不提交到仓库。仓库只保存 provider 接口、配置模板、模型下载说明、测试语料和许可证记录。

建议为 ASR/TTS 使用独立 Python 3.11/3.12 环境，避免和桌面系统 Python 以及零依赖 Bridge 发生冲突。

## 6. 安全和提示词处理

默认保留三个版本：

```text
raw_transcript
normalized_transcript
optimized_prompt
```

提示词优化必须默认保护：

- 文件名、目录、URL 和命令行。
- 数字、版本号和单位。
- 函数名、变量名、类名和专有名词。
- 否定条件、限制条件和用户意图。

建议只在用户明确说“整理后发送”或切换到优化模式时调用本地小模型。编码任务不应默认自动改写。

## 7. 验证计划

建立一组固定语料，至少包含：

- 普通中文和中英混说。
- 文件路径、命令、数字、版本号和 URL。
- Agent 名称、项目名、函数名和专有名词。
- 口语停顿、重复、否定和长句。

记录以下指标：

- 首个 partial 延迟。
- 松开按键到 final 文本延迟。
- 中文 CER、英文 WER 和关键字准确率。
- CPU、内存、显存和模型加载时间。
- TTS 首包延迟、可理解度、可打断时间。
- 断音频设备、切换默认设备和 Agent 退出时的恢复行为。

## 8. 主要来源

- [FunASR](https://github.com/modelscope/FunASR)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)
- [Piper](https://github.com/OHF-Voice/piper1-gpl)
- [Kokoro](https://github.com/hexgrad/kokoro)
- [CosyVoice](https://github.com/FunAudioLLM/CosyVoice)
- [VoxCPM](https://github.com/OpenBMB/VoxCPM)

许可证结论只对项目代码或权重的公开声明负责；实际部署前仍需核对具体模型卡、voice 文件和商业使用条件。

# DeskHelm 文档

本目录按照“研究输入 → 架构决策 → 工程实现 → 产品化”的顺序组织。

## 目录

| 目录 | 内容 |
|---|---|
| [`research/`](research/) | 市场、竞品、开源项目和技术可行性调研 |
| [`architecture/`](architecture/) | 系统边界、协议、状态机和总体架构 |
| [`hardware/`](hardware/) | 原理图、PCB、器件、结构和制造资料 |
| [`software/`](software/) | 固件、Agent Bridge、配置器和适配器设计 |
| [`product/`](product/) | 产品定义、用户场景、成本、合规与商业化 |
| [`decisions/`](decisions/) | ADR，记录重要工程决策及其原因 |

## 文档约定

- 调研文档使用 `YYYY-MM-DD-topic.md` 命名，保留调研时间点。
- 工程规范使用稳定名称，例如 `usb-protocol.md`、`agent-state-machine.md`。
- 重要技术选型使用 ADR，不在调研报告中直接视为最终决定。
- 外部项目必须记录来源、许可证、可制造资料、验证状态和风险。
- 成本数据必须注明日期、数量阶梯、税费和运费口径。

## 当前文档

- [`research/2026-07-16-agent-macropad-landscape.md`](research/2026-07-16-agent-macropad-landscape.md)：第一轮产品、软硬件生态和成本调研。
- [`research/2026-07-16-oshwhub-macropad-references.md`](research/2026-07-16-oshwhub-macropad-references.md)：嘉立创宏键盘工程、附件和许可证专项调研。
- [`research/2026-08-14-local-voice-stack.md`](research/2026-08-14-local-voice-stack.md)：Linux 本地 ASR、TTS、提示词处理和 Voice Gateway 调研。
- [`architecture/phase-0.md`](architecture/phase-0.md)：第一阶段软件验证范围与成功标准。
- [`architecture/multimodal-agent-console.md`](architecture/multimodal-agent-console.md)：Voice Gateway、Agent Bridge 和实体 Agent Console 的综合边界设计。
- [`decisions/0001-phase-0-python-unix-socket.md`](decisions/0001-phase-0-python-unix-socket.md)：Python 与 Unix socket 原型决策。
- [`decisions/0003-adopt-deskhelm-name.md`](decisions/0003-adopt-deskhelm-name.md)：DeskHelm 命名与兼容入口迁移决策。

# 多 Agent Vibecoding 状态键盘调研

调研日期：2026-07-16

## 1. 结论摘要

`agent-io` 的产品机会是创造一种新的桌面交互方式：

> 一台兼容 Codex、Claude Code、Gemini CLI、OpenCode 等工具的开放式桌面 Agent Console。

最有价值的不是键盘本身，而是以下组合：

1. 用实体灯光同时显示多个 Agent 的状态。
2. 用按键完成批准、拒绝、中断、切换会话、语音输入等高频动作。
3. 用旋钮或摇杆切换 Agent、项目、会话或调节终端界面。
4. 通过统一的本地桥接程序适配不同 Agent，而不是把固件绑定到 Codex。

推荐首版采用 **RP2040 + USB HID + USB CDC/WebSerial + 每键 RGB + EC11 旋钮**。不要在第一版加入昂贵 CNC 铝壳、定制键帽、触控屏或无线功能。

## 2. 市场参考：Creator Micro for Codex

OpenAI 与 Work Louder 联名的产品名为 **Creator Micro for Codex**，官方页面标价 **299 美元**，并标注限量 1,000 台。

从官方资料和宣传图可确认的主要元素：

- 4 个可区分 Agent 状态的透明 RGB 键位。
- 4 个操作键，图标对应快速操作、确认、拒绝和跳转/展开。
- 独立语音输入键。
- 一个带方向操作能力的摇杆式控件。
- 两个旋转控制器，其中一个看起来支持方向/多轴操作。
- 白色半透明外壳、定制键帽和较强的桌面摆件属性。
- Agent 状态灯至少表达空闲、思考、运行、等待和完成。

这个案例说明，实体 Agent 交互设备的价值不仅来自电子硬件，也来自工业设计、材料、定制结构、软件联动和完整的桌面体验。`agent-io` 将其视为市场验证和设计参考，而不是产品定义或实现模板。

官方来源：

- [OpenAI × Work Louder 产品页](https://openai.com/zh-Hans-CN/supply/co-lab/work-louder/)
- [Work Louder Creator Micro](https://worklouder.cc/creator-micro/)

## 3. 推荐系统架构

```text
Codex / Claude Code / Gemini CLI / OpenCode / 自定义 Agent
                         │
          hooks / plugin / notify / log / process events
                         │
                 Agent Bridge 守护进程
                         │
          USB CDC / WebSerial / HID feature report
                         │
                    RP2040 固件
                  ┌──────┴──────┐
             RGB 状态显示     HID 输入事件
```

### 为什么要有 Agent Bridge

键盘固件不适合直接理解每种 Agent 的日志和配置。桌面端桥接程序负责：

- 将不同 Agent 的事件统一成相同状态模型。
- 维护 Agent、项目、终端会话和物理槽位之间的映射。
- 把状态通过 USB 串口或自定义 HID report 发给设备。
- 把设备按键映射为快捷键、命令、tmux 操作或 Agent API 调用。
- 在某个 Agent 不提供插件接口时，退化为通知、日志或进程状态检测。

建议统一事件协议：

```json
{
  "agent_id": "project-a:codex:1",
  "slot": 0,
  "state": "waiting_approval",
  "progress": null,
  "label": "backend",
  "updated_at": 1784190000000
}
```

建议状态集合：

- `offline`
- `idle`
- `thinking`
- `running_tool`
- `waiting_approval`
- `waiting_user`
- `completed`
- `failed`

## 4. Agent 集成可行性

### Codex

当前 Codex CLI 支持外部 `notify` 程序，也支持 `SessionStart`、`PreToolUse`、`PostToolUse`、`PermissionRequest`、`SubagentStart`、`SubagentStop` 和 `Stop` 等生命周期 hooks。这足以可靠地生成开始、运行工具、请求批准和完成等状态事件。

实现建议：

- 用户级 hook 将事件写入本地 Unix socket。
- Bridge 接收事件并更新对应槽位。
- `PermissionRequest` 显示橙色或红色呼吸灯。
- `Stop` 显示绿色完成灯，若 turn 仍需用户输入则显示蓝色。

注意：该设备与 Codex 的内部联动协议并未在产品页面公开。`agent-io` 使用公开 hooks/notify 能力构建独立、跨 Agent 的事件与设备协议，不依赖逆向第三方设备。

### Claude Code

Claude Code 提供 hooks 机制，可在通知、工具调用前后、权限请求和会话停止等节点执行命令。它与 Codex 的集成方式相近，可以共用 Bridge 的 hook receiver，只需不同的事件转换器。

参考：[Claude Code hooks 文档](https://docs.anthropic.com/en/docs/claude-code/hooks)

### Gemini CLI

Gemini CLI 已提供 hooks 文档和生命周期事件，可通过命令型 hook 将状态推送给 Bridge。

参考：[Gemini CLI hooks](https://google-gemini.github.io/gemini-cli/docs/hooks/)

### OpenCode

OpenCode 的插件系统可订阅 session、message、tool、permission 等事件，比解析终端输出更稳定，适合作为首批官方适配器。

参考：[OpenCode plugins](https://opencode.ai/docs/plugins/)

### 其他 Agent

建议提供三级适配能力：

1. **原生适配器**：使用 hooks、plugin SDK 或结构化事件。
2. **终端适配器**：管理 tmux/zellij pane，并根据进程和提示符检测状态。
3. **通用适配器**：监听桌面通知或由用户脚本调用 `agentdeck emit`。

不要把“解析彩色终端文本”作为主要方案，它容易随版本、主题和语言变化而失效。

## 5. 可复用开源项目

### 固件与键盘生态

- [QMK Firmware](https://github.com/qmk/qmk_firmware)：成熟的键盘固件生态，支持 RP2040、RGB Matrix、旋转编码器、宏和多层键位。GPL-2.0。
- [KMK Firmware](https://github.com/KMKfw/kmk_firmware)：基于 CircuitPython，原型迭代快，适合快速验证串口协议和灯效。MIT。
- [Vial](https://github.com/vial-kb/vial-qmk)：可在桌面端实时改键，比编译 QMK 更适合消费级产品，但正式产品需认真处理设备定义和安全解锁流程。GPL-2.0。
- [Pimoroni Keybow 2040](https://github.com/pimoroni/keybow2040-circuitpython)：RP2040、16 键、每键 RGB 的成熟参考，可借鉴灯光和 CircuitPython 原型结构。
- [Adafruit MacroPad RP2040](https://github.com/adafruit/Adafruit_CircuitPython_MacroPad)：12 键、RGB、旋钮和显示屏的成熟软硬件参考。

### 硬件和结构参考

- [Framework Input Module firmware](https://github.com/FrameworkComputer/inputmodule-rs)：展示 RP2040 输入模块、USB HID 和 RGB LED 控制的产品级 Rust 固件实现。MIT/Apache-2.0。
- [Framework Input Module mechanical](https://github.com/FrameworkComputer/InputModules)：包含输入模块的开源机械设计，可参考薄型模块、结构公差和制造文件。
- [ScottoKeebs](https://github.com/joe-scotto/scottokeebs)：大量低成本手焊键盘和 macropad 设计，适合验证无需二极管或简化矩阵的低成本原型。
- [GP2040-CE](https://github.com/OpenStickCommunity/GP2040-CE)：虽然主要面向街机控制器，但它是 RP2040 上 USB HID、Web Configurator、RGB 和扩展接口的成熟实现，可借鉴配置网页与升级机制。MIT。

### 多 Agent 控制台参考

- [Agent Deck](https://github.com/asheshgoplani/agent-deck)：面向 Codex、Claude、Gemini、OpenCode 等编码 Agent 的终端会话管理器，可参考其 Agent 检测、会话组织和状态展示方式。MIT。
- [Crystal](https://github.com/stravu/crystal)：并行管理多个 Codex/Claude Code 会话的桌面应用，可作为“多任务映射到物理槽位”的交互参考。

### 嘉立创开源硬件平台的使用建议

已使用 Lightpanda 和平台搜索 API 对嘉立创开源硬件平台进行了专项检索。当前最相关的项目包括 ZERO-PAD、3Plus V2、IcePad、AFpad-AT、ESP32-C3 17 键小键盘和多个 RP2040/QMK 键盘工程。

详细项目矩阵、附件完整性、许可证和工程建议见：

- [嘉立创开源硬件平台宏键盘参考调研](2026-07-16-oshwhub-macropad-references.md)

这些项目中多数使用 `CC BY-NC-SA`，适合学习和验证，但不能直接用于商业产品。平台“开源”也不等于放弃著作权或允许销售。

使用平台项目时必须逐项确认：

- 是否明确允许商用，而不只是允许个人学习和打样。
- PCB、固件、外壳三部分是否使用相同或兼容许可证。
- BOM 中元件是否仍可采购，封装是否来自私有库。
- USB 差分线、ESD、晶振、Flash 和供电设计是否经过实际验证。
- 作者展示“开源”不等于放弃著作权或允许直接销售。

平台入口：[嘉立创开源硬件平台](https://oshwhub.com/)

## 6. 首版硬件建议

### MVP：8 键 + 4 Agent 灯 + 双旋钮

推荐配置：

- RP2040 或 RP2350 主控。
- USB-C，仅 USB 2.0 Device，不做 Hub。
- 8 个机械轴按键，其中 4 个兼作 Agent 状态灯。
- 8 至 12 颗 SK6812 MINI-E 或兼容可寻址 RGB LED。
- 2 个 EC11 旋转编码器，均支持按压。
- 1 个蜂鸣器，可选。
- 1 个 Qwiic/STEMMA QT 扩展口，可选。
- 1 个 Boot 和 1 个 Reset 按钮。
- FR4 定位板或 3D 打印外壳。

第一版不建议加入：

- 电池、蓝牙和 2.4G 无线。
- OLED/TFT 屏幕。
- 定制摇杆或轨迹球。
- CNC 铝合金外壳。
- 热插拔轴座，除非用户测试明确需要频繁换轴。

### 主控选择

**方案 A：RP2040 模块**

- 最快、风险最低。
- 可以直接使用 Waveshare RP2040-Zero、Seeed XIAO RP2040 等模块。
- 适合 10 至 50 台工程样机。

**方案 B：板载 RP2040**

- 单板成本更低，外形更自由。
- 需要处理外部 Flash、晶振、USB、上电和调试细节。
- 适合结构验证完成后的几十至数百台批量。

**方案 C：RP2350**

- 性能和安全能力更强，但对这个产品并非刚需。
- 如果供应和固件生态成本无明显劣势，可作为后续升级版。

## 7. 通信和固件策略

推荐设备同时暴露两个 USB interface：

1. 标准 HID Keyboard/Consumer Control，保证无需驱动即可使用。
2. CDC Serial 或 Vendor HID，用于灯光状态、设备配置和固件信息。

不推荐只用串口发送按键，因为这样会失去即插即用体验。也不推荐让 Bridge 模拟键盘，因为权限和跨平台兼容性更差。

固件路线：

- **原型期**：KMK/CircuitPython，快速修改协议和灯效。
- **产品期**：QMK 或 Rust/TinyUSB，自定义 Vendor HID/CDC 接口。
- **配置器**：首版使用本地 WebSerial 页面，后续再做 Tauri 桌面应用。

建议将 Agent 状态灯与普通 RGB 层分开。状态颜色由 Bridge 控制，用户灯效只能修改非状态区域或状态灯的亮度/动画样式，避免视觉语义混乱。

## 8. 粗略 BOM 成本

以下为设计阶段估算，不含税、运费、贴片损耗、认证、开发和售后成本。

### 手工样机，1 至 10 台

| 项目 | 估算单价 |
|---|---:|
| RP2040 开发模块 | ¥20–45 |
| PCB，小批量 | ¥10–30 |
| 机械轴、键帽、二极管 | ¥25–60 |
| RGB LED | ¥5–15 |
| 两个编码器和旋钮帽 | ¥10–30 |
| USB-C、电阻、电容、保护器件 | ¥8–20 |
| FR4/3D 打印外壳和紧固件 | ¥20–80 |
| **电子与结构合计** | **约 ¥100–280** |

### 50 至 200 台小批量

| 项目 | 估算单价 |
|---|---:|
| 板载 RP2040、Flash、时钟和供电 | ¥18–35 |
| PCB 与 SMT | ¥20–55 |
| 轴、键帽、RGB、编码器 | ¥35–80 |
| 注塑前的 3D 打印/亚克力/FR4 外壳 | ¥20–70 |
| 包装、线材、组装和测试 | ¥20–60 |
| **制造成本目标** | **约 ¥110–300** |

如果使用 CNC 铝壳、定制透明键帽、小批量复杂结构件或高端轴体，成本很容易增加 ¥300–1,000 以上。真正的低成本版本应尽量使用标准 MX/Choc 键帽、标准旋钮和 PCB/FR4 结构。

合理零售价可能落在：

- 基础套件：¥299–399。
- 完整成品：¥499–699。
- 高级外壳/定制键帽版：¥899–1,299。

价格是否成立，取决于 Bridge 软件是否足够稳定。只做一个普通宏键盘，很难支撑较高售价。

## 9. 产品差异化机会

`agent-io` 的独立产品方向包括：

- 不绑定 Codex，同时支持本地和云端 Agent。
- 一个按键对应一个项目或 Agent，而不是固定某种工作流。
- 提供公开事件协议和 SDK，让用户适配任意工具。
- 支持 tmux、zellij、VS Code、JetBrains 和终端窗口聚焦。
- 支持批准、拒绝、中断、继续、语音输入和复制最终结果。
- 支持多个设备拼接或 4/8/12 Agent 不同规格。
- 提供完全离线模式，Bridge 不上传提示词或代码内容。
- 开源基础版，销售组装成品、外壳和高级软件功能。

## 10. 主要风险

### 商标与外观

- 不要使用 Codex、OpenAI、Work Louder 商标作为产品名或营销主视觉。
- 不要一比一复制官方外壳、图标、丝印和键帽造型。
- 可以复现功能思想，但应形成明显不同的工业设计和品牌。

### 软件兼容性

- 各 Agent 的 hook 事件和配置格式会变化，需要版本检测和适配测试。
- 权限批准通常具有安全含义。物理“批准”键必须防止误触，并明确显示目标 Agent 和命令。
- 不应默认提供“自动批准所有命令”的硬件快捷键。

### USB 与量产

- Vendor HID、CDC 和键盘复合设备在 Windows、macOS、Linux 上都要测试。
- 可寻址 RGB 会制造瞬时电流和信号完整性问题，需要限流和电源设计。
- 若作为成品销售，需要评估 FCC/CE、RoHS、包装、电商和售后要求。

## 11. 推荐开发阶段

### Phase 0：软件验证，约 2–4 天

- 用命令行程序模拟四个 Agent 状态。
- Codex hook 将状态写入本地 socket。
- Bridge 在终端显示四个彩色槽位。

### Phase 1：面包板/模块样机，约 1 周

- RP2040-Zero + 4 颗 RGB + 4 至 8 个按键。
- 实现 HID 按键和串口状态协议。
- 验证 Codex、Claude Code、Gemini CLI、OpenCode 各一个适配器。

### Phase 2：首块 PCB，约 1–2 周

- 8 键、双编码器、每键 RGB、USB-C。
- FR4 夹层或 3D 打印外壳。
- WebSerial 配置器。

### Phase 3：小范围用户测试

- 找 10–20 名重度 vibecoding 用户。
- 测试“状态一眼可见”是否真能减少切换终端的次数。
- 记录最常用的实体动作，再决定是否加入摇杆、屏幕或更多键。

## 12. 最终建议

优先启动一个低风险原型，目标不是复制官方外观，而是验证以下核心假设：

> 用户是否愿意为“同时看见多个 Agent 状态，并用实体键快速响应”付费。

建议首版产品代号为 **Agent Pad** 或 **Agent Deck** 类中性名称，但正式命名前应先做商标和现有项目冲突检索。首版只做 8 键、双旋钮和 RGB，硬件预算控制在 ¥200 左右，把主要精力投入跨 Agent Bridge、可靠状态机和安全的批准交互。

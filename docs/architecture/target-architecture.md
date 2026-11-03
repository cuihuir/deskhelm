# 目标架构（重设计提案）

日期：2026-11-03

状态：2026-11-03 采纳，见 ADR 0043；分阶段实施。§8 的 D1–D4 和基线提交都按建议决定。

范围：Bridge、适配器、语音、客户端和治理。硬件和远程访问只定义边界，细节另立 ADR。

依据：对仓库（含未提交改动）的四路只读审视，即 Bridge 核心与协议、Runtime 接入与适配器、语音与表面与客户端、治理与测试；以及与 t3code（`references/vendor/t3code`，commit `997e3f3`）的对照。正文中的事实都附代码位置；标【推】的是推断，未复现。

## 1. 结论

问题的根子不在某个模块，而在一个结构选择：Bridge 进程既是控制面，又托管了数据面。适配器、Codex TUI 代理、语音、终端面板都运行在 Bridge 里。由此连锁出三类问题：

- 跨子系统的锁顺序反转可以冻结全部会话（§2.1）。
- 重启或升级 Bridge 会中断全部会话（§2.1）。
- 会话事实没有单一所有者，每个客户端都在自己重建状态，未来的键盘拿不到可用的数据（§2.2、§2.4）。

目标架构把 DeskHelm 改成“一个纯控制面加两类客户端”：

- **Bridge** 只管会话事实、路由、确认策略和投影；核心单线程，不含任何厂商、语音或渲染代码。
- **会话宿主**每会话一个进程，拥有厂商进程和驱动，Codex 的 TUI 代理也在这里。
- **表面**（web、dashboard、语音、未来的键盘）共用一个客户端运行时和同一套确认策略。

不变的是：本地优先、不持有权威历史、三元组会话标识、控制安全信封、原生 Codex TUI 共存、所有上限（§3）。

迁移分五个阶段，每个阶段都能单独交付（§6）。阶段 0 是在现有代码上修 bug，不管本提案采不采纳都应该做。

## 2. 诊断：五个根因

规模参考：`deskhelm_bridge` 35 个模块约 1.09 万行；三个适配器约 4.8 千行，其中 Codex 3.5 千；`voice/` 约 6.7 千行；测试 50 个文件、405 个测试函数。

### 2.1 Bridge 既是控制面又托管数据面

| 现象 | 证据 |
|---|---|
| 锁顺序反转：路由持锁调用 handler，handler 同步发布用户消息时需要 `_event_lock`；发布路径持 `_event_lock` 记录审批时需要路由锁。【推】审批请求和提示提交并发时，整个 user bridge 死锁 | `bridge/deskhelm_bridge/control_router.py:161-216`、`:129`；`bridge/deskhelm_bridge/server.py:127-131`；`adapters/codex/deskhelm_codex_adapter/app_server.py:475`；`adapters/claude/deskhelm_claude_adapter/runtime.py:60` |
| handler 在锁内阻塞：Grok 未就绪时忙等最长 30 秒，期间所有会话的控制都被挡住；Claude 的 `sendall` 没有看到超时 | `adapters/grok/deskhelm_grok_adapter/runtime.py:87-92`；`adapters/claude/deskhelm_claude_adapter/runtime.py:145-154` |
| 重启 Bridge 中断全部会话：relay 收到 EOF 即停止厂商进程；user bridge 的 socket 消失时，Codex 启动器停掉 TUI；TUI 代理本身也在 Bridge 进程里 | `bridge/deskhelm_bridge/stdio_relay.py:205-207`；`bridge/deskhelm_bridge/alpha.py:424-429,453-454`；`adapters/codex/deskhelm_codex_adapter/app_server.py:338-353` |
| 适配器升级必须重启 Bridge。【推】所以 ADR 0041、0042 所说的“重启该会话后生效”并不成立 | AGENTS.md：“restart it before the first `deskhelm agent`” |
| 配置面膨胀：`deskhelm bridge` 有 50 个参数，其中语音 22 个、Codex 6 个、gateway/runtime 11 个，还负责终端面板。开语音时 Bridge 必须运行在重型语音环境里，已运行的无语音 Bridge 无法补上语音 | `deskhelm bridge --help`；AGENTS.md：“a bridge already running without voice is not upgraded” |

### 2.2 会话事实没有单一所有者

| 现象 | 证据 |
|---|---|
| 一个会话散落在 5 个结构、4 套生命周期里：SessionRegistry、AdapterRegistry、AgentRuntimeDirectory、StateStore，以及 ControlRouter 里的审批 | `bridge/deskhelm_bridge/` 下的 `session_registry.py:38-44`、`adapter_registry.py:14-34`、`runtime_directory.py:121-133`、`state_store.py:39`、`control_router.py:49-57` |
| slot 兼任容量上限，`deskhelm bridge` 默认 4 个 | `bridge/deskhelm_bridge/cli.py:46`；`bridge/deskhelm_bridge/session_registry.py:182-186` |
| 状态平面是 legacy 的、按 slot 存放的 `AgentEvent`，没有 `session_id` 和 `project_id`；三个现代适配器都不产出状态事件 | `bridge/deskhelm_bridge/event.py:29-36`；`bridge/deskhelm_bridge/alpha.py:990-997`；`bridge/deskhelm_bridge/agent_session.py:15-22` |
| 待批准记录只在 ControlRouter 里，`session_query` 不返回；交互平面只推实时事件。客户端重连后看不到、也批不了已挂起的审批 | `server.py` 中没有任何 approvals 字段；web 与 dashboard 各自重建（`bridge/deskhelm_bridge/web_client.py:449-453`、`bridge/deskhelm_bridge/dashboard.py:161`） |
| 挂载没有租约：启动器异常退出后，在同一目录重启会得到 `already_attached` | `bridge/deskhelm_bridge/runtime_directory.py:273` |
| “确定没送达”被当成“可能已生效”：runtime 未挂载时分派抛错，审批照样被消费 | `bridge/deskhelm_bridge/runtime_directory.py:342`；`bridge/deskhelm_bridge/control_router.py:220-234` |
| 能力有三套：传输能力 7 个、`AdapterCapability` 8 个、厂商声明的 token。它们由启动器而不是实现方声明，路由从不检查。Codex 发布工具事件，却没有声明 `TOOL_EVENTS` | `bridge/deskhelm_bridge/alpha.py:990-997` |
| 厂商 id 当主键：审批表以全局 request_id 为键，三家的 id 方案各不相同；ADR 0042 靠文本给回显去重 | `bridge/deskhelm_bridge/control_router.py:52` |

### 2.3 没有统一的适配器契约

| 现象 | 证据 |
|---|---|
| `AgentRuntime` 只有 start 和 close，facade 靠鸭子类型冒充 ControlRouter；codex exec gateway 的 `AgentProvider` 是第二套契约 | `bridge/deskhelm_bridge/agent_runtime.py:10-19` |
| 终止只有 completed 和 failed，没有 interrupted、失败分类和会话结束 | `bridge/deskhelm_bridge/interaction.py:16-23` |
| 三家行为不一致：审批超限时 Codex 有处理，Claude 和 Grok 超过 8 条就静默丢弃【推：厂商会一直挂起】；Claude 和 Grok 从不结算残留审批；去重窗口分别是 8、1、1；就绪判断和跨线程保护各不相同 | `adapters/codex/deskhelm_codex_adapter/app_server.py:1345`；`adapters/claude/deskhelm_claude_adapter/stream.py:290`；`adapters/grok/deskhelm_grok_adapter/stream.py:276` |
| 隐私边界各自解释：三家都发布工具输出，对参数的处理却不同。Codex 把命令行当作工具名发布，还发布 diff；Claude 不发参数；Grok 用 ACP title 作工具名和审批摘要，而真实 Grok 的 title 含命令和路径，如 “Execute `rm -rf …`”、“Write `<workspace>/…`”，违反 ADR 0040“不发布工具参数和工作目录” | `adapters/codex/deskhelm_codex_adapter/app_server.py:2225-2268`；`adapters/claude/deskhelm_claude_adapter/stream.py:206,237-246`；`adapters/grok/deskhelm_grok_adapter/stream.py:225,283-291`；t3code 的 Grok 1.0.41 录制 |
| 已知的 Grok 缺陷：审批选项用子串匹配，会选中 `allow_always`；`session/cancel` 用带 id 的 request 发送，ACP 规定它是 notification | `adapters/grok/deskhelm_grok_adapter/stream.py:414`、`:81-91` |
| 测试是对协议的猜测：fixture 全是合成单帧，Codex 测试用手写的 fake transport，没有双向时序回放 | `tests/test_codex_app_server_adapter.py:62` |
| 新增一个 runtime 要改 7 到 9 个文件，runtime 种类硬编码在核心里 | `bridge/deskhelm_bridge/runtime_directory.py:40-45`；`bridge/deskhelm_bridge/cli.py:243,936-949`；`bridge/deskhelm_bridge/agent_session.py:37-59` |

### 2.4 表面之间没有共享语义

| 现象 | 证据 |
|---|---|
| 语音生命周期由四方分持：按键边沿在外部进程，授权在 Router，短语解释和失败转写在 integration，状态机在 gateway。松开即提交，没有草稿阶段，取消也不对外暴露 | `bridge/deskhelm_bridge/ptt.py:30-61`；`voice/deskhelm_voice/gateway.py:342` |
| ASR 超时或取消后不等识别结束就清回空闲。【推】单 worker 的识别线程仍在忙时，下一次请求的排队时间会吃掉它自己的时限 | `voice/deskhelm_voice/gateway.py:377-381,395-402` |
| 三个客户端各自实现连接和状态聚合：dashboard 断线即退出，失败文案有 3 份 | `bridge/deskhelm_bridge/dashboard.py:132`；`bridge/deskhelm_bridge/voice_integration.py:396`、`bridge/deskhelm_bridge/client.py:52`、`web-assets/app.js` 的 `CODE_TEXT` |
| 潜伏缺陷：dashboard 把审批摘要截到 160 字再提交，Router 要求逐字相等 | `bridge/deskhelm_bridge/dashboard.py:161`；`bridge/deskhelm_bridge/control_router.py:309` |
| web 的幂等键由中继进程每次请求随机生成，浏览器重试就是一条新命令；浏览器端的事件数组从不裁剪 | `bridge/deskhelm_bridge/web_client.py:427`；`bridge/deskhelm_bridge/web-assets/app.js:701,710` |
| 语义放错了层：通知文案写死 “Codex”，Claude 和 Grok 会话也这样播报；语音失败作为 system 消息混进 transcript | `bridge/deskhelm_bridge/voice_integration.py:375-378`、`:339-355` |
| 确认强度没有统一定义：说“可以”就批准，没有待批准项时同一句话被当作 prompt 发出；架构文档写的却是“语音默认不执行批准” | `bridge/deskhelm_bridge/voice_integration.py:149-152`、`:404-414`；`docs/architecture/multimodal-agent-console.md:247` |
| 没有 device projection：架构图里画了，代码里不存在，按现有协议未来的实体批准键构造不出合法命令。ADR 0027 要求键盘 LED 接收语音生命周期，ADR 0009 却规定语音事件只在进程内 | `docs/architecture/multimodal-agent-console.md:35`；ADR 0027 第 33 行；ADR 0009 第 56 行 |

### 2.5 治理放大了漂移

| 现象 | 证据 |
|---|---|
| 最后一次提交在 2026-09-02，之后约 1.4 万行改动未提交：49 个修改文件、44 个未跟踪文件，含 ADR 0034–0042、Claude 与 Grok 适配器、web 和 dashboard | `git status`、`git log` |
| AGENTS.md 711 行，45% 是实现级规则，每次实现变化都变成宪法修改；Handoff.md 约 2900 行，与它自己“简洁的当前状态”的规则相反；约两个半月产生 42 个 ADR | `wc -l` |
| 文档互相矛盾：ADR 0027 与 0009（见上）；架构文档 §6 与 AGENTS.md 在语音批准上相反；ADR 0036 把 web 写成 “inside the Bridge process”，实际是独立进程 | 同上 |
| 打包要靠 PYTHONPATH 拼接：`pyproject.toml` 只声明 4 个包，含 legacy 的 `agent_io_bridge`，不含 Claude 和 Grok 适配器 | `pyproject.toml` |

## 3. 重构中保留的设计

- 本地优先、Agent 无关；不持有外部 Agent 的权威历史，不读 transcript 文件；不成为新的 Agent 框架。
- 单个 Unix socket、hello 协商、1 MiB 帧上限、有界队列、慢订阅者直接断开、首帧限时（改成总时限）。
- 会话用 `agent_id + session_id + project_id` 标识；`project_id` 不透明；工作目录永不进入协议。
- 控制必须定向、可过期、带幂等键（按 `issued_by + idempotency_key` 划分），永不按 slot 定向；审批永不自动重试，只要可能已送达就视为消费。
- 焦点只能显式改变，断开、释放、过期即清除；需要关注不等于可以批准。
- 厂商格式只在适配器边界解析；提示走 stdin，不进 argv；按进程组终止；默认不记录提示、源码、原始事件和音频。
- 原生 Codex TUI 共存：app-server 加 TUI 代理，审批走原 JSON-RPC 通道，键盘与语音的竞争和迟到抑制语义保持不变（ADR 0033）。
- 语音：`voice/` 不依赖 Bridge；provider 懒加载、有界、可取消；VAD 只作参考；press 与 release 用 id 关联；只播报固定类别；显式指定的设备缺失时不回退；右 Alt 只观察 keyd 节点、不 grab，用 flock 保证单 owner。
- fixture 出处纪律；Bridge 纯标准库；Python 依赖统一用 uv。
- 不换语言：本地规模下 Python 标准库够用，语音生态也在 Python。t3code 选 TypeScript 和 Effect 是为它的多端产品服务，不是我们的约束。

## 4. 目标架构

### 4.1 拓扑

```text
厂商 CLI ──stdio / WebSocket── 会话宿主（每会话一个进程，拥有厂商进程）
                                  deskhelm codex           Codex 驱动 + TUI 代理 + PTY
                                  deskhelm agent claude    Claude 驱动
                                  deskhelm agent grok      ACP 驱动 + Grok flavor
                                        │  host 连接，连接即租约：fact ↑  effect ↓  结果 ↑
                                        ▼
                               Bridge（每用户一个，纯标准库）
                                  server：只收发帧、排队
                                  core：单线程 actor
                                        SessionTable · Receipts · ApprovalBook
                                        Focus · DecisionPolicy · Projections
                                        ▲  client 连接：订阅 console 或 interaction，发 command
                                        │
表面（共用 deskhelm_client，按层级拿投影）
   deskhelm web          operator 层；浏览器经 token 与 cookie 鉴权
   deskhelm dashboard    operator 层
   deskhelm voice        voice 层；PTT 监听 + VoiceTurns + ASR/TTS，运行在语音环境
   device 宿主（以后）    device 层；键盘的 LED、屏幕和批准键，固件不直接说 NDJSON
```

Bridge 重启时，宿主保留厂商进程，带退避重连，重新打开会话并重新上报未结请求。这段时间 Codex 的 TUI 照常可用，只是没有语音和 web 控制。

### 4.2 组件职责

| 组件 | 负责 | 不负责 |
|---|---|---|
| Bridge | 会话事实、焦点、路由与幂等、审批票据、确认策略、投影与订阅、上限 | 厂商协议、进程、音频、渲染、HTTP |
| 会话宿主 | 厂商进程、驱动、Codex 的 TUI 代理、与 Bridge 的重连 | 跨会话状态、焦点、确认策略 |
| `deskhelm_client` | 连接、重连、快照与游标同步、视图模型、命令与结果关联、唯一一份失败文案 | 业务判断 |
| 表面 | 渲染投影、采集意图、各自的输入设备 | 会话真相、审批有效性 |
| `voice/` 库 | provider 无关的捕获、VAD、ASR、TTS、播放 | 目标选择和 Bridge 协议，二者由语音宿主组合 |

### 4.3 Bridge 核心与数据模型

```python
@dataclass(frozen=True)
class SessionKey:
    agent_id: str
    session_id: str
    project_id: str                     # 三项都必填（删除 legacy 之后）

class Activity(StrEnum):
    STARTING = "starting"
    IDLE = "idle"
    WORKING = "working"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_INPUT = "waiting_input"
    DISCONNECTED = "disconnected"

@dataclass(frozen=True)
class ApprovalTicket:
    approval_id: str                    # Bridge 生成，永不复用
    session: SessionKey
    summary: str                        # 固定、无内容的摘要，各层级都可见
    expires_at: int
    answerable: bool                    # False：只能在原生 UI 处理

@dataclass(frozen=True)
class SessionView:
    key: SessionKey
    runtime_name: str
    capabilities: CapabilityMatrix
    activity: Activity
    turn_id: str | None                 # Bridge 生成
    last_outcome: TurnOutcome | None
    approvals: tuple[ApprovalTicket, ...]

@dataclass(frozen=True)
class ConsoleState:                     # console 订阅：先快照，再按 seq 推 delta
    generation: int                     # Bridge 重启或追赶失败时递增
    seq: int
    focus: SessionKey | None
    sessions: tuple[SessionView, ...]
    surfaces: tuple[SurfaceStatus, ...] # 例如语音：idle、capturing、transcribing、speaking、failed

class Core:                             # 单线程、无锁、无 I/O
    def handle_command(self, cmd: Command, tier: Tier, now: int) -> Decision: ...
    def ingest_fact(self, fact: Fact, now: int) -> list[Change]: ...
    def settle_effect(self, effect_id: str, outcome: EffectOutcome, now: int) -> list[Change]: ...
    def snapshot(self, tier: Tier) -> ConsoleState: ...
    def tick(self, now: int) -> list[Change]: ...   # 过期、effect 超时
```

规则：

- server 线程只做编解码和排队，所有输入经一个有界 inbox 交给 core 顺序处理。handler 不再在任何锁内执行，锁顺序问题从结构上消失。
- 状态只从 fact 的无内容字段推导，富内容只进入 operator 层的交互流。隐私在输出侧按层级过滤，不再靠输入侧分平面。
- approval_id 和 turn_id 由 Bridge 生成；厂商 id 只作为会话内的 `provider_ref` 交给宿主。
- effect 是 Bridge 发给宿主的执行请求，结果分四种：Accepted 和 Ambiguous（已发出但超时或断线）消费审批；Undelivered 和 Unsupported 不消费，并返回明确的错误码。命令结果在宿主回执或超时之后才发给客户端。
- 幂等收据用哈希指纹；容量满时拒绝新命令，绝不驱逐活记录。
- core 里没有 slot。每个表面用自己的 SlotMap 决定显示哪几个会话，slot 不再限制会话数量。
- 断开的会话在有界时间后过期，由 core 的时钟驱动。

### 4.4 协议 v2

| 方向 | 消息 |
|---|---|
| 握手 | `hello`、`hello_ok`、`error` |
| 宿主 → Bridge | `session_open`（含能力矩阵和未结请求，重连时据此恢复）、`fact`、`effect_result`、`session_close` |
| Bridge → 宿主 | `session_open_result`、`effect` |
| 表面 → Bridge | `subscribe`（console 或 interaction，可按会话过滤，可带游标）、`command`、`query` |
| Bridge → 表面 | `snapshot`、`delta`、`interaction`、`command_result`、`query_result` |

- 角色只剩 host 和 client 两种。client 连接可以同时订阅和发命令，一个 web 或 dashboard 进程一条连接就够。现在的 25 种消息、三种角色和 legacy 无类型帧都不再需要。
- 能力只剩两类：hello 里协商的线协议特性，以及每个会话由驱动自报的能力矩阵。厂商声明的 token 只作为 catalog 里的展示数据。
- catalog 由宿主异步刷新后作为 fact 推给 Bridge 缓存，查询不再阻塞在宿主上。
- 宿主只能为自己打开的会话发布 fact。同一个会话被新连接重新打开时，沿用“替换者不受旧连接关闭影响”的规则。

### 4.5 会话宿主与适配器契约

```python
class Driver(Protocol):
    kind: str                                    # "codex" | "claude" | "acp"
    def capabilities(self) -> CapabilityMatrix: ...
    def open(self, spec: SessionSpec, sink: FactSink) -> DriverSession: ...

class DriverSession(Protocol):                   # 单线程 actor：方法只入队，立即返回
    def perform(self, effect: Effect) -> EffectOutcome: ...   # Accepted | Undelivered | Unsupported
    def open_requests(self) -> list[RequestOpened]: ...       # 重连时随 session_open 上报
    def close(self, reason: CloseReason) -> None: ...

@dataclass(frozen=True)
class Fact:
    session: SessionKey
    host_seq: int                                # 每会话单调递增，Bridge 据此发现缺口
    kind: FactKind                               # ready、turn_started、message、tool、request_opened、
                                                 # request_resolved、turn_terminal、catalog、session_ended
    meta: Mapping[str, str | int | bool]         # 无内容：状态、类别、id、计数、固定摘要
    content: Mapping[str, str]                   # 富内容：只进入 operator 层

@dataclass(frozen=True)
class CapabilityMatrix:
    version: int
    prompt_while_busy: Literal["steer", "queue", "reject"]
    interrupt: bool
    approvals: Literal["answerable", "notify_only", "none"]
    approval_overflow: Literal["decline", "native_ui"]
    user_input: Literal["answerable", "native_ui", "none"]
    resume: bool
    configure: frozenset[str]                    # {"model", "effort"} 的子集
    catalog: frozenset[str]                      # {"models", "mcp", "skills"} 的子集

@dataclass(frozen=True)
class TurnOutcome:
    status: Literal["completed", "interrupted", "failed"]
    failure: Literal["provider", "usage_limit", "auth", "protocol",
                     "transport_lost", "process_exit", "timeout"] | None
    reusable: bool                               # 会话能否继续接收 prompt
```

契约规则：

1. 每条终止路径（完成、中断、失败、进程退出、传输中断）都结算该 turn 的全部未结请求和工具调用。
2. 请求超过上限时，按能力 decline 或交给原生 UI，并上报；绝不静默丢弃。
3. 审批按语义 kind 精确选择“仅本次”；找不到就返回 Unsupported，绝不退而选“总是允许”。厂商的 option id 原样透传。
4. 中断早于 turn 真正开始时，宿主保留这次取消，直到收到厂商的 turn-started 信号。
5. 宿主按 command_id 关联自己发出的提示和厂商回显，不再比较文本，取代 ADR 0042 的去重窗口。
6. 隐私由一个 PrivacyFilter 统一处理，字段分 meta、content、never 三级，never 永不离开宿主。审批摘要只用工具类别，不用厂商 title。
7. 能力矩阵只描述厂商真实能做的事；声明了却在运行时失败，算缺陷。

adapter kit 由所有宿主共用，包括：JsonRpcPeer；唯一一份 WebSocket 与 NDJSON 帧实现；PendingRequests（有界、终止时结算）；TurnTracker；PromptCorrelation；ProcessOwner（进程组、stdin 提示、stderr 抑制、终止宽限）；PrivacyFilter；BridgeLink（带抖动退避的重连和 `session_open`）。宿主在目录锁里记录自己拥有的进程组，下一个宿主启动时回收残留，取代启动器里的 lsof/kill 补救。

| 驱动 | 传输 | 说明 |
|---|---|---|
| codex | WebSocket JSON-RPC（app-server） | 线程跟随、TUI 代理、steer 与 start 对账，从 `adapters/codex` 原样搬进宿主 |
| claude | stdio NDJSON（stream-json） | 按 request_id 关联 control_request；删除 relay |
| acp | stdio JSON-RPC（ACP） | initialize、session/new（cwd 只留在宿主）、prompt、cancel（notification）、request_permission（按 kind） |
| acp + grok flavor | 同上 | 只处理 argv、leader socket 和能力展平 |
| 其他 ACP agent | 同上 | 用 flavor 或注册表接入，不写新驱动 |

### 4.6 表面、客户端运行时与确认策略

**`deskhelm_client`**：每个进程一个连接 owner。

- 握手；带抖动的指数退避重连，上限 5 分钟，连接稳定后才重置；快照加游标同步，generation 变化时丢弃本地缓存。
- 视图模型：会话列表、待批准、交互流合并（乐观提示按 command_id 对账）、唯一一份失败文案。
- 命令：幂等键由调用方生成并在重试时保持，来源是浏览器、设备或语音 transcript id；结果带 command_id；审批永不自动重试。

**ControlCommand v2**：保留 v1 的安全信封（command_id、kind、目标三元组、issued_by、issued_at、expires_at、idempotency_key），新增两个字段：

- `evidence`：Click、Hold(ms)、SpokenExact(phrase)、Typed，说明这个决定是怎么做出的。
- `seen_seq`：做决定时看到的是哪一版投影。

审批 payload 改为 approval_id，取代复制 request_id、summary 和 expiry。dashboard 截断摘要这一类缺陷因此不再可能出现。

**DecisionPolicy** 在 core 里统一检查：

| 动作 | operator（web、dashboard） | device（键盘） | voice |
|---|---|---|---|
| submit_prompt | Typed | — | 转写，直接提交或复核后提交 |
| approve、reject | Click | Hold，阈值由硬件 ADR 定 | 由 D3 决定 |
| interrupt、focus | Click | Press | — |
| PTT 按下、松开、取消 | — | Press | 本地按键 |

**语音宿主与 VoiceTurns**：

- 状态：idle → capturing → transcribing →（review）→ submitted、cancelled 或 failed。按键边沿、目标、转写、提交、取消全部在语音宿主内，取代现在的四方分持。
- 本地右 Alt 直达 VoiceTurns，目标取推送来的焦点；同时向 Bridge 发 press 命令复核焦点，被拒绝就取消录音。来自键盘等其他表面的 PTT 经 Bridge 转给语音宿主，和会话宿主走同一套 effect 与回执。
- 取消立即作废结果，但识别资源保持占用，直到 provider 真正结束；超时不再提前回到 idle。
- 审批短语按 D3 处理，永不落成 prompt；“取消”只表示取消录音。
- 通知由 console 的 delta 派生（进入等待批准、等待输入、turn 终止），文案用 `runtime_name` 和项目名，按会话、turn_id、类别三者去重。
- 语音失败作为语音表面状态加一句固定播报，不再写进 transcript。

**web**：独立进程，用 operator 层连接。

- 浏览器鉴权：启动时生成一次性 token，换成 HttpOnly、SameSite=Strict 的 cookie；校验 Host 白名单和 Origin；只接受 `application/json`。`--allow-remote` 必须带 token。
- 推送视图模型：交互流在 Python 侧合并，`app.js` 只负责渲染。

**device（以后）**：device 宿主把 device 层投影映射成设备帧。

- 会话灯按 activity 和能力点亮；批准键只在焦点会话恰有一张可批准票据时可用。
- 设备回传 decide(approval_id, hold_ms)、PTT、interrupt，由宿主构造 ControlCommand v2；固件不直接说 NDJSON。
- 传输和 MCU 先立硬件 ADR，这是 AGENTS.md 的要求。

### 4.7 访问层级

| 层级 | 谁 | 可订阅 | 可发命令 |
|---|---|---|---|
| host | 会话宿主 | — | 只为自己打开的会话发 fact、回 effect 结果 |
| operator | web、dashboard、CLI | console、interaction（含内容） | 全部 |
| voice | 语音宿主 | console | submit_prompt、按 D3 的 approve 和 reject；执行 PTT 和播报 |
| device | device 宿主 | console（无内容） | approve 和 reject（Hold）、interrupt、focus、PTT |
| observer | 只读工具 | console | — |

层级在同一用户的 Unix socket 上是职责隔离，用来防误用、防越级，不是对抗同用户恶意进程的安全边界。真正的安全边界在 HTTP 入口，即现在的 web 和将来的远程访问。

### 4.8 测试

- **core**：不用 socket，注入时钟，确定性地覆盖审批只消费一次、焦点只显式变化、收据有界、Ambiguous 判定。
- **驱动契约套件**：同一组场景对每个驱动回放。
  - 场景：simple、multi_turn、busy_prompt、interrupt、interrupt_before_start、approval_allow、approval_reject、approval_overflow、process_exit、bridge_reconnect。
  - 回放格式只有三种条目：expect_outbound、emit_inbound、runtime_exit。
  - 录制要显式开启，只用公开提示，路径匿名化，附出处清单。录不到的失败（如额度用尽）用合成 transcript 并标注。
- **wire**：帧上限、握手总时限、慢订阅者断开。
- **e2e**：每种角色一条真实 socket 冒烟测试。

### 4.9 打包

一个 uv 工作区：根包声明全部 Python 包，去掉 PYTHONPATH 拼接。`voice/runtime` 通过路径依赖引用根包，模型依赖仍然只在 `voice/runtime`（AGENTS.md 第 12 条）。Bridge 保持纯标准库。

### 4.10 从 t3code 借鉴什么

| 借鉴 | 落在本方案的 |
|---|---|
| 决策与副作用分离、命令收据 | 单线程 core、哈希收据、effect 结果分类 |
| 快照加游标 | console 订阅 |
| 能力矩阵、降级策略、类型化的 unsupported | CapabilityMatrix、DecisionPolicy |
| app id 为主、provider id 为引用 | approval_id、turn_id、provider_ref |
| ProviderAdapter 契约、flavor、适配器踩坑清单 | Driver 契约、adapter kit、契约规则 |
| 通用 ACP 适配 | acp 驱动加 Grok flavor |
| 回放 transcript | 驱动契约套件 |
| 共享的客户端连接运行时 | `deskhelm_client` |
| 每个入口都鉴权 | web 鉴权 |

不借鉴：以持久事件日志为真相源；checkpoint、fork、切换 provider；替代厂商 UI；TypeScript 与 Effect 技术栈。

## 5. 与现有决策的冲突

| 文档 | 现有规定 | 本方案 | 处理 |
|---|---|---|---|
| ADR 0002、0004 | slot 在注册时分配，兼任容量 | slot 只是各表面的展示映射 | 取代 |
| ADR 0005 | 三种角色；订阅者只选一个平面；交互订阅无快照 | host 和 client 两种角色；console 快照加游标；交互窗口见 D1 | 修订 |
| ADR 0006 | 审批复制 id、摘要、到期；任何分派尝试都消费 | approval_id 加 seen_seq；只有可能已送达才消费 | 修订 |
| ADR 0007 | 租约连接加 attach 两条腿；能力由启动器声明 | 单一 `session_open`，连接即租约；驱动自报，Bridge 强制 | 取代 |
| ADR 0008、0031 | codex exec gateway；project registry | 见 D4 | 待定 |
| ADR 0009 | Bridge 进程内组合语音；语音事件只在进程内 | 独立语音宿主；公开无内容的语音状态 | 修订 |
| ADR 0017 | 不对外暴露 cancel | 暴露 PTT 取消 | 修订 |
| ADR 0033 | Bridge 拥有 TUI 代理 socket | 代理在 Codex 宿主内，语义不变 | 修订 |
| ADR 0036 | web 写作 “inside the Bridge process”；120/480 窗口在 web 进程；无鉴权 | 独立表面；窗口移入 Bridge（D1）；加鉴权 | 修订 |
| ADR 0038 | 进程内工厂、attach 帧、runtime 目录 | 宿主连接 | 取代 |
| ADR 0040 | relay socket 与 relay 型 runtime_kind；中文失败文案 | 删除 relay；统一失败分类 | 取代 |
| ADR 0042 | 文本去重，窗口 8 条或 1 条 | 宿主按 command_id 关联 | 修订 |
| AGENTS.md | “Rich interaction never updates StateStore” | 状态由 fact 的无内容字段推导；内容永不进入 device 层 | 改写措辞 |
| AGENTS.md 与架构文档 §6 | 语音短语可以批准，与语音默认不批准相互矛盾 | 见 D3 | 二选一 |
| `multimodal-agent-console.md` §2 | Device Projection（未实现） | console 投影加 device 层 | 更新 |

采纳后写一份 ADR 0043 作为架构基线，列出上表中被取代和修订的条目；此后的 ADR 只记录跨组件的决定。

## 6. 迁移路线

| 阶段 | 内容 | 退出条件 |
|---|---|---|
| 0 止血与基线（不依赖 D1–D4） | 基线提交（需你同意）。在现有代码上修复：路由改成三段式，即锁内决策、锁外调用 handler、锁内提交；Grok 未就绪时立即拒绝；补“审批与提示并发”的回归测试；未送达的审批不消费；Grok 审批按 kind 精确匹配；Grok 工具名和审批摘要只用 kind、不用 title；Grok cancel 改为 notification（需实测）；dashboard 提交完整摘要；web 鉴权；审批短语不落成 prompt；通知文案用 runtime_name。搭回放测试骨架，录制现有三家的场景，作为后续重构的对照 | 全量无硬件测试通过；回放覆盖 simple、multi_turn、interrupt、审批允许和拒绝 |
| 1 协议 v2 与单线程 core | protocol 包；core actor，含 SessionTable、哈希收据、ApprovalBook、DecisionPolicy 骨架、console 投影；server 只做 I/O；host 角色和 console 订阅与旧协议并存 | core 有不依赖 socket 的确定性测试；fake 宿主跑通全部场景；旧客户端不受影响 |
| 2 会话宿主（可与阶段 3 并行） | adapter kit；acp 驱动加 Grok flavor；Claude 驱动；`deskhelm agent` 变成宿主，删除 stdio relay。Codex 驱动和 TUI 代理整体搬进 `deskhelm codex` 宿主，行为不变，由回放测试守护。删除 attach 帧、runtime 目录、进程内工厂和启动器的 lsof/kill 补救 | 重启 Bridge 不中断任何 Codex TUI 或 Claude、Grok 进程；宿主重连后待批准项恢复 |
| 3 客户端运行时与表面 | `deskhelm_client`；迁移 dashboard、web、CLI；web 推送视图模型；`deskhelm voice` 语音宿主；Bridge 删除全部语音参数；删除旧状态平面、旧交互平面、客户端审批缓存和轮询 | 客户端重连后能看到并处理已挂起的审批；语音可以附着到已在运行的 Bridge |
| 4 清理与治理 | 按 D4 删除 legacy；写 ADR 0043；AGENTS.md 的架构规则收敛为不变量，细节移到 `protocol/` 和各组件 README；`protocol/` 换成 v2；Handoff.md 重写为当前状态 | 文档与代码一致；每个阶段都有提交 |
| 5 以后 | 硬件 ADR 与 device 宿主；远程访问 ADR | — |

预计可以删除或合并的模块：`stdio_relay.py`、`runtime_directory.py`、`agent_runtime.py`、`adapter_registry.py`；`session_registry.py` 与 `state_store.py` 并入 SessionTable；两个订阅模块合一；`display.py`；两套 WebSocket 实现中的一套；重复的 catalog 归一化。按 D4 还有 `codex_hook.py`、`agent_gateway.py`、`fake_agent_provider.py`、`project_registry.py` 和 `agent_io_bridge`。

## 7. 风险

- **Codex 审批竞争与迟到抑制**（ADR 0033）：搬迁时只移动、不改写，由现有的 `test_codex_tui_proxy` 和新回放测试守护。
- **线程跟随与 missing-rollout 重试**：同上。
- **PTY 两次 Ctrl-C 守卫与终端恢复**：宿主化时保持 `run-with-pty` 不变。
- **双栈期**：阶段 1 到 3 新旧协议并存，限定在阶段 4 统一删除，避免长期并存。
- **user bridge 单启动锁**：任何宿主或表面都可以拉起 user bridge，必须沿用现有的锁。
- **回放录制的隐私**：只用公开提示、匿名化、显式开启，提交前人工审阅。

## 8. 决策

2026-11-03 全部按【建议】采纳，记录在 ADR 0043。基线提交为 `ec128a3`，tag 为 `pre-architecture-v2`。

**D1 富内容的保留与可见范围**（宪法修订）

- (a) 保持现状：交互流纯实时，客户端重连后什么都没有。
- (b)【建议】Bridge 为每个会话在内存里保留一个有界的近期交互窗口，例如 200 条或 256 KiB；会话结束即丢弃，从不落盘，明确为非权威。客户端订阅时先收到窗口，再收实时事件。
  - 理由：Claude 和 Grok 会话没有别的界面，刷新页面就什么都看不到。web 进程今天已经在自己内存里保留 120/480 条（ADR 0036），这只是把同一个窗口挪到唯一的所有者那里。
- 子问题：operator 层是否可见命令行、命令输出和 diff？现状是三家都发布工具输出，Codex 还发布命令行和 diff，Claude 不发参数，Grok 经 title 泄露参数；AGENTS.md 和 ADR 0040 写的是不发布工具参数。
  - 【建议】operator 层统一可见，有界截断；device 层、voice 层和日志永不可见。

**D2 进程拓扑**（推翻 ADR 0033、0038、0040 的相关部分）

- 【建议】采纳：适配器和 TUI 代理移到每会话宿主，语音移到独立的语音宿主，Bridge 不 import 任何适配器和语音代码。
- 不采纳的代价：只修锁和契约，但重启或升级 Bridge 仍会中断所有会话，开语音时 Bridge 仍须运行在语音环境里。

**D3 语音能不能批准**

- (a)【建议，过渡期】保留精确短语批准，但只在焦点会话恰有一张可批准票据时生效，永不落成 prompt，“取消”只表示取消录音。硬件到位后，默认改为“语音提名，硬件长按确认”。
- (b) 语音只能提名，必须在屏幕或硬件上二次确认。
- (c) 语音不批准，只播报，与架构文档 §6 一致。

**D4 删除 legacy**

- 【建议】删除：`AgentEvent v1` 与旧状态平面、`codex_hook`、`emit`；codex exec gateway（ADR 0008）与 project registry（ADR 0031）；`agent-io` 别名和 `agent_io_bridge`。`simulate` 改为一个 fake 宿主。
- 如果其中有仍在使用的，请指出，保留那一项。

**前置：基线提交**

重构之前，先把当前工作区（约 1.4 万行未提交改动）提交成一个基线。是否提交、按主题拆成几个提交还是一个提交加 tag，由你决定。

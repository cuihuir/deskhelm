# Project Constitution

These principles govern all AI-assisted and agent-driven work in this
repository. When another instruction conflicts with this constitution, follow
the higher-priority user or system instruction and document the conflict when
it affects the result.

## 1. Use Visualizations Purposefully

When explaining complex workflows, architecture, dependencies, state changes,
or repeated comparisons, use the smallest effective visualization such as a
table, flowchart, timeline, or tree. Do not add diagrams to simple facts or
one-step operations merely for presentation.

## 2. Be Concise and Evidence-Based

Lead with conclusions and the evidence that materially supports them. Clearly
distinguish verified facts, reasonable inferences, and unknowns. Prefer project
source material, official documentation, and reliable primary sources. Never
present an unverified assumption as fact.

## 3. Stay Aligned With the User's Goal

Every analysis, recommendation, and change must serve the user's current
objective and respect the stated scope, constraints, and priorities. Do not
expand the task, alter the business goal, or add unrequested functionality for
technical completeness. When the goal conflicts with the current
implementation, explain the conflict and propose the smallest viable
resolution.

## 4. Ask Only for Critical Decisions

Do not interrupt the user when repository evidence or a reasonable low-risk
assumption allows progress. Ask for confirmation only when missing information
would materially change the result, expand scope, cause irreversible impact, or
require a key business decision. Briefly disclose consequential assumptions in
the result.

## 5. Use Subagents With Restraint

Use subagents only when work can be cleanly separated, parallel execution
materially improves efficiency, or an independent review or specialist
capability is valuable. Do not split simple work for appearance or assign
duplicate work to multiple agents. The primary agent owns integration, conflict
resolution, validation, and final delivery.

When a test or validation task is delegated to a subagent, use a Luna model for
that subagent. This does not require delegating tests that the primary agent can
run directly.

## 6. Keep Changes Focused

Limit code and documentation changes to the smallest scope required to complete
the current objective. Do not include unrelated refactors, formatting,
dependency upgrades, or directory changes. If broader work is necessary,
explain why and preserve existing interfaces, behavior, and user-owned work.

## 7. Validate Real Outcomes

Do not declare completion because code is present, reads correctly, or appears
finished. Run risk-appropriate builds, tests, execution checks, or output
inspections that verify the result the user actually needs. If validation
cannot be completed, state what remains unverified, why, and the residual risk.

## 8. Protect Existing Code and Data

Preserve existing code, uncommitted changes, configuration, and data. Never
overwrite, delete, revert, or reset user work without clear authorization.
Resolve destructive targets and impact before acting; ask when scope is
unclear. If unexpected changes appear during work, stop and notify the user.

## 9. Report Only Meaningful Progress

Report progress that affects user decisions: important findings, scope changes,
risks, validation results, and blockers. Do not stream mechanical operations,
repeat plans, or send status messages without new information. Final delivery
should emphasize outcomes, changed locations, validation, and necessary next
steps.

## 10. Keep AGENTS.md Current

Update `AGENTS.md` whenever project structure, critical commands, test
procedures, engineering constraints, or durable collaboration rules change.
Record only stable, reusable information that affects future work; do not add
temporary task history or one-off decisions. Check updates against the
repository and related documentation.

## 11. Maintain Handoff.md

After each identifiable phase of work, update the root `Handoff.md` so work can
stop and resume at any time. Record the current objective and status, completed
work, key decisions, changed files, validation results, remaining work, next
steps, risks, blockers, and essential context. Keep it as a concise statement
of current state rather than an append-only activity log. Never include
secrets, tokens, or sensitive credentials. Leave a short final state when a
task is complete.

## 12. Use uv for Python Dependencies

Use `uv` for every Python dependency resolution, environment creation,
synchronization, lock update, and dependency-backed command in this repository.
Do not use `pip`, `venv`, `virtualenv`, Conda, or direct interpreter package
installation. Keep Bridge dependencies minimal. Model-backed voice dependencies
remain in the separate `voice/runtime` uv project and synchronize only into
ignored or external storage; never add them to the Bridge package.

# DeskHelm Repository Guidelines

## Project Identity and Scope

DeskHelm is a local-first control surface for coding agents. The GitHub
repository and product name are `DeskHelm` / `deskhelm`. The pre-release
`agent-io` commands and `agent_io_bridge` module execution entry point are
temporary compatibility aliases under ADR 0003; do not introduce new public
interfaces under the legacy names.

The project is a monorepo with these responsibility boundaries:

```text
Agent runtimes
  -> adapters
  -> Bridge: sessions, state, targeting, and control routing
     -> Voice Gateway: PTT, ASR, TTS, playback, and voice notifications
     -> TUI, web, and desktop clients
     -> Physical Surface: HID, controls, RGB, displays, and device transport
```

DeskHelm integrates existing Agent runtimes. It must not become a new general
LLM or Agent framework, and it must not own an external Agent's canonical
conversation history.

## Project Structure

- `bridge/`: dependency-minimal local core service.
- `adapters/`: runtime-specific Codex, Claude Code, Gemini CLI, and other
  integrations.
- `voice/`: provider-neutral PTT, ASR, TTS, playback, and voice lifecycle core.
- `voice/native/whisper-cpp-shim/`: optional external whisper.cpp C-shim source
  and build notes; native libraries and models remain outside Git.
- `protocol/`: versioned state, interaction, control, and transport contracts.
- `configurator/`: device and client setup application.
- `hardware/`: electronics, PCB, mechanical, production, and test assets.
- `firmware/`: device firmware and bootloader integration.
- `tests/`: cross-component, compatibility, and hardware-in-the-loop tests.
- `docs/`: research, architecture, product, software, hardware, and ADRs.
- `tools/`: development and manufacturing utilities.

Keep third-party reference files outside version control under
`references/vendor/`. Keep model weights and generated binaries outside Git.

## Architecture and Protocol Rules

- Keep integrations Agent-agnostic and local-first.
- Preserve the separation between small `StateEvent` projections, rich
  `InteractionEvent` content, and targeted `ControlCommand` actions.
- Keep `AgentEvent v1` compatible until a documented migration exists.
- Use the single DeskHelm Unix socket for negotiated publishers, subscribers,
  and controllers. A connection has one fixed role after
  `client_hello` / `server_hello` negotiation.
- Negotiated state publishers use the `agent_event_v1` capability and add
  `message_type: agent_event`; the remaining payload stays compatible with
  `AgentEvent v1`.
- Negotiated publishers may also use `interaction_event_v1`; one publisher
  connection may negotiate both state and interaction capabilities.
- Modern adapter publishers negotiate `adapter_session_v1`, declare adapter
  and runtime versions plus capabilities, and register a complete session
  before publishing lifecycle-managed events.
- Bind modern sessions to a server-assigned publisher owner. Closing an old
  connection must not disconnect a session re-registered by a replacement.
- Lifecycle-managed state events update `StateStore` without invoking the
  legacy agent-only session observation path.
- Negotiated state subscribers use `state_subscription_v1`, receive an atomic
  sequence-zero snapshot, and then subscription-local ordered live updates.
- Negotiated interaction subscribers use `interaction_subscription_v1`,
  receive a sequence-zero start marker, and then live-only rich updates with no
  snapshot, history, or replay.
- A subscriber connection selects exactly one state or interaction plane.
  Rich interaction never updates `StateStore`, terminal projection, or hardware
  state.
- Limit legacy compatibility to connections whose first frame is an
  `AgentEvent v1`; legacy connections remain state publishers only.
- Limit UTF-8 NDJSON frames to 1 MiB and use bounded per-connection queues.
  A full subscriber queue is terminal; disconnect and require a fresh snapshot.
- Keep the subscriber limit below the total connection limit so long-lived
  readers cannot consume every publisher worker.
- Bound first-frame negotiation time so incomplete connections cannot retain
  workers indefinitely.
- Do not promise durable event history or replay. On reconnect or a sequence
  gap, clients request a fresh snapshot before consuming live events.
- Identify sessions with `agent_id + session_id + project_id`; treat `slot` as a
  presentation mapping only.
- Keep `project_id` opaque. Map it to a local Agent working directory only
  through the bounded operator-configured registry; never interpret protocol
  identity as a path or accept a working directory from an adapter. Once
  project mappings are configured, reject unmapped projects without fallback.
- Do not focus sessions implicitly. Only active sessions may be focused;
  disconnecting, replacing, releasing, or expiring the focused session clears
  focus, and restore does not re-focus automatically.
- Parse vendor formats at the adapter boundary and expose declared adapter
  capabilities.
- Keep runtime fixture provenance explicit: distinguish official documentation
  examples from synthetic boundaries and never claim a fixture was locally
  captured unless it was.
- Bound streams, records, queues, retries, and slow-subscriber behavior.
- Correlate tool calls, results, approvals, controls, and terminal events with
  stable identifiers.
- Every control must name `agent_id + session_id + project_id`, `issued_by`, an
  issue time, an expiry, and an idempotency key. Never target controls by slot.
- Negotiated controllers use `control_command_v1`; `client_id` must match every
  command's `issued_by`, and every structurally valid command receives a
  private-content-free correlated `control_result`.
- Scope control idempotency by `issued_by + idempotency_key`; a retry preserves
  the complete command identity and content.
- Never evict a live idempotency record to admit a new dispatch. Reject at
  capacity, retain ambiguous handler failures, and keep all control and approval
  records explicitly bounded.
- Never replay approval or rejection blindly. Consequential controls must name
  their target session and copy the pending request ID, summary, and expiry
  exactly. Approval and rejection are never automatically retried.
- Treat any approval dispatch attempt as consuming the pending request, even if
  its handler fails, because the downstream decision may already have applied.
- Control handlers must be explicit, non-blocking, and bounded. Missing handlers
  reject safely; never report dispatch success before a handler accepts work.
- Agent providers run behind the generic bounded gateway. Allow at most one
  active run per complete session, bound concurrent runs and retained provider
  session records, and do not create an unbounded prompt queue.
- Keep vendor process construction and event parsing in `adapters/`. A provider
  must produce one normalized terminal outcome and honor cancellation and
  timeout of its owned work.
- Integrate manually operated Codex TUI sessions only through a
  DeskHelm-managed TUI proxy, `codex app-server`, and `codex --remote`
  connection. Keep the TUI proxy frontend distinct from the app-server
  upstream socket and keep the adapter on a separate observer/control
  connection. Do not inject terminal keystrokes or claim that an unrelated
  plain Codex process can be attached after launch.
- Keep the Codex app-server adapter Alpha-only while its WebSocket transport is
  experimental. Use bounded local Unix WebSocket frames, queues, requests,
  pending approvals, pending input, timeouts, and reconnect backoff. Keep
  transient TUI-proxy queue saturation as bounded backpressure: pause upstream
  reads with stop-aware polling and rely on local socket backpressure rather
  than dropping, reordering, or disconnecting a healthy streamed turn. Keep
  voice approval decisions on a distinct bounded priority queue so notification
  bursts cannot reject a decision. After a voice-first response, retain bounded
  duplicate-response suppression through `serverRequest/resolved` and until the
  late TUI response or a fixed expiry; notify approval resolution exactly once
  so a later approval in the same turn can announce and route independently.
  Allow at most one additional short-lived TUI connection for Codex's `/resume`
  session picker. It may forward app-server traffic but must never replace the
  primary TUI's approval channel or participate in voice approval routing. A
  confirmed picker resume must cause the separate adapter observer to attach to
  that same thread before later voice controls are routed.
  Keep thread attachment separate from event subscription: attach directly to
  one unambiguous loaded thread so a new empty remote TUI accepts controls before
  its first rollout exists, then retry bounded `thread/resume` calls until that
  connection is subscribed. A missing rollout is retriable only for a
  discovered thread; explicit thread failures remain fail-closed. Ignore a
  bounded, positively identified ephemeral internal thread, but require an
  explicit selector before choosing among multiple non-ephemeral threads.
- Keep `deskhelm codex --tui` as a convenience launcher for the exact local
  remote-TUI command. `deskhelm alpha` remains a hidden alias with the same
  arguments. It must require an interactive terminal. Default Codex startup joins the
  user bridge from ADR 0038 and, on exit, detaches that session and stops this
  directory's Codex app-server. It must not stop or unlink the user bridge.
  Explicit `--socket` still owns and stops its private bridge. The launcher
  PTY must translate a first raw or Kitty-protocol Ctrl-C into clear-input and
  forward Ctrl-C only on a second press within a bounded confirmation window;
  timeout or other input resets the guard.
- Keep `tools/deskhelm-user-launcher` as a thin source-checkout entry point. It
  must preserve the invoking working directory, use the already synchronized
  root environment without dependency installation, and use the bounded PTY
  relay for every real `--tui` launch so Codex retains terminal
  semantics plus the two-press Ctrl-C guard. Default `deskhelm codex` omits `--socket`
  and joins the user bridge. The launcher must not inject a private socket
  and must not signal the user bridge process group. Leftover Codex sockets
  may be cleared by signaling only this directory's app-server. Core TUI
  startup must not consult `project-secrets`. Only `--voice` dynamically
  injects Alpha voice settings through the checkout's encrypted profile
  without storing their values.
  Because `project-secrets` output forwarding is line-buffered, the relay must
  render directly to the controlling terminal and perform boundary-safe
  streaming redaction of injected values itself.
- When an Alpha lifecycle registration declares interaction or approval event
  capabilities, its publisher connection must also negotiate
  `interaction_event_v1`; do not retry a deterministic capability mismatch as
  Bridge startup unavailability.
- Keep app-server approval and input notifications private-content-free. Voice
  may map only the bounded exact approval/rejection phrase sets implemented at
  the composition boundary to one non-expired target approval, must use a
  one-shot response, and must leave persistent, ambiguous, multi-question, and
  secret interactions to the Codex TUI. For Codex permission approvals, grant
  only the requested subset for the current turn; never persist voice-granted
  permissions to the session.
- Proxy managed-TUI command and file approvals on their original JSON-RPC
  response channel. Forward the request to the TUI, retain only bounded
  correlation metadata, clear DeskHelm state when the keyboard answers first,
  and suppress a duplicate keyboard response when voice answers first. Proxy
  disconnect, expiry, or capacity must fail closed without inferring a decision;
  excess approvals remain keyboard-actionable.
- Map Codex `waitingOnApproval` thread status to notification-only `attention`
  state, never by itself to an actionable `approval_request`. The managed TUI
  proxy supplies the real correlated request; status-derived attention remains
  a fallback with no request ID. Deduplicate its fixed voice notice with a
  later correlated request until the status clears.
- A Codex `turn/steer` whose error says there is no active turn clears that
  remembered turn and sends one `turn/start` with the same prompt. Review and
  compact steer errors, and any other steer error, still publish
  `codex_control_failed` and do not start a turn. ADR 0041. Do not add a second
  transcript channel for this recovery. Output stays on the existing
  interaction events for every attached agent.
- Publish each prompt DeskHelm accepts once, as a complete user message on
  that interaction plane. The correlation id is the control command id.
  Codex remembers the last eight accepted texts, and Claude and Grok remember
  the latest one; one matching vendor echo is not published again. A different
  vendor user item keeps its own id. ADR 0042.
- Publish attached-thread Codex `item/started` and `item/completed`
  notifications as bounded interaction events. Keep token deltas opted out.
  Do not publish reasoning, working directories, image bytes, local image
  paths, or tool arguments. Approval and user-input notifications stay
  private-content-free.
- Default DeskHelm sessions share one user bridge at `default_socket_path()`
  (`bridge.sock`), ADR 0038. Default `deskhelm codex` attaches one session
  there and leaves the bridge running. Explicit `--socket` keeps a private
  bridge. Codex app-server and TUI proxy sockets stay directory-scoped and
  are never derived from the user bridge. Attach carries no working
  directory. The directory holds at most 32 runtimes and cannot be combined
  with a single agent provider. The first `deskhelm codex --voice` must start the
  user bridge; a bridge already running without voice is not upgraded.
- `deskhelm agent claude` and `deskhelm agent grok` start that vendor in the
  current directory, register an adapter session on the bridge named by
  `--socket`, and attach one confined stdio relay. Prompts stay on the vendor
  stdin. Omitted `runtime_kind` remains the Codex socket pair. `claude_stream`
  and `grok_stdio` name `relay_socket` only. `session_query` includes adapter
  identity and declared `AdapterCapability` values. Catalog
  `agent_capabilities` carries vendor-declared names and is not a new enum
  value. Web, the dashboard, and later clients read those same fields. Agents
  DeskHelm did not start stay off the page. ADR 0040.
- The loopback web client in ADR 0036 is a replaceable Bridge client. One web
  process holds one state subscription and one interaction subscription and
  fans them out to browsers. Omitting `--socket` reads the user bridge and
  lists every attached session. Browsing a thread does not focus it. The page
  does not create sessions, read transcript files, or promise replay. Bind
  `127.0.0.1` unless `--allow-remote` is explicit.
- `session_catalog_query` is a read-only controller frame for one active
  session's models, MCP status, skill names, and vendor-declared capability
  tokens. The Codex adapter is the
  catalog provider and answers only for its attached thread. `configure_session`
  stores a model and reasoning override for the next `turn/start` after a
  catalog read from the last 120 seconds. It does not write Codex config, call
  MCP tools, return skill bodies, or follow a vendor cursor. The web 能力 panel
  fetches on open or refresh and does not poll. `/compact`, `/plan`, and
  `/fast` remain deferred.
- Never place prompts in process command-line arguments. Prefer bounded stdin,
  suppress private stderr from ordinary logs, and terminate the owned process
  group on cancellation or timeout.
- Do not log prompts, source code, tool arguments, raw Agent events, audio, or
  credentials by default.
- Keep PyTorch, CUDA, model weights, and provider-specific voice dependencies
  outside the core Bridge.
- Keep the whisper.cpp fallback behind the DeskHelm-specific ABI in
  `voice/native/whisper-cpp-shim/`; require explicit library/model paths and
  never silently fall back from another ASR provider.
- Keep `voice/` independent of Bridge. Convert transcripts and speech controls
  only at the Bridge composition boundary.
- Allow one active PTT capture/transcription flow, bound speech queues, and
  require providers to honor cancellation. Starting PTT cancels only current
  interruptible playback; queued or active speech remains session-targeted.
- External PTT release must copy the matching press command ID and match the
  complete active session target. A stale, idle, or cross-session release must
  fail without changing capture state; do not model external PTT as a toggle.
- The right-Alt PTT listener reads `/dev/input/deskhelm-ptt-kbd` without
  `EVIOCGRAB`. That node is keyd's virtual keyboard, and a grab hides every
  key from the desktop. One listener is a non-blocking flock on
  `deskhelm_runtime_directory() / "ptt.lock"`. A second listener fails closed.
  Right Alt still reaches the compositor. DeskHelm does not inject keys or
  open the physical keyboard. ADR 0039.
- Keep model-backed local voice disabled by default. Validate selected audio
  devices and required external artifacts before Bridge startup, keep model
  imports/weights lazy, and do not describe batch PTT capture as VAD integration.
- Keep the reproducible optional Paraformer/Piper/Kokoro CPU environment pinned in
  `voice/runtime/pyproject.toml` plus its committed `uv.lock`, and synchronize
  it only into ignored or external storage with `uv`. Do not add it to Bridge
  dependencies.
- Preserve raw and normalized transcripts separately. Ordinary lifecycle and
  failure events must not contain audio, transcript, prompt, or speech text.
- Controlled live ASR diagnostics must use versioned public reference phrases,
  suppress provider stdout/stderr, retain PCM and recognized text only in
  memory, and publish only signal, aggregate speech activity, accuracy, length,
  timing, and fixed failure metadata. Input-level hints are provisional and
  must not change system gain. Diagnostic VAD must never gate or alter ASR.
  Do not interpret a live ASR result until the user confirms whether they spoke
  the prompted phrase during that capture.
- Keep voice benchmark corpora and observation formats versioned. Bound each
  NDJSON observation to 1 MiB, each result file to 64 MiB, and each run to
  10,000 observations. Record exact provider/model versions and licenses plus
  an anonymous system profile.
- Model streaming PCM as complete, contiguous chunks of at most 1 MiB with one
  immutable format and absolute frame positions. Open one independent VAD
  session per stream, emit ordered alternating frame-positioned speech
  boundaries, and flush it explicitly at end of stream.
- Voice Gateway capture prefers `StreamingCaptureProvider`, validates format and
  continuity, and aggregates under fixed chunk, byte, and duration bounds until
  PTT release. Preserve legacy batch provider compatibility.
- Keep live VAD optional, disabled by default, and advisory until a later ADR.
  It may publish bounded alternating speech boundaries with absolute frame
  positions, but must not stop or trim capture, skip final ASR, or publish
  partial transcripts. Emit one privacy-safe fixed failure and continue the
  PTT path when VAD startup, processing, validation, flushing, or cleanup fails.
- Keep VAD samples bounded to 100,000 chunks, 256 segments, and 64 MiB PCM.
  Persist only derived segmentation/timing/resource observations, never raw PCM
  or provider exception text, unless separately approved under corpus rules.
- Keep external VAD audio reproducible through a versioned manifest containing
  pinned revisions, HTTPS source URLs, SHA-256 checksums, licenses, and explicit
  composition recipes. Keep downloaded/prepared audio and model files ignored.
- Keep WebRTC and Silero dependencies lazy and outside Bridge requirements.
  Every Silero stream resets recurrent state and context even when providers
  share an immutable ONNX Runtime session.
- Keep Paraformer/FunASR/PyTorch dependencies lazy and outside Bridge
  requirements. Pin benchmark model revisions and checksums, use one streaming
  cache per transcription, serialize access to a shared model, and distinguish
  offline first-partial estimates from live capture-to-UI latency.
- Keep SenseVoice/sherpa-onnx dependencies lazy and outside Bridge requirements.
  Treat SenseVoice as final-only, serialize the shared recognizer, report no
  first partial, and do not describe VAD-segmented offline decode as streaming.
  Review FunASR Model License 1.1 before packaging or production selection.
- Treat a new ASR request as the retry boundary: release provider locks on
  failures, never retry recognition implicitly, and check cancellation at each
  provider's documented inference boundary. Re-resolve default audio devices
  from fresh inventory snapshots; manual stable device names fail closed when
  unavailable and never fall back silently.
- Keep Piper and Kokoro dependencies lazy and outside Bridge requirements. Pin
  runtime/model revisions, licenses, artifact sizes, and checksums. Piper is the
  initial low-latency notification baseline; Kokoro remains the quality
  candidate, and neither is the final production selection.
- Treat TTS first audio as the first complete provider chunk. Do not describe
  it as PCM-frame streaming or claim cancellation during one model inference.
- Review Piper's GPL-3.0-or-later runtime together with the repository license
  and packaging plan before bundling or distributing it.
- Do not commit microphone captures, generated speech, model output, or local
  benchmark results without explicit provenance, consent, and redistribution
  terms.
- The initial local audio path follows the computer's current PipeWire default
  source and sink. Allow an optional stable source/sink name override; if an
  override is set but missing, fail explicitly instead of falling back. Do not
  persist numeric PipeWire object IDs or add Opus to this local path.
- Represent the local PipeWire path as complete-frame raw S16LE PCM with an
  explicit rate and channel count. Keep `pw-cat` providers bounded by bytes and
  duration, own their process group, suppress private stderr, and terminate then
  kill within a bounded grace period.
- Allow a validated `pw-cat` command prefix at the composition boundary. The
  verified Distrobox prefix is `host-spawn -no-pty pw-cat`; native execution
  defaults to `pw-cat`.
- Keep local audio discovery bounded and read-only. Resolve defaults through
  PipeWire/WirePlumber, persist only stable node names, and fail rather than
  fall back when a manual source or sink is unavailable.
- Treat input/output diagnostics as explicit user actions. Discard diagnostic
  PCM after deriving signal metadata and generate only short bounded low-volume
  output tones.
- Once DeskHelm hardware audio exists, prefer the connected DeskHelm keyboard
  microphone over the computer default unless the user selected another source.
  Keep manual selection highest priority and document disconnect fallback in an
  ADR before implementing it.
- Treat ESP32-S3 plus Opus as a researched future direction, not a selected MCU
  or frozen transport. Record an ADR before hardware or wire implementation.

Record significant protocol, transport, concurrency, framework, MCU, or
licensing decisions in an ADR before implementation spreads across components.

## Build, Test, and Development Commands

The Phase 0 Bridge requires Python 3.11 or newer and has no runtime
dependencies. A repository-wide build system has not been selected; do not add
a placeholder task runner without an ADR.

Current commands:

- `uv sync --locked`: synchronize the dependency-minimal root development
  environment.
- `uv sync --extra ptt --locked`: add the optional host-local `evdev` runtime
  for the explicit right-Alt PTT controller.
- `cd voice/runtime && uv sync --locked --python 3.12`: create or synchronize
  the ignored, pinned optional local-voice runtime. Run this from its project
  directory so uv resolves the local-voice lock rather than the root lock.
- `deskhelm codex --check`: preflight the managed Codex startup without audio.
- `deskhelm codex --tui`: run the managed Codex TUI from the current project
  directory and attach it to the user bridge. The launcher requires no project
  secrets or voice runtime, uses a bounded terminal relay for the two-press
  Ctrl-C guard, and routes the TUI through the approval-aware local proxy.
  On exit it detaches this session and stops this directory's Codex app-server.
  It leaves the user bridge running. Explicit `--socket` still stops that
  private bridge.
- `deskhelm codex --tui --yolo`: explicitly launch that managed Codex TUI with
  Codex's full-access, no-approval mode. This bypasses the normal sandbox and
  approval boundary; use only when that level of access is intended.
- `deskhelm codex --tui --voice`: add local Paraformer voice, directory-selected
  Piper or Kokoro TTS, and the explicit right-Alt PTT controller. A Kokoro
  directory uses `zf_xiaobei` for Chinese and `af_heart` for English. Only this
  mode loads
  `DESKHELM_ALPHA_ASR_MODEL_DIRECTORY`, `DESKHELM_ALPHA_TTS_DIRECTORY`, and the
  optional `DESKHELM_ALPHA_VOICE_SOURCE` and
  `DESKHELM_ALPHA_VOICE_SINK` through `project-secrets`.
- `deskhelm codex --check --voice`: preflight the optional local-voice models,
  runtime, PipeWire selection, and PTT device without opening audio or loading
  model weights. A missing configured source fails without fallback.
- `deskhelm focus --socket <bridge.sock> --agent-id <id> --session-id <id>
  --project-id <id>`: mark the session currently visible in a UI as the
  Bridge's explicit focus before using `deskhelm ptt --focused`.
- `PYTHONPATH=bridge python3 -m deskhelm_bridge dashboard --socket <bridge.sock>`:
  run the dependency-free multi-session TUI Dashboard. It consumes state and
  interaction subscriptions, queries complete session metadata, and supports
  explicit focus plus targeted interrupt without owning Agent processes.
- `PYTHONPATH=bridge python3 -m deskhelm_bridge agent <claude|grok>
  --socket <bridge.sock>`: start that vendor in the current directory and
  attach it to the named bridge. The prompt stays on stdin. Codex remains
  `deskhelm codex --tui`. The running user bridge must already contain the
  relay factory; restart it before the first `deskhelm agent` against
  `bridge.sock`.
- `PYTHONPATH=bridge python3 -m deskhelm_bridge web [--socket <bridge.sock>]`:
  run the loopback Codex-layout web client from ADR 0036. Open
  `http://127.0.0.1:8766`. Omitting `--socket` reads the user bridge and lists
  every attached session. An explicit `--socket` selects one bridge. An empty
  path is rejected. Browsing a session does not change Bridge focus. The page
  shows live interaction only and sends explicit focus, interrupt,
  submit_prompt, approve, and reject controls. The 能力 panel reads the
  session catalog and sends `configure_session` for the next new turn. It
  does not create sessions or own Agent history. The bind stays on
  `127.0.0.1` unless `--allow-remote` is set.
- After linking `tools/deskhelm-user-launcher` into the user's executable path,
  run these commands directly from the Agent project directory; the launcher
  preserves that directory as `--agent-workdir`.
- `PYTHONPATH=bridge:adapters/codex uv run --project voice/runtime --locked
  --no-sync python -m unittest tests.test_client_voice_status
  tests.test_codex_tui_proxy tests.test_codex_app_server_adapter
  tests.test_codex_app_server_fixtures tests.test_voice_integration
  tests.test_local_voice_config tests.test_local_alpha -v`: run the focused
  managed-TUI approval, consecutive-turn, Alpha topology, TTS selection, and
  exact voice-decision regressions from the isolated runtime.
- `PYTHONPATH=bridge python3 -m unittest tests.test_runtime_directory
  tests.test_paths tests.test_local_alpha -v`: validate attached-runtime
  dispatch, the user-bridge web socket, and the default Alpha join command
  without using the operator's bridge socket.
- `PYTHONPATH=bridge:adapters/claude:adapters/grok python3 -m unittest
  tests.test_runtime_directory tests.test_stdio_relay tests.test_agent_streams
  tests.test_session_catalog tests.test_bridge_e2e tests.test_web_client -v`:
  validate relay attach, Claude and Grok normalization, session identity, and
  the web supervision page without using the operator's bridge socket.

- `PYTHONPATH=bridge python3 -m deskhelm_bridge bridge --plain`: run the Bridge.
- `PYTHONPATH=bridge python3 -m deskhelm_bridge bridge --max-connections 16`:
  run with an explicit bounded connection limit.
- `PYTHONPATH=bridge python3 -m deskhelm_bridge bridge --max-subscribers 8
  --subscriber-queue-frames 8`: run with explicit subscription bounds.
- `PYTHONPATH=bridge python3 -m deskhelm_bridge bridge
  --control-idempotency-entries 1024 --control-approval-records 1024`: run with
  explicit control-state bounds.
- `PYTHONPATH=bridge python3 -m deskhelm_bridge simulate`: emit demo events.
- `PYTHONPATH=bridge:voice python3 -m deskhelm_bridge audio status --list`:
  resolve and list local PipeWire devices without opening audio.
- `PYTHONPATH=bridge:voice python3 -m deskhelm_bridge audio test-input
  --seconds 2`: explicitly test the selected input and discard captured PCM.
- `PYTHONPATH=bridge:voice python3 -m deskhelm_bridge audio test-output`:
  explicitly play a short bounded low-volume tone on the selected sink.
- `PYTHONPATH=bridge:adapters/codex python3 -m deskhelm_bridge bridge
  --agent-provider codex --agent-workdir "$PWD"`: run the opt-in text-only Codex
  gateway with its default read-only sandbox.
- `PYTHONPATH=bridge:adapters/codex python3 -m deskhelm_bridge bridge
  --agent-provider codex --agent-project deskhelm=/path/to/deskhelm
  --agent-project other=/path/to/other`: run strict bounded multi-project
  routing; unmapped `project_id` values fail before provider work.
- `PYTHONPATH=bridge:adapters/codex python3 -m unittest
  tests.test_codex_app_server_transport tests.test_codex_app_server_adapter
  tests.test_codex_app_server_fixtures -v`: validate the bounded Unix WebSocket,
  thread resume, approval-attention state, approval/input normalization, and
  versioned JSON-RPC fixtures without a model request.
- `PYTHONPATH=bridge:adapters/codex python3 -m unittest
  tests.test_session_catalog tests.test_control tests.test_web_client
  tests.test_codex_app_server_adapter -v`: validate the session catalog, model
  override, loopback web client, and synthetic Codex thread-item projection
  without a model request.
- `PYTHONPATH=bridge:voice python3 -m deskhelm_bridge bridge --voice-provider
  local <explicit-model-and-resource-options> [--voice-vad-provider webrtc]`:
  run the provisional local voice composition from an environment containing
  its optional runtimes; live VAD remains disabled unless selected explicitly.
- `PYTHONPATH=bridge python3 -m unittest tests.test_voice_gateway
  tests.test_voice_integration -v`: run the no-hardware Voice Gateway tests.
- `PYTHONPATH=bridge python3 -m unittest tests.test_pipewire_providers -v`: run
  deterministic PipeWire provider tests without opening audio devices.
- `PYTHONPATH=bridge python3 -m unittest tests.test_audio_config -v`: run
  deterministic audio discovery, selection, and diagnostic tests.
- `PYTHONPATH=bridge python3 -m unittest tests.test_local_voice_config -v`: run
  local voice composition preflight tests without audio or model runtimes.
- `PYTHONPATH=bridge python3 -m unittest tests.test_local_voice_live_tool -v`:
  validate live diagnostic bounds and privacy-safe summaries without audio.
- `PYTHONPATH=bridge python3 -m unittest
  tests.test_whisper_cpp_provider -v`: run the optional provider tests with a
  fake native shim; no whisper.cpp checkout or model is required.
- `PYTHONPATH=bridge python3 -m unittest
  tests.test_whisper_cpp_benchmark_tool -v`: validate pinned-model metadata
  and benchmark-runner bounds without loading a native model.
- `PYTHONPATH=voice python tools/run-whisper-cpp-benchmark.py --model
  <base|small> --model-path <ignored-ggml-model> --library-path
  <external-shim> --prepared <prepared-asr-set> --observations <results.ndjson>
  --summary <summary.json>`: run the explicit external whisper.cpp benchmark;
  keep models, native libraries, observations, and summaries outside Git.
- `PYTHONPATH=voice /ignored/py312/bin/python
  tools/run-local-asr-diagnostic.py --live-audio --asr-model-directory
  <ignored-model> --utterance-id zh-repeat-01 <audio-options>`: run one bounded
  public-phrase microphone/ASR diagnostic without playback or transcript output.
  Repeat `--utterance-id` at most eight times for a fresh-capture batch; confirm
  every phrase separately after the run.
  For chat-driven synchronization, add `--await-phrase-ready` and send one
  exact `ready` line on stdin after each displayed phrase; each wait is bounded.
- `PYTHONPATH=voice python3 -m deskhelm_voice.benchmark score-asr --corpus
  voice/benchmarks/utterances-v1.json --observations <results.ndjson>`: score a
  bounded ASR benchmark run.
- `PYTHONPATH=voice python3 -m deskhelm_voice.benchmark summarize-vad
  --observations <results.ndjson>`: summarize bounded VAD observations.
- `PYTHONPATH=voice python tools/prepare-vad-benchmark.py --manifest
  voice/benchmarks/vad-external-v1.json --artifact-root
  references/vendor/vad-bench/run-v1`: verify and prepare the pinned external
  VAD set under ignored storage.
- `PYTHONPATH=voice python tools/run-vad-benchmark.py --provider webrtc
  --manifest voice/benchmarks/vad-external-v1.json --prepared
  references/vendor/vad-bench/run-v1/prepared --observations
  voice/benchmarks/results/webrtc-v1.ndjson`: run the lightweight baseline from
  an isolated environment that provides its optional runtime.
- `PYTHONPATH=voice uv run --project voice/runtime --locked --no-sync python
  tools/prepare-vad-benchmark.py --manifest
  voice/benchmarks/vad-conversation-noise-v2.json --artifact-root
  references/vendor/vad-bench/run-v2`: verify and prepare the pinned AMI plus
  ESC-10 conversation/noise set under ignored storage.
- `PYTHONPATH=voice uv run --project voice/runtime --locked --no-sync python
  tools/run-vad-benchmark.py --provider webrtc --sweep --manifest
  voice/benchmarks/vad-conversation-noise-v2.json --prepared
  references/vendor/vad-bench/run-v2/prepared --observations
  voice/benchmarks/results/webrtc-v2-sweep.ndjson --summary
  voice/benchmarks/results/webrtc-v2-sweep-summary.json`: run all four bounded
  WebRTC profiles; use the same runner with `--provider silero`, `--sweep`, and
  an explicit ignored model path for the three pinned Silero threshold pairs.
- `PYTHONPATH=voice uv run --project voice/runtime --locked --no-sync python
  tools/run-live-vad-diagnostic.py --live-audio --seconds 3 --provider webrtc
  --webrtc-mode 2 --await-phrase-ready`: run one explicit privacy-safe live VAD
  timing diagnostic using the fixed public phrase; send one exact `ready` line
  to start the bounded capture. It reports aggregate signal level plus VAD
  timing/activity; PCM is retained only in memory and discarded.
- `PYTHONPATH=voice python tools/prepare-asr-benchmark.py --manifest
  voice/benchmarks/asr-external-v1.json --artifact-root
  references/vendor/paraformer-bench/run-v1`: verify and prepare the pinned ASR
  set under ignored storage.
- `PYTHONPATH=voice python tools/run-asr-benchmark.py --provider
  <paraformer|sensevoice> --manifest
  voice/benchmarks/asr-external-v1.json --prepared
  references/vendor/paraformer-bench/run-v1/prepared --model-directory
  <ignored-model-snapshot> --observations <results.ndjson> --summary
  <summary.json>`: run a pinned ASR baseline from its isolated runtime.
- `PYTHONPATH=voice python tools/prepare-tts-benchmark.py --manifest
  voice/benchmarks/tts-candidates-v1.json --artifact-root
  references/vendor/tts-bench/run-v1`: verify and prepare pinned TTS artifacts
  under ignored storage.
- `PYTHONPATH=voice python tools/run-tts-benchmark.py --candidate
  piper-chaowen-medium --manifest voice/benchmarks/tts-candidates-v1.json
  --prepared references/vendor/tts-bench/run-v1/prepared --corpus
  voice/benchmarks/utterances-v1.json --observations <results.ndjson> --summary
  <summary.json>`: run a pinned TTS candidate from its isolated runtime.
- `PYTHONPATH=bridge uv run --project voice/runtime --locked --no-sync python -m
  unittest discover -s tests -v`: run the full no-hardware suite from the
  already synchronized pinned uv environment; model imports remain lazy.
- `git diff --check`: detect whitespace errors.
- `find docs -name '*.md' -type f`: list documentation for review.
- `rg '<term>' .`: search code and documentation.

Document component-specific commands in that component's `README.md`. Add a
repository-level wrapper only when multiple implemented stacks require it.

## Coding and Naming Conventions

Follow `.editorconfig`: UTF-8, LF line endings, a final newline, two-space
indentation, and no trailing whitespace. Makefiles use tabs. Prefer descriptive
names over abbreviations.

- Directories and files: `kebab-case`.
- ADRs: `docs/decisions/NNNN-short-title.md`.
- Dated research: `docs/research/YYYY-MM-DD-topic.md`.
- Protocol fields and configuration keys: `snake_case` unless an adopted
  ecosystem requires otherwise.
- Tests: name after observable behavior.

## Testing and Compatibility

Add tests beside their component or under `tests/` for cross-component
behavior. Protocol and adapter changes require captured or synthetic fixtures,
including producing runtime versions where applicable. Test malformed input,
unknown events, cancellation, queue limits, and terminal states, not only happy
paths.

Firmware changes should include hardware-in-the-loop notes when necessary.
Record every untested hardware or model assumption explicitly.

## Git and Review

Use focused commits with short imperative subjects, optionally with a
conventional prefix, such as `protocol: define interaction envelope`. Do not
mix unrelated formatting, refactors, generated artifacts, or dependency updates
into a feature commit.

Before committing, run the relevant tests and `git diff --check`. Pull requests
should explain the problem, approach, validation, affected components, risks,
and linked ADRs. Include screenshots, recordings, schematics, or renders for
visible changes.

Do not commit secrets, credentials, generated binaries, model weights, vendor
archives, or files with unclear commercial licensing.

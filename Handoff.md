# Handoff

Date: 2026-08-18

Project: DeskHelm

Repository: <https://github.com/cuihuir/deskhelm>

## Current Objective and Status

Build the no-hardware DeskHelm software path before committing to physical
devices:

```text
Bridge state and sessions
  -> interaction and control protocols
  -> text-only Agent gateway
  -> PTT, ASR, and interruptible TTS
  -> physical controls
```

Phase 0 is working and pushed to `main`. The Bridge has a Unix socket transport,
normalized `AgentEvent v1`, CLI simulator, Codex hook adapter, `StateStore`,
in-process subscriptions, `SessionRegistry`, terminal projection, bounded
concurrent connections, and negotiated state publishers. `InteractionEvent v1`
and `ControlCommand v1` are implemented and covered by compatibility fixtures,
and negotiated state subscribers receive atomic snapshots followed by ordered
live updates. Negotiated interaction publishers and bounded live-only
interaction subscribers are implemented without entering state projection.
`ControlRouter`, bounded control state, correlated results, and negotiated
controller connections are implemented. Modern adapter publishers can now
declare capabilities and drive complete session registration, disconnect,
restore, and release lifecycle with connection ownership. Versioned Codex JSONL
evidence is present. The opt-in bounded text Agent Gateway now handles targeted
prompt submission and interruption, streams normalized Codex JSONL interactions,
and supports timeout, cancellation, and process-local resume. The isolated,
bounded Voice Gateway skeleton is now implemented with fake capture, ASR, TTS,
and playback providers. Bridge composition completes the no-hardware
`PTT -> transcript -> Agent -> speech` path and routes targeted speech controls.
The local voice benchmark foundation now has a versioned synthetic corpus,
bounded provider-neutral runners and NDJSON observations, accuracy/latency/
resource summaries, and explicit licensing identity. PipeWire capability is
verified on the development machine; the PCM and process/recovery contract is
the next implementation boundary.

## Completed Work

- Created and pushed the initial project baseline.
- Selected `DeskHelm` as the product and repository name.
- Migrated the distribution, primary Python package, CLI, Codex hook CLI, and
  runtime socket path to DeskHelm names under ADR 0003.
- Preserved temporary `agent-io`, `agent-io-codex-hook`, and
  `python -m agent_io_bridge` compatibility entry points.
- Added repository Git attributes, ignore rules, local commit identity, and an
  HTTPS `origin`.
- Separated Bridge state storage, session projection, and terminal rendering.
- Added explicit session lifecycle and focus semantics: register, restore,
  focus, disconnect, release, and expiration.
- Accepted ADR 0005: one negotiated Unix socket with fixed connection roles,
  bounded frames and queues, legacy first-frame compatibility, and
  snapshot-based reconnect recovery.
- Replaced the sequential accept/read loop with a 16-connection bounded worker
  pool, configurable through `--max-connections`.
- Added byte-accurate 1 MiB UTF-8 NDJSON frame enforcement and process-local
  stream identifiers.
- Implemented `client_hello` / `server_hello` for negotiated publishers using
  the `agent_event_v1` capability and self-describing `agent_event` frames.
- Added a validated `send_negotiated_event` Python client path for integrations.
- Added structured protocol errors for invalid negotiation, unavailable roles
  or capabilities, and invalid negotiated frames.
- Preserved no-handshake `AgentEvent v1` clients, including continuing after a
  malformed event on an established legacy connection.
- Enabled negotiated `state_subscription_v1` clients with an atomic current
  snapshot followed by subscription-local ordered state updates.
- Added a separate subscriber limit below the total connection limit, an
  8-frame non-blocking queue per subscriber, a two-second first-frame deadline,
  and a two-second subscriber write deadline.
- Added terminal recovery behavior for slow, read/write-invalid, oversized, or
  capacity-exceeding subscriptions; reconnect always starts from a new snapshot.
- Enabled negotiated `interaction_event_v1` publishers and
  `interaction_subscription_v1` live subscribers with bounded non-blocking
  queues and no retained history.
- Allowed one adapter publisher connection to negotiate both state and rich
  interaction capabilities while requiring each subscriber connection to
  select exactly one plane.
- Verified rich interaction events do not update `StateStore`, session
  projection, terminal rendering, or ordinary Bridge logs.
- Implemented the `InteractionEvent v1` model for messages, tools, approvals,
  user-input requests, and task terminal events.
- Added four complete wire fixtures and validation/round-trip tests for
  `InteractionEvent v1`.
- Accepted ADR 0006: controls require a complete session target, issuer,
  timestamps, expiry, and idempotency key; approvals copy pending request
  metadata and are never automatically replayed.
- Implemented `ControlCommand v1` for focus, prompt submission, interruption,
  approval, rejection, speech, and stopping speech.
- Added seven complete control wire fixtures plus validation and expiry tests.
- Implemented `ControlResult v1` with fixed accepted/rejected codes and no
  private command or handler-error content.
- Implemented `ControlRouter` validation for controller identity, expiry,
  active full-session targets, approval metadata, and idempotency conflicts.
- Added bounded idempotency and pending/decided approval records. Capacity
  refuses new work instead of evicting live deduplication state.
- Enabled negotiated `control_command_v1` controller connections with one
  correlated result per structurally valid command.
- Made `focus` an internal router action and exposed explicit non-blocking
  handler registration for Agent and Voice Gateway commands.
- Consumed approval requests after any dispatch attempt, including ambiguous
  handler failure, so approval decisions cannot be replayed.
- Added unit and end-to-end coverage for events, display, Codex hooks,
  `StateStore`, and `SessionRegistry`.
- Recorded Phase 0 and Bridge-boundary ADRs.
- Added local voice-stack research and a no-hardware software roadmap.
- Accepted ADR 0007: adapter sessions declare adapter/runtime identity,
  capabilities, full session identity, and connection-owned lifecycle.
- Implemented `adapter_session_v1` register, disconnect, and release frames plus
  correlated lifecycle acknowledgements.
- Bound modern sessions to server-assigned publisher owners so an old
  connection cannot disconnect a replacement registration.
- Required lifecycle publishers to register active owned sessions before state
  or interaction publishing, while preserving the full session record through
  state updates.
- Made complete modern sessions targetable by `ControlRouter` and verified
  focus, disconnect rejection, restore, and release behavior end to end.
- Added versioned Codex `exec --json` fixtures with explicit official-document
  versus synthetic provenance and malformed/unknown/failure boundaries.
- Accepted ADR 0008: use an in-process, fixed-capacity generic Agent Gateway
  while keeping Codex process construction and JSON parsing in its adapter.
- Implemented bounded `submit_prompt` and `interrupt` handlers with one active
  run per complete session, fixed worker capacity, and bounded provider-session
  records.
- Implemented a deterministic fake provider for prompt streaming, resume,
  capacity, cancellation, and terminal-event tests.
- Implemented the Codex subprocess provider using `codex exec --json`, bounded
  JSONL frames, supported item mapping, forward-compatible unknown-event
  handling, timeout, process-group termination, and nonzero/malformed failure
  outcomes.
- Passed prompts through stdin so private text does not appear in process
  command-line arguments, and suppressed Codex stderr from ordinary logs.
- Added socket-level coverage proving a targeted controller prompt produces an
  assistant message and task terminal event for interaction subscribers.
- Accepted ADR 0009: keep Voice provider-neutral and Bridge-independent, bound
  capture and speech work, target every operation by complete session identity,
  and defer VAD until the streaming capture boundary is selected.
- Added `voice/deskhelm_voice` with validated models, capture/ASR/TTS/playback
  provider contracts, one-at-a-time PTT, and a bounded priority speech queue.
- Preserved raw and normalized transcripts separately and added content-free,
  recoverable lifecycle and provider-failure events.
- Added deterministic fake providers and full no-hardware coverage for
  `PTT -> transcript -> Agent -> TTS -> playback`, playback interruption, queue
  capacity, and targeted speech controls.
- Added Bridge composition for targeted prompt submission, `speak`,
  `stop_speaking`, and complete assistant-message speech routing. Voice queue
  exhaustion remains isolated from interaction publishers.
- Accepted ADR 0010: compare voice providers through a versioned synthetic
  corpus and bounded provider-neutral observations with explicit versions,
  licenses, anonymous system profile, latency, accuracy, and resource fields.
- Added 12 stable Chinese, English, and mixed-language utterances covering
  commands, paths, URLs, symbols, numbers, negation, repetition, and recovery.
- Implemented dependency-free fake/production ASR and TTS benchmark runners,
  bounded UTF-8 NDJSON I/O, CER, English WER, keyword accuracy, p50/p95 latency,
  CPU time, real-time factor, and optional RSS/VRAM summaries.
- Ensured provider failures use fixed codes without exception text; generated
  audio, microphone recordings, and local results remain outside Git by default.
- Verified PipeWire 1.6.8 tools, one default source and sink, raw record/playback
  options, and stable node names without capturing audio or changing settings.
- Recorded ESP32-S3 wireless-audio research: BLE HID for keyboard controls,
  reliable BLE/Wi-Fi state, and Wi-Fi Opus as the preferred future voice path.
- Selected a simpler local POC path: explicitly configured USB microphone
  capture through PipeWire and playback through the computer's
  configured/default sink, without Opus.
- Reviewed two public Agent I/O projects and extracted streaming, adapter,
  fixture, observability, privacy, and idempotency lessons.
- Added the project constitution and durable DeskHelm-specific collaboration
  rules to `AGENTS.md`.

## Key Decisions

- DeskHelm integrates existing coding agents; it is not a new general Agent or
  LLM runtime.
- Bridge, Voice Gateway, and Physical Surface remain separate components.
- State projection, rich interaction, and control commands remain separate
  protocol planes.
- `AgentEvent v1` remains compatible while richer protocols are designed.
- Session identity is `agent_id + session_id + project_id`; `slot` is a display
  mapping.
- Vendor parsing belongs inside adapters, which must declare capabilities.
- Modern adapter lifecycle uses `adapter_session_v1`. Registration binds the
  complete session to one publisher owner; re-registration transfers ownership
  and restores activity without restoring focus.
- Declared state production requires negotiated `agent_event_v1`; declared
  interaction, tool, or approval production requires `interaction_event_v1`.
- Lifecycle-managed state publishing bypasses legacy agent-only session
  observation so control routing retains full identity.
- Adapter registrations are process-local; version 1 has no durable persistence
  and no control delivery over the publisher connection.
- The text Agent Gateway is opt-in, uses a fixed worker pool without an
  unbounded pending queue, and retains only a bounded set of provider session
  IDs and interaction sequences.
- `project_id` remains an identity rather than a path. The initial gateway uses
  one explicitly configured working directory for all sessions.
- A `dispatched` result means bounded work was accepted, not completed. Agent
  completion, cancellation, timeout, and failure use interaction terminal
  events.
- Codex prompts use stdin, JSONL records are limited to 1 MiB, stderr is not
  logged, and owned process groups are terminated on cancellation or timeout.
- The Codex provider is disabled by default and uses a read-only sandbox unless
  workspace-write is selected explicitly.
- Streams and queues require explicit bounds, ordering, cancellation,
  correlation, and slow-consumer behavior.
- Approval and rejection require precise targets and must not be blindly
  retried.
- Bridge remains dependency-minimal; voice models and GPU runtimes stay outside
  it.
- The Voice core has no Bridge imports. `VoiceBridgeIntegration` is the only
  composition layer translating transcripts, interactions, and controls.
- Voice input allows one capture/transcription flow. Speech uses a bounded
  priority queue and one playback worker; new PTT cancels current interruptible
  playback and queued playback waits until PTT returns to idle.
- Voice lifecycle events expose identifiers and fixed error codes, not audio,
  transcripts, prompts, or speech text. Raw and normalized transcripts remain
  separate in memory.
- VAD is deferred until PipeWire streaming capture is designed; the skeleton
  does not impose a speculative batch VAD interface.
- Voice benchmark v1 fixes corpus IDs and reference text, limits records to
  1 MiB, files to 64 MiB, and runs to 10,000 observations, and requires
  provider/model versions, licenses, anonymous system profile, and device
  identity.
- CER uses NFKC/case-folded text with whitespace ignored; WER is reported only
  for English-labeled utterances, while keyword accuracy preserves visibility
  into paths, symbols, versions, names, numbers, and negation.
- Numeric PipeWire object IDs are not durable. Providers must resolve configured
  defaults or stable node names and must not assume an undeclared PCM format.
- The local POC uses an explicitly configured USB microphone and the computer's
  configured/default sink. A missing microphone fails recoverably instead of
  falling back silently to another input. Opus is reserved for a constrained
  future wireless link, with an initial research profile of 16 kHz mono, 20 ms
  frames, and 24 kbps VoIP mode.
- ESP32-S3 and its wireless framing remain research directions, not frozen
  hardware or protocol decisions; implementation requires later ADRs.
- Canonical runtime identifiers are `deskhelm`, `deskhelm_bridge`, and
  `deskhelm-codex-hook`; legacy names are compatibility aliases only.
- Registration and Agent events never change focus implicitly. Only active
  sessions may be focused; disconnect, release, replacement, and expiration
  clear focus, while restore requires a new explicit focus action.
- New local clients negotiate one fixed role through
  `client_hello` / `server_hello`; only a first-frame `AgentEvent v1` receives
  legacy publisher compatibility.
- Local protocol frames are UTF-8 NDJSON limited to 1 MiB, with bounded
  per-connection queues and no automatic retry for control commands.
- The Bridge accepts at most 16 concurrent connections by default. It never
  places accepted connections into an unbounded application work queue.
- Negotiated publishers support `adapter_session_v1`, `agent_event_v1`,
  `interaction_event_v1`, or a valid combination. Negotiated subscribers select
  exactly one of `state_subscription_v1` and `interaction_subscription_v1`.
  Negotiated controllers use `control_command_v1`.
- Version 1 has no durable event history or replay. State subscribers recover
  through a fresh snapshot; interaction subscribers restart live-only delivery.
- Snapshot capture and subscriber registration are atomic. Snapshot sequence is
  zero; later sequences are monotonic within one `subscription_id`.
- Interaction subscriptions start at sequence zero and then deliver only new
  rich events. They have subscription-local wrapper sequences but no snapshot,
  retained history, replay, or state-projection side effects.
- At most half of the connection workers are subscribers by default. Queue
  overflow or a blocked write disconnects the subscriber without blocking
  publishers.
- Every control targets `agent_id + session_id + project_id`; slots never route
  controls. Voice controls also retain session ownership.
- Control idempotency is scoped by `issued_by + idempotency_key`. An allowed
  retry preserves the complete command identity and content.
- Controller `client_id` is bound to `issued_by`. Exact retained retries return
  the original result without redispatch; changed content is a conflict.
- Live idempotency entries are never evicted to admit new work. The default
  bounds are 1024 idempotency entries, five-minute minimum retention, and 1024
  combined pending/decided approval records.
- Approval and rejection echo the pending request ID, summary, and expiry. The
  command expiry equals the request expiry, and automatic replay is forbidden.
  Any dispatch attempt consumes the request because downstream failure may be
  ambiguous.

## Important Files

- `AGENTS.md`: constitution and repository rules.
- `README.md`: project overview and current quickstart.
- `docs/software/no-hardware-roadmap.md`: active milestone plan.
- `docs/architecture/multimodal-agent-console.md`: component architecture.
- `docs/research/2026-08-14-local-voice-stack.md`: ASR and TTS research.
- `docs/research/2026-08-17-agent-io-design-lessons.md`: external design review.
- `docs/decisions/0001-phase-0-python-unix-socket.md`: Phase 0 transport.
- `docs/decisions/0002-separate-bridge-state-and-session-projection.md`:
  Bridge state and session boundary.
- `docs/decisions/0003-adopt-deskhelm-name.md`: naming and compatibility plan.
- `docs/decisions/0004-session-lifecycle-and-focus.md`: session lifecycle and
  safe focus semantics.
- `docs/decisions/0005-single-socket-negotiated-local-protocol.md`: negotiated
  local transport, roles, bounds, compatibility, and reconnect behavior.
- `docs/decisions/0006-targeted-expiring-idempotent-controls.md`: targeting,
  expiry, idempotency, retry, and approval safety decisions.
- `docs/decisions/0007-adapter-session-capabilities-and-ownership.md`: adapter
  identity, capabilities, lifecycle, ownership, and restore semantics.
- `docs/decisions/0008-bounded-text-agent-gateway.md`: provider boundary,
  capacity, process safety, privacy, cancellation, and terminal semantics.
- `docs/decisions/0009-isolated-bounded-voice-gateway.md`: Voice isolation,
  provider contracts, bounds, targeting, privacy, and cancellation.
- `docs/decisions/0010-versioned-voice-benchmark-contract.md`: corpus stability,
  observation format, scoring, bounds, privacy, resources, and licensing.
- `docs/research/2026-08-18-pipewire-preflight.md`: verified local PipeWire
  capabilities and provider-design implications.
- `docs/research/2026-08-18-esp32-s3-audio-transport.md`: official ESP32-S3 and
  Opus evidence, wireless control split, parameters, risks, and local USB path.
- `protocol/adapter-session-v1.md`: lifecycle frames, acknowledgements,
  declared capabilities, and event ownership validation.
- `protocol/interaction-event-v1.md`: rich session event contract.
- `protocol/control-command-v1.md`: targeted control command contract.
- `protocol/control-result-v1.md`: correlated control outcomes and fixed result
  codes.
- `protocol/state-subscription-v1.md`: state snapshot, live update, sequencing,
  resource bounds, and reconnect contract.
- `protocol/interaction-subscription-v1.md`: live-only rich updates, bounds,
  sequencing, and gap behavior.
- `protocol/local-transport-v1.md`: implemented framing, handshake, publisher,
  compatibility, limits, and error contract.
- `bridge/deskhelm_bridge/interaction.py`: `InteractionEvent v1` model and
  validation.
- `bridge/deskhelm_bridge/control.py`: `ControlCommand v1` payload models,
  validation, serialization, and expiry checks.
- `bridge/deskhelm_bridge/control_result.py`: correlated result model and fixed
  status codes.
- `bridge/deskhelm_bridge/control_router.py`: live target checks, approval
  tracking, bounded idempotency, focus, and handler dispatch.
- `bridge/deskhelm_bridge/adapter.py`: adapter lifecycle and acknowledgement
  protocol models.
- `bridge/deskhelm_bridge/adapter_registry.py`: connection-owned registration,
  lifecycle, and event validation.
- `bridge/deskhelm_bridge/agent_gateway.py`: generic bounded prompt/interrupt
  scheduling, provider-session resume, sequence ownership, and normalization.
- `bridge/deskhelm_bridge/fake_agent_provider.py`: deterministic provider used
  by gateway tests without external services.
- `voice/deskhelm_voice/gateway.py`: PTT lifecycle, bounded speech queue,
  playback ownership, interruption, and Voice events.
- `voice/deskhelm_voice/providers.py`: provider-neutral capture, ASR, TTS, and
  playback contracts.
- `voice/deskhelm_voice/fake_providers.py`: deterministic no-hardware providers.
- `voice/deskhelm_voice/benchmark.py`: bounded runners, observation models,
  NDJSON CLI, accuracy metrics, and summaries.
- `voice/benchmarks/utterances-v1.json`: stable synthetic benchmark corpus.
- `voice/benchmarks/README.md`: measurement and artifact-handling contract.
- `bridge/deskhelm_bridge/voice_integration.py`: transcript, interaction, and
  control composition between Bridge and Voice.
- `adapters/codex/deskhelm_codex_adapter/provider.py`: Codex command, stdin,
  JSONL parsing, timeout, cancellation, and process-exit handling.
- `bridge/deskhelm_bridge/subscription.py`: subscription wire models and bounded
  per-subscriber update queue.
- `bridge/deskhelm_bridge/interaction_subscription.py`: rich subscription wire
  models, bounded queue, and in-process fan-out hub.
- `bridge/deskhelm_bridge/transport.py`: hello and protocol-error wire models.
- `bridge/deskhelm_bridge/server.py`: bounded concurrent socket handling and
  connection role dispatch.
- `bridge/deskhelm_bridge/state_store.py`: state snapshots and subscriptions.
- `bridge/deskhelm_bridge/session_registry.py`: session-to-slot projection.

## Validation

Last verified on 2026-08-18:

```bash
PYTHONPATH=bridge python3 -m unittest discover -s tests -v
```

Result: 140 tests passed, including strict `ResourceWarning` handling.

Repository checks also passed:

```bash
git diff --check
python3 -m compileall -q bridge adapters/codex voice tests
! rg -n '[[:blank:]]+$' . --glob '!.git/**'
```

## Remaining Work

1. Choose and add the repository license; the GitHub repository is currently
   public but has no root license file.
2. Define PCM format, capture byte/time bounds, process ownership, explicit USB
   microphone and default computer-sink targeting, and recovery.
3. Add PipeWire capture/playback and recovery providers, then benchmark VAD,
   Paraformer, Piper, and Kokoro outside Bridge.
4. Add a multi-project working-directory registry before one Bridge process
   manages Agent sessions from different repositories.

## Risks and Blockers

- The text Agent Gateway is disabled by default. Without
  `--agent-provider codex`, Agent commands other than focus still return
  `handler_unavailable`.
- The current text gateway handles prompt submission and interruption.
  Approval and rejection remain unavailable; speech handlers exist only when a
  Voice Gateway is explicitly composed into the Bridge.
- Production audio devices, VAD, ASR, and TTS are not implemented. The current
  Voice path is deterministic and no-hardware only.
- The benchmark runner records batch ASR final and TTS synthesis latency. First
  partial, streaming first-audio, interruption, and recovery timing await the
  streaming provider contract.
- The current audio models do not declare PCM sample format or container, so a
  real `pw-cat` provider would otherwise rely on an unsafe hidden assumption.
- The intended USB microphone was not present in the recorded PipeWire
  preflight; the current default source was built-in analog. USB discovery and
  stable-name configuration remain unverified until the device is connected.
- Adapter registrations are process-local and must be re-established after a
  Bridge restart. No durable session history or replay is promised.
- Approval tracking is bounded. When its capacity is exhausted, new approval
  requests remain visible to interaction subscribers but cannot be decided
  through the router and return `approval_not_found`.
- The default socket path changed during the pre-release rename; existing
  Bridge and hook processes must restart together after updating.
- Legacy CLI and module-execution aliases still exist and need a deprecation
  review before the first stable release.
- The public repository has no selected open-source license.
- The renamed Python distribution has not been built as a wheel on this
  machine because the system Python environment does not include `setuptools`;
  source-tree imports, both module entry points, and `pyproject.toml` metadata
  were validated directly.
- Codex compatibility currently has official-document and synthetic JSONL
  fixture evidence plus fake-subprocess coverage, but no locally captured
  authenticated model run. Claude and Gemini compatibility still lack
  versioned fixture sets.
- One configured Agent working directory currently applies to every gateway
  session. Multi-project path ownership is not yet modeled.
- ASR and TTS quality, latency, recovery, resource use, and model licensing have
  not been benchmarked on the target machine.

## Next Step

Define and accept the PipeWire PCM, capture bounds, process ownership, explicit
USB microphone and default computer-sink targeting, and recovery contract. Then
implement deterministic fake subprocess tests before recording live microphone
audio. Obtain the repository license decision when packaging or external
contributions require it.

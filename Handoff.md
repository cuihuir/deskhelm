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
resource summaries, and explicit licensing identity. The first real local audio
boundary is implemented: explicit raw PCM models plus bounded PipeWire capture
and playback providers support the current default devices or stable-name
overrides. They are not activated by the Bridge CLI, and no live microphone or
speaker was opened during implementation or validation. The streaming PCM and
VAD benchmark boundary now has its first reproducible real-audio implementation:
pinned FSDD sources, deterministic prepared samples, lazy WebRTC and Silero
ONNX adapters, and privacy-safe aggregate observations. The result validates
both paths but does not select a production VAD.

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
- Accepted ADR 0011: local audio uses complete-frame raw S16LE PCM with explicit
  rate/channels, default or stable-name PipeWire targets, and bounded provider
  lifecycle semantics.
- Added dependency-free `pw-cat` capture and playback providers with byte/time
  limits, cancellation, fixed private-content-safe errors, and owned process
  groups that escalate from SIGTERM to SIGKILL.
- Added deterministic fake-`pw-cat` coverage for default/manual targets, PCM
  alignment, bounds, startup/nonzero failures, cancellation, and forced cleanup
  without opening live audio devices.
- Accepted ADR 0012: streaming capture uses contiguous complete-frame PCM chunks
  with absolute frame positions, and each VAD run owns an independent session
  with explicit end-of-stream flushing.
- Added streaming PCM, VAD event, and speech-segment models plus provider
  protocols for owned chunk streams and session-based VAD implementations.
- Extended the benchmark with bounded VAD samples and privacy-safe NDJSON
  observations for segmentation overlap, detection delay, processing latency,
  CPU time, real-time factor, and optional memory peaks.
- Added deterministic fake VAD sessions and coverage for chunk continuity,
  event ordering, precision/recall/F1, failure isolation, NDJSON, and CLI output
  without audio devices or model dependencies.
- Accepted ADR 0013: compare WebRTC VAD 2.0.14 and Silero VAD 6.2.1 ONNX as the
  first classical and neural baselines without adding optional runtimes to
  Bridge or committing third-party audio/model files.
- Added a versioned external VAD manifest with six FSDD speakers, pinned source
  revision and SHA-256 values, CC BY-SA 4.0 identity, and seven deterministic
  speech/silence composition scenarios.
- Added bounded preparation and run tools that verify downloads, convert to
  16 kHz mono S16LE, checksum prepared WAV files, load exact reference frame
  intervals, and keep raw observations under ignored storage.
- Implemented WebRTC and Silero ONNX streaming adapters with arbitrary-chunk
  buffering, cancellation, explicit flushing, bounded endpointing state, and
  lazy optional runtimes. Silero resets recurrent state/context per stream.
- Ran 35 observations per candidate on Linux x86-64/Python 3.14.6 with no
  failures. WebRTC recorded F1 0.894 and replay p50/p95 0.19/0.31 ms; Silero
  recorded F1 0.859 and 2.98/4.57 ms. The quiet trimmed corpus is explicitly
  insufficient for final production selection.
- Recorded ESP32-S3 wireless-audio research: BLE HID for keyboard controls,
  reliable BLE/Wi-Fi state, and Wi-Fi Opus as the preferred future voice path.
- Selected a simpler local POC path: follow the computer's current PipeWire
  default capture and playback devices, with optional stable-name overrides and
  no Opus.
- Set the future product preference: manual source selection first, then a
  connected DeskHelm keyboard microphone, then the computer default source.
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
- Voice Gateway integration of VAD remains deferred. The current gateway keeps
  its batch capture path while the separate benchmark boundary uses
  frame-positioned chunks and provider-owned VAD sessions.
- Streaming chunks keep one immutable PCM format, contiguous absolute frame
  positions, and a 1 MiB per-chunk limit. VAD events are ordered, alternating
  speech boundaries no later than supplied audio; every active region must end
  during processing or `finish()`.
- VAD benchmark samples are limited to 100,000 chunks, 256 segments, and
  64 MiB PCM. Observations persist derived durations, counts, timing, and
  resources rather than PCM or provider exception text.
- Offline VAD replay measures compute time and frame-relative detection delay
  separately; it is not reported as live microphone end-to-end latency.
- Initial production candidates are WebRTC VAD and Silero VAD ONNX. Their
  optional runtimes remain lazy and external to Bridge; Silero shares the
  immutable ONNX Runtime session but resets state and context per stream.
- External VAD audio is reconstructed from a versioned manifest with pinned
  HTTPS URLs, revisions, checksums, licenses, and explicit silence recipes.
  Raw/prepared audio, models, and observation files remain ignored.
- Voice benchmark v1 fixes corpus IDs and reference text, limits records to
  1 MiB, files to 64 MiB, and runs to 10,000 observations, and requires
  provider/model versions, licenses, anonymous system profile, and device
  identity.
- CER uses NFKC/case-folded text with whitespace ignored; WER is reported only
  for English-labeled utterances, while keyword accuracy preserves visibility
  into paths, symbols, versions, names, numbers, and negation.
- Numeric PipeWire object IDs are not durable. Providers must resolve configured
  defaults or stable node names and must not assume an undeclared PCM format.
- Local capture/playback audio is complete-frame raw S16LE PCM with explicit
  sample rate and channels. Capture defaults to 16 kHz mono, 30 seconds, and
  1 MiB; playback defaults to 120 seconds and 16 MiB.
- PipeWire providers use raw `pw-cat`, own a process session, suppress stderr,
  poll stop/cancel, and terminate then kill their process group within a bounded
  grace period. They remain composition-layer options and are not CLI defaults.
- The local POC follows the computer's current PipeWire default source and sink.
  Users may override either with a stable node name; a missing explicit override
  fails recoverably instead of silently falling back. Opus is reserved for a
  constrained future wireless link, with an initial research profile of 16 kHz
  mono, 20 ms frames, and 24 kbps VoIP mode.
- After hardware integration, the DeskHelm keyboard microphone becomes the
  default input when connected, unless the user manually chose another source.
  Disconnect fallback and user notification remain an ADR decision.
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
- `docs/decisions/0011-bounded-pipewire-pcm-providers.md`: local PCM format,
  PipeWire targets, provider bounds, process ownership, and privacy contract.
- `docs/decisions/0012-streaming-pcm-vad-benchmark-boundary.md`: streaming PCM
  chunks, VAD sessions/events, benchmark metrics, bounds, and privacy contract.
- `docs/decisions/0013-select-webrtc-and-silero-vad-baselines.md`: initial VAD
  candidates, dependency isolation, FSDD provenance, and selection limits.
- `docs/research/2026-08-18-pipewire-preflight.md`: verified local PipeWire
  capabilities and provider-design implications.
- `docs/research/2026-08-18-esp32-s3-audio-transport.md`: official ESP32-S3 and
  Opus evidence, wireless control split, parameters, risks, and local USB path.
- `docs/research/2026-08-18-vad-candidates-and-first-benchmark.md`: verified
  candidate facts, first-run configuration, aggregate results, and gaps.
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
- `voice/deskhelm_voice/pipewire.py`: bounded raw-PCM `pw-cat` capture and
  playback providers.
- `voice/deskhelm_voice/streaming.py`: frame-positioned PCM chunks, speech
  boundaries, and segment models.
- `voice/deskhelm_voice/benchmark.py`: bounded runners, observation models,
  NDJSON CLI, accuracy metrics, and summaries.
- `voice/benchmarks/utterances-v1.json`: stable synthetic benchmark corpus.
- `voice/benchmarks/vad-external-v1.json`: pinned public VAD audio provenance,
  checksums, format, speakers, and deterministic scenario recipes.
- `voice/benchmarks/README.md`: measurement and artifact-handling contract.
- `voice/deskhelm_voice/vad_manifest.py`: bounded external-audio manifest model.
- `voice/deskhelm_voice/vad_samples.py`: checksum-validating prepared WAV loader
  and deterministic PCM chunk construction.
- `voice/deskhelm_voice/webrtc_vad.py`: lazy WebRTC VAD streaming adapter.
- `voice/deskhelm_voice/silero_onnx_vad.py`: lazy stateful Silero ONNX adapter.
- `tools/prepare-vad-benchmark.py`: bounded download, verification, conversion,
  composition, and local prepared-index generation.
- `tools/run-vad-benchmark.py`: isolated candidate runner and NDJSON writer.
- `bridge/deskhelm_bridge/voice_integration.py`: transcript, interaction, and
  control composition between Bridge and Voice.
- `adapters/codex/deskhelm_codex_adapter/provider.py`: Codex command, stdin,
  JSONL parsing, timeout, cancellation, and process-exit handling.
- `bridge/deskhelm_bridge/subscription.py`: subscription wire models and bounded
  per-subscriber update queue.
- `bridge/deskhelm_bridge/interaction_subscription.py`: rich subscription wire
  models, bounded queue, and in-process fan-out hub.
- `tests/test_pipewire_providers.py`: fake-subprocess PCM, targeting, bounds,
  failure, cancellation, and process-cleanup coverage.
- `tests/test_vad_benchmark.py`: streaming chunk/session validation, VAD metrics,
  failure records, NDJSON, and CLI summary coverage.
- `tests/test_vad_providers.py`: manifest, prepared checksum, WebRTC buffering,
  format, and hysteresis coverage.
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

Result: 164 tests passed, including strict `ResourceWarning` handling, 13
fake-subprocess PipeWire/PCM tests, 7 streaming VAD benchmark tests, and 4
manifest/provider tests. The full unit suite opened no live audio device.

The isolated real-candidate run also passed with 35/35 successful observations
for both WebRTC and Silero. Downloaded FSDD audio, prepared WAV files, the ONNX
model, the virtual environment, and raw NDJSON results remain ignored.

Repository checks also passed:

```bash
git diff --check
python3 -m compileall -q bridge adapters/codex voice tests tools
! rg -n '[[:blank:]]+$' . --glob '!.git/**'
! rg -n 'deskhelm_bridge|bridge\.' voice/deskhelm_voice
```

## Remaining Work

1. Choose and add the repository license; the GitHub repository is currently
   public but has no root license file.
2. Benchmark Paraformer, Piper, and Kokoro outside Bridge, then expand VAD to
   noisy/conversational labeled audio and live-device threshold measurements.
3. Add application-level audio provider/device configuration and measure live
   default/manual target plus disconnect/reconnect recovery behavior.
4. Add a multi-project working-directory registry before one Bridge process
   manages Agent sessions from different repositories.

## Risks and Blockers

- The text Agent Gateway is disabled by default. Without
  `--agent-provider codex`, Agent commands other than focus still return
  `handler_unavailable`.
- The current text gateway handles prompt submission and interruption.
  Approval and rejection remain unavailable; speech handlers exist only when a
  Voice Gateway is explicitly composed into the Bridge.
- PipeWire capture/playback providers exist, but the Bridge CLI does not select
  them yet. Production VAD selection, ASR/TTS, streaming PipeWire capture, device
  enumeration, and live recovery are not implemented.
- The benchmark runner records VAD compute/detection timing plus batch ASR final
  and TTS synthesis latency. Live capture-to-decision, first partial,
  streaming first-audio, interruption, and recovery timing remain unmeasured.
- PipeWire lifecycle behavior is validated with fake subprocesses only. Live
  source/sink access, unavailable explicit targets, hot unplug, default-device
  changes, and actual audio latency remain unverified by design.
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
- Initial WebRTC and Silero VAD replay quality/latency and licenses are measured,
  but the corpus lacks conversational speech, Chinese, noise, music, keyboard
  sounds, distant microphones, and live recovery. Production VAD is unselected.
- Production ASR and TTS quality, latency, recovery, resource use, and model
  licensing have not been benchmarked on the target machine.

## Next Step

Benchmark the first streaming ASR candidate, Paraformer, through the existing
provider-neutral corpus and observation boundary while keeping its runtime and
weights outside Bridge and Git. Then compare Piper and Kokoro notification TTS.
Keep broader VAD threshold/noise work plus PipeWire streaming activation and
live-device recovery at the application composition boundary. Obtain the
repository license decision when packaging or external contributions require it.

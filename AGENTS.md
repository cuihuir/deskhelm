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
     -> TUI and desktop clients
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
- Never place prompts in process command-line arguments. Prefer bounded stdin,
  suppress private stderr from ordinary logs, and terminate the owned process
  group on cancellation or timeout.
- Do not log prompts, source code, tool arguments, raw Agent events, audio, or
  credentials by default.
- Keep PyTorch, CUDA, model weights, and provider-specific voice dependencies
  outside the core Bridge.
- Keep `voice/` independent of Bridge. Convert transcripts and speech controls
  only at the Bridge composition boundary.
- Allow one active PTT capture/transcription flow, bound speech queues, and
  require providers to honor cancellation. Starting PTT cancels only current
  interruptible playback; queued or active speech remains session-targeted.
- Preserve raw and normalized transcripts separately. Ordinary lifecycle and
  failure events must not contain audio, transcript, prompt, or speech text.
- Keep voice benchmark corpora and observation formats versioned. Bound each
  NDJSON observation to 1 MiB, each result file to 64 MiB, and each run to
  10,000 observations. Record exact provider/model versions and licenses plus
  an anonymous system profile.
- Model streaming PCM as complete, contiguous chunks of at most 1 MiB with one
  immutable format and absolute frame positions. Open one independent VAD
  session per stream, emit ordered alternating frame-positioned speech
  boundaries, and flush it explicitly at end of stream.
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
- `PYTHONPATH=bridge python3 -m unittest tests.test_voice_gateway
  tests.test_voice_integration -v`: run the no-hardware Voice Gateway tests.
- `PYTHONPATH=bridge python3 -m unittest tests.test_pipewire_providers -v`: run
  deterministic PipeWire provider tests without opening audio devices.
- `PYTHONPATH=bridge python3 -m unittest tests.test_audio_config -v`: run
  deterministic audio discovery, selection, and diagnostic tests.
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
- `PYTHONPATH=voice python tools/prepare-asr-benchmark.py --manifest
  voice/benchmarks/asr-external-v1.json --artifact-root
  references/vendor/paraformer-bench/run-v1`: verify and prepare the pinned ASR
  set under ignored storage.
- `PYTHONPATH=voice python tools/run-asr-benchmark.py --manifest
  voice/benchmarks/asr-external-v1.json --prepared
  references/vendor/paraformer-bench/run-v1/prepared --model-directory
  <ignored-model-snapshot> --observations <results.ndjson> --summary
  <summary.json>`: run the pinned Paraformer baseline from its isolated runtime.
- `PYTHONPATH=voice python tools/prepare-tts-benchmark.py --manifest
  voice/benchmarks/tts-candidates-v1.json --artifact-root
  references/vendor/tts-bench/run-v1`: verify and prepare pinned TTS artifacts
  under ignored storage.
- `PYTHONPATH=voice python tools/run-tts-benchmark.py --candidate
  piper-chaowen-medium --manifest voice/benchmarks/tts-candidates-v1.json
  --prepared references/vendor/tts-bench/run-v1/prepared --corpus
  voice/benchmarks/utterances-v1.json --observations <results.ndjson> --summary
  <summary.json>`: run a pinned TTS candidate from its isolated runtime.
- `PYTHONPATH=bridge python3 -m unittest discover -s tests -v`: run tests.
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

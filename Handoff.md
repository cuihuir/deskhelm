# Handoff

Date: 2026-08-19

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
overrides. They remain disabled by default but can be composed explicitly by
the Bridge CLI. A unified ignored Python 3.12 runtime has now executed the
pinned Paraformer and Piper providers together, and a real four-second PTT run
completed capture, final ASR, fixed-response TTS, and playback through the
current USB microphone and computer sink without saving PCM or transcript
text. The diagnostic does not call Codex, publish partials, or measure actual
first-speaker-audio. PipeWire and Voice Gateway capture now use
the frame-positioned streaming boundary: chunks retain one format and
contiguous absolute frame positions, and the Gateway aggregates them under
fixed chunk, byte, and duration limits until PTT release. Legacy batch capture
providers remain compatible. The streaming PCM and
VAD benchmark boundary now has its first reproducible real-audio implementation:
pinned FSDD sources, deterministic prepared samples, lazy WebRTC and Silero
ONNX adapters, and privacy-safe aggregate observations. The result validates
both paths but does not select a production VAD. The first real streaming ASR
baseline is also complete: a pinned Paraformer provider and external audio set
produced 24/24 successful observations. It remains a Chinese candidate rather
than DeskHelm's sole ASR because English word boundaries and short English
commands performed poorly. The first Piper/Kokoro TTS comparison is complete
with 36/36 successful observations per candidate. Piper is now the initial
low-latency notification baseline; Kokoro remains the quality candidate, and a
final production voice awaits human listening, live playback, recovery, and
packaging license evidence. Application-level local audio selection and
diagnostics are now implemented: the CLI can resolve/list PipeWire defaults or
stable manual targets without opening audio, explicitly test an input while
discarding PCM, and explicitly play a bounded low-volume output tone. These
diagnostics do not activate model-backed voice. Negotiated controllers can now
start and release PTT for a complete session target. Each
release copies its matching press command ID, so stale or cross-session
releases cannot stop the active capture. The Bridge CLI can now explicitly
compose provisional PipeWire capture/playback, Paraformer ASR, and Piper TTS.
The path remains disabled by default, validates devices and artifact files
before startup, and loads model runtimes only on first use. Optional WebRTC VAD
is now attached as an explicit disabled-by-default advisory observer. It emits
bounded frame-positioned input activity, while PTT release remains authoritative
and final ASR always receives the complete bounded recording. VAD failures emit
one fixed non-terminal event and fall back to the unchanged PTT path.
The controlled microphone-only ASR diagnostic now pairs one versioned public
phrase with signal, accuracy, and latency metadata while suppressing provider
output and retaining neither PCM nor recognized text. Its first run produced
`voice_no_transcript`; the user later confirmed that they did not speak, so the
result is retained only as an unspoken negative control. A later user-confirmed
spoken run produced 27 characters, matched both expected keywords, and had
0.545455 CER. This verifies basic recognition and command-intent recovery, but
literal accuracy remains inadequate for code-sensitive commands. The controlled
diagnostic now also reports advisory speech segment count, active duration, and
active fraction without allowing VAD to gate ASR. An immediate-capture,
user-confirmed run improved CER to 0.181818 with full keyword accuracy and
0.579396 speech-active fraction; it is the current live baseline.
The second ASR baseline is now implemented and measured: SenseVoiceSmall through
`sherpa-onnx` uses a verified INT8 ONNX artifact, a lazy final-only adapter, and
the same privacy-safe diagnostic contract. Its public run completed 24/24
observations with 412.512 MiB peak RSS and 0.055813 mean RTF, but failed most
isolated English digit clips. A user-confirmed synchronized live run recovered
both keywords with 0.136364 CER and 1,328.308 ms cold-process final latency,
improving on the current Paraformer live phrase. Neither provider is selected
as production ASR; SenseVoice's custom FunASR model license also needs review.
The recovery phase now has an explicit retry boundary: provider failures release
their locks for a later request, cancellation is checked at each provider's
documented boundary, PipeWire sessions can be reopened after disconnect, fresh
default-device snapshots rebind, and missing manual stable names fail closed.
Live hot-unplug and hard in-flight cancellation measurements remain open.
The multi-phrase diagnostic phase is now implemented under ADR 0022. It accepts
up to eight repeated `--utterance-id` values, opens a fresh capture for each,
reuses one lazy provider for warm-path behavior, and marks each result as
requiring separate post-run confirmation. Four Chinese/mixed coding phrases
were run for the Paraformer/SenseVoice comparison. The first
SenseVoice batch completed all four captures and the user confirmed speaking
each phrase, but reported that the spoken pace did not fully keep up with the
phrase transitions; the result is therefore live diagnostic evidence rather
than a tightly synchronized provider comparison.
The repeated Paraformer batch also completed all four captures with `ok` status.
The user described the prompt cadence as faster than comfortable natural speech
and asked to begin analysis; the four records are treated as spoken with that
timing caveat. Paraformer had lower mean CER but much higher final latency, and
neither provider recovered the mixed coding keywords reliably.
ADR 0023 now adds an opt-in one-by-one `ready` stdin handshake before each
phrase capture, with a bounded timeout and fixed `voice_phrase_not_ready`
failure. The existing immediate mode remains unchanged; a live handshake-mode
rerun is pending.

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
- Accepted ADR 0014: use `funasr/paraformer-zh-streaming` as the first streaming
  ASR baseline at immutable tag `apache-2.0-20260804`, resolved commit
  `fd2af606b37d7fb8b3b8a218c5be5b07b53ef6ba`, with a verified `model.pt`
  SHA-256 and isolated FunASR/PyTorch runtime.
- Added a bounded Paraformer adapter using official 600 ms chunks, one cache per
  transcription, serialized shared-model inference, lazy imports, cancellation
  checks, and explicit input/output limits.
- Added a versioned external ASR manifest plus bounded preparation and runner
  tools. The set pins one Apache-2.0 official Chinese sample, one official
  English sample with `unverified` audio license, and six CC BY-SA 4.0 FSDD
  samples; downloaded and prepared audio remains ignored.
- Ran 24/24 successful Paraformer observations on Python 3.12.3 with FunASR
  1.3.21 and CPU-only PyTorch/torchaudio 2.11.0. Mean CER was 0.438, English WER
  0.643, keyword accuracy 0.438, mean RTF 0.121, estimated first-partial p50/p95
  376/1,848 ms, cold load 4.97 seconds, and peak RSS 3.09 GiB.
- Accepted ADR 0020 and added a lazy, serialized, final-only SenseVoiceSmall
  adapter through `sherpa-onnx` 1.13.6. The verified INT8 release artifact is
  pinned by GitHub asset ID plus archive, model, and token SHA-256 values.
- Ran 24/24 successful SenseVoice observations on the same public ASR set. Mean
  RTF was 0.055813, final latency p50/p95 was 21.726/122.086 ms, cold load was
  681.488 ms, and peak RSS was 412.512 MiB. Long Chinese/English samples were
  strong, while six isolated English digits exposed a severe short-command gap.
- Ran one synchronized user-confirmed SenseVoice microphone diagnostic on the
  same public Chinese phrase. It matched both keywords with 0.136364 CER and
  1,328.308 ms cold-process final latency without saving PCM or recognized text.
- Accepted ADR 0021 and added deterministic recovery tests for ASR failure and
  cancellation boundaries, PipeWire process disconnect/retry, and default versus
  manual device changes. No implicit recognition retry or device fallback is
  introduced.
- Accepted ADR 0022 and added a bounded multi-phrase microphone diagnostic for
  repeated Chinese/mixed coding commands. Batch output contains only per-phrase
  privacy-safe metrics and fixed statuses.
- Ran the first four-phrase SenseVoice batch with fresh captures and warm
  provider reuse. All four records completed with fixed `ok` status and the
  user confirmed speaking each phrase; pace/synchronization was imperfect, so
  literal accuracy and keyword misses remain qualified evidence.
- Ran the repeated four-phrase Paraformer batch with fresh captures and warm
  provider reuse. All four records completed with fixed `ok` status; the user
  reported the cadence was faster than comfortable natural speech, so the
  records are qualified live evidence rather than paired comparison data.
- Compared the two qualified batches: Paraformer mean CER was `0.726123` versus
  SenseVoice `0.809943`; mean final latency was `2,158.430 ms` versus
  `442.620 ms`. Both providers had `0.166667` mean keyword accuracy and zero
  keyword accuracy on the three mixed coding phrases, so neither is selected.
- Accepted ADR 0023 and added an opt-in one-by-one phrase readiness handshake.
  Each bounded stdin wait requires an exact `ready` line, reports its mode in
  the privacy-safe output, and fails with `voice_phrase_not_ready` on timeout
  or EOF without opening capture.
- Accepted ADR 0015: use Piper Chaowen as the initial low-latency notification
  TTS baseline while retaining Kokoro 82M as the quality candidate and
  deferring the final production selection.
- Added pinned Piper/Kokoro candidate and artifact manifests, bounded download
  and preparation tooling, lazy serialized providers, first-provider-chunk and
  RTF benchmark metrics, ignored WAV export, and interruption probes.
- Ran 36/36 successful observations per TTS candidate on Python 3.12.3 and four
  CPU threads. Piper recorded 0.034 mean RTF, 174/592 ms first-chunk p50/p95,
  and 1.01 GiB peak RSS; Kokoro recorded 0.185 RTF, 1,090/3,441 ms, and 2.52
  GiB. Piper cancelled between chunks; Kokoro completed before cancellation.
- Transcribed 24 generated WAV files through the pinned Paraformer model as an
  explicitly model-dependent intelligibility proxy. Both candidates were strong
  on Chinese-only keywords and weak on mixed coding commands; no human quality
  or MOS claim was made.
- Accepted ADR 0016: keep local audio selection explicit and process-local,
  prefer manual stable names over current defaults, fail without fallback when
  a manual target is absent, and separate read-only discovery from active tests.
- Added bounded `pw-dump`/`wpctl` discovery, validated provider/device
  composition, `deskhelm audio status`, explicit signal-only input diagnostics,
  and explicit short low-volume output diagnostics.
- Verified the current default USB mono microphone and internal stereo sink.
  Two-second default and manual-source input tests succeeded without saving
  PCM, and a 250 ms default-sink tone succeeded. A one-second input test
  produced no audio during device startup and failed explicitly.
- Accepted ADR 0017: expose targeted `press_ptt` and correlated `release_ptt`
  controls while keeping cancellation internal.
- Added control fixtures and Bridge composition handlers for external PTT. A
  release must match both the complete active target and the press command ID;
  idle, stale, and cross-session releases fail without changing capture state.
- Accepted ADR 0018: add an explicit provisional local voice composition while
  keeping all model-backed voice disabled by default and VAD deferred until a
  real streaming capture migration.
- Added `LocalVoiceConfig` preflight and `deskhelm bridge --voice-provider
  local`. Startup resolves PipeWire devices, verifies required Paraformer/Piper
  files, composes bounded providers, and leaves heavyweight imports and model
  loading lazy.
- Verified the local composition can start and stop against the current
  PipeWire graph and prepared artifact paths without opening audio or importing
  model runtimes.
- Added one pinned optional Python 3.12 CPU runtime specification for combined
  Paraformer/Piper execution. Piper Chinese synthesis additionally required
  `g2pw==0.1.1` and `sentence-stream==1.3.0`.
- Made the PipeWire `pw-cat` command prefix configurable. Distrobox execution
  uses the compatible host PipeWire 1.6.8 client through
  `host-spawn -no-pty pw-cat`; the container's 1.0.5 client lacks `--raw`.
- Reverified Paraformer at 8/8 and Piper at 12/12 in the unified runtime.
- Added an explicit privacy-safe live diagnostic and completed a real
  four-second `PTT -> final ASR -> fixed TTS -> playback` run. It emitted the
  complete lifecycle through `speech_completed`, retained only a one-character
  transcript count, and did not save or print audio or transcript text.
- Migrated PipeWire capture to an owned `PcmChunkStream` that emits complete
  frames with contiguous absolute positions while retaining `capture()` as a
  compatibility wrapper.
- Migrated Voice Gateway to prefer streaming capture, reject gaps and format
  changes, and enforce 10,000-chunk, byte, and duration bounds before final ASR.
- Added deterministic streaming capture fakes and coverage for release gating,
  stream cleanup, split-frame buffering, discontinuity, cancellation, byte
  limits, and legacy batch compatibility.
- Distinguished an explicit empty ASR result as `voice_no_transcript` without
  exposing provider errors or private content.
- Rechecked the live stream: a four-second privacy-safe signal diagnostic
  captured 128,286 bytes with peak 0.399 and RMS 0.0617. Two full attempts
  reached transcription but produced no final text; the second included a
  user-confirmed spoken sentence. No PCM or transcript was retained.
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
- Voice Gateway integration of VAD remains deferred. The Gateway now consumes
  frame-positioned capture chunks but aggregates them until PTT release; live
  provider-owned VAD sessions and partial ASR are not attached.
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
- Paraformer is the initial Chinese streaming ASR candidate, not the sole
  production ASR. Its optional FunASR/PyTorch runtime and weights stay outside
  Bridge and Git; the provider uses one cache per transcription and serializes
  access to the shared model.
- Paraformer first-partial results are offline pacing estimates: required audio
  availability plus processing time for the first non-empty chunk. They are not
  live microphone capture-to-UI measurements.
- Piper is the initial notification TTS integration baseline because it was
  materially faster and smaller in the first run. Kokoro remains a comparison
  candidate; neither is the final production voice.
- TTS first-audio timing means the first complete provider chunk, not PCM-frame
  streaming. Piper and Kokoro cancel only between inference/chunk boundaries.
- Piper's GPL-3.0-or-later runtime requires packaging review before bundling or
  distribution, especially while the repository has no selected root license.
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
  grace period. They remain composition-layer options and are not Bridge
  service defaults.
- The local POC follows the computer's current PipeWire default source and sink.
  Users may override either with a stable node name; a missing explicit override
  fails recoverably instead of silently falling back. Opus is reserved for a
  constrained future wireless link, with an initial research profile of 16 kHz
  mono, 20 ms frames, and 24 kbps VoIP mode.
- Local audio selection is process-local. Read-only status never opens audio;
  input/output tests require explicit commands. Diagnostic input is reduced to
  duration/peak/RMS metadata and discarded.
- The audio diagnostic CLI does not select VAD, ASR, or TTS and does not enable
  the Bridge Voice Gateway. External PTT and model composition remain separate.
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
- External PTT is not a toggle. The press command ID identifies one activation,
  and release must copy that ID in addition to matching the complete session.
  Exact retries use router deduplication; clients do not automatically replay
  PTT controls after an ambiguous result.
- Model-backed local voice is an explicit experimental composition, not a
  production default. Startup validates the selected devices and required
  external files but does not load FunASR, PyTorch, Piper, or ONNX Runtime.
- The current local path uses PTT release as its capture endpoint. Streaming
  capture alone is not VAD or partial ASR; do not claim live endpoint timing
  until a provider-owned VAD session consumes chunks before final aggregation.
- An explicit empty provider recognition maps to `voice_no_transcript`; other
  capture, format, runtime, and model failures remain `voice_input_failed`.
- The live diagnostic substitutes a fixed public TTS response for Codex. Its
  `SPEECH_STARTED` event occurs before synthesis, so it must not be reported as
  actual first-speaker-audio latency.
- Native audio execution defaults to `pw-cat`. A composition may provide a
  validated command prefix; the verified Distrobox prefix is
  `host-spawn -no-pty pw-cat`.
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
- `docs/decisions/0014-use-paraformer-as-initial-streaming-asr-baseline.md`:
  pinned model/runtime identity, streaming configuration, bounds, and limits.
- `docs/decisions/0020-evaluate-sensevoice-as-second-asr-baseline.md`: selected
  comparison, immutable artifact identity, final-only semantics, and license
  risk.
- `docs/decisions/0021-bounded-voice-recovery-and-device-rebind.md`: retry,
  cancellation, process recovery, and strict device rebinding semantics.
- `docs/decisions/0022-bounded-multi-phrase-asr-diagnostic.md`: bounded batch
  capture, per-phrase confirmation, and privacy-safe comparison output.
- `docs/decisions/0023-one-by-one-asr-readiness-handshake.md`: bounded per-phrase
  stdin readiness and explicit capture-start semantics for chat-driven runs.
- `docs/decisions/0015-use-piper-as-initial-notification-tts-baseline.md`:
  initial TTS baseline, streaming semantics, licensing, and selection limits.
- `docs/decisions/0016-explicit-local-audio-selection-and-diagnostics.md`:
  provider/device selection, privacy-safe diagnostics, and deferred composition.
- `docs/decisions/0017-correlate-external-ptt-press-and-release.md`:
  external PTT targeting, activation correlation, retry, and failure semantics.
- `docs/decisions/0018-opt-in-provisional-local-voice-composition.md`:
  explicit composition, preflight, lazy loading, provider status, and VAD
  deferral.
- `docs/research/2026-08-18-pipewire-preflight.md`: verified local PipeWire
  capabilities and provider-design implications.
- `docs/research/2026-08-18-esp32-s3-audio-transport.md`: official ESP32-S3 and
  Opus evidence, wireless control split, parameters, risks, and local USB path.
- `docs/research/2026-08-18-vad-candidates-and-first-benchmark.md`: verified
  candidate facts, first-run configuration, aggregate results, and gaps.
- `docs/research/2026-08-18-paraformer-first-benchmark.md`: pinned identity,
  external-set provenance, measurements, per-sample results, and next evidence.
- `docs/research/2026-08-18-piper-kokoro-first-benchmark.md`: pinned TTS
  identities, performance, interruption, proxy intelligibility, and gaps.
- `docs/research/2026-08-19-local-audio-diagnostics.md`: current PipeWire
  inventory, live default/manual tests, signal metadata, and startup gap.
- `docs/research/2026-08-19-local-voice-runtime-and-live-path.md`: combined
  runtime identity, PipeWire host boundary, live timings, and limitations.
- `docs/research/2026-08-19-controlled-live-asr-diagnostic.md`: controlled
  public-phrase contract, first real signal/ASR metrics, interpretation, and
  remaining evidence.
- `docs/research/2026-08-19-sensevoice-second-asr-baseline.md`: candidate
  comparison, pinned artifacts, public/live results, and licensing boundary.
- `docs/research/2026-08-19-provider-recovery-and-device-change.md`: recovery
  matrix, fake-boundary evidence, and outstanding live hot-plug measurements.
- `docs/research/2026-08-19-multi-phrase-asr-diagnostic.md`: phrase set,
  privacy bounds, live provider comparison, and handshake follow-up.
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
- `voice/deskhelm_voice/gateway.py`: PTT lifecycle, bounded streaming capture
  aggregation, speech queue, playback ownership, interruption, and Voice events.
- `voice/deskhelm_voice/providers.py`: provider-neutral capture, ASR, TTS, and
  playback contracts.
- `voice/deskhelm_voice/fake_providers.py`: deterministic batch/streaming
  capture, ASR, TTS, playback, and VAD providers.
- `voice/deskhelm_voice/pipewire.py`: owned frame-positioned raw-PCM `pw-cat`
  capture stream, batch compatibility wrapper, and bounded playback provider.
- `voice/deskhelm_voice/audio_config.py`: bounded discovery, provider/device
  selection, signal-only input reports, and generated output tones.
- `voice/deskhelm_voice/local_gateway.py`: provisional provider selection,
  artifact preflight, bounds, and lazy Voice Gateway construction.
- `voice/runtime/requirements-local-voice-py312.txt`: pinned optional combined
  Paraformer/SenseVoice/Piper CPU runtime.
- `voice/deskhelm_voice/streaming.py`: frame-positioned PCM chunks, speech
  boundaries, and segment models.
- `voice/deskhelm_voice/benchmark.py`: bounded runners, observation models,
  NDJSON CLI, accuracy metrics, and summaries.
- `voice/benchmarks/utterances-v1.json`: stable synthetic benchmark corpus.
- `voice/benchmarks/vad-external-v1.json`: pinned public VAD audio provenance,
  checksums, format, speakers, and deterministic scenario recipes.
- `voice/benchmarks/asr-external-v1.json`: pinned public ASR audio references,
  checksums, licenses, speakers, and expected transcripts.
- `voice/benchmarks/tts-candidates-v1.json`: pinned TTS runtime/model identity,
  artifacts, sizes, checksums, and licenses.
- `voice/benchmarks/README.md`: measurement and artifact-handling contract.
- `voice/deskhelm_voice/vad_manifest.py`: bounded external-audio manifest model.
- `voice/deskhelm_voice/vad_samples.py`: checksum-validating prepared WAV loader
  and deterministic PCM chunk construction.
- `voice/deskhelm_voice/webrtc_vad.py`: lazy WebRTC VAD streaming adapter.
- `voice/deskhelm_voice/silero_onnx_vad.py`: lazy stateful Silero ONNX adapter.
- `voice/deskhelm_voice/paraformer.py`: lazy bounded streaming Paraformer ASR
  adapter and offline first-partial measurement.
- `voice/deskhelm_voice/sensevoice.py`: lazy bounded final-only SenseVoice ASR
  adapter with serialized inference and explicit cancellation boundaries.
- `voice/deskhelm_voice/asr_manifest.py`: bounded external ASR manifest and
  prepared-set checksum/duration validation.
- `voice/deskhelm_voice/piper_tts.py`: lazy bounded Piper provider with pinned
  local Chinese G2P resources.
- `voice/deskhelm_voice/kokoro_tts.py`: lazy bounded Kokoro Chinese/English
  provider.
- `voice/deskhelm_voice/tts_manifest.py`: bounded TTS candidate and artifact
  manifest validation.
- `tools/prepare-vad-benchmark.py`: bounded download, verification, conversion,
  composition, and local prepared-index generation.
- `tools/run-vad-benchmark.py`: isolated candidate runner and NDJSON writer.
- `tools/prepare-asr-benchmark.py`: bounded ASR download, checksum verification,
  conversion, and prepared-index generation.
- `tools/run-asr-benchmark.py`: explicit Paraformer/SenseVoice verification and
  isolated runs, observations, resource metadata, and summary output.
- `tools/prepare-tts-benchmark.py`: bounded downloads, verification, and safe
  G2PW extraction.
- `tools/run-tts-benchmark.py`: isolated candidate runner, WAV export,
  interruption probe, observations, and summary output.
- `tools/run-local-voice-live.py`: explicit privacy-safe live capture, ASR,
  fixed-response TTS, playback, and timing diagnostic.
- `tools/run-local-asr-diagnostic.py`: controlled public-phrase microphone and
  selectable ASR diagnostic with privacy-safe signal, accuracy, and latency.
- `bridge/deskhelm_bridge/voice_integration.py`: transcript, interaction, and
  control composition between Bridge and Voice.
- `adapters/codex/deskhelm_codex_adapter/provider.py`: Codex command, stdin,
  JSONL parsing, timeout, cancellation, and process-exit handling.
- `bridge/deskhelm_bridge/subscription.py`: subscription wire models and bounded
  per-subscriber update queue.
- `bridge/deskhelm_bridge/interaction_subscription.py`: rich subscription wire
  models, bounded queue, and in-process fan-out hub.
- `tests/test_pipewire_providers.py`: fake-subprocess PCM, targeting, bounds,
  failure, cancellation, process cleanup, and disconnect/retry coverage.
- `tests/test_vad_benchmark.py`: streaming chunk/session validation, VAD metrics,
  failure records, NDJSON, and CLI summary coverage.
- `tests/test_vad_providers.py`: manifest, prepared checksum, WebRTC buffering,
  format, and hysteresis coverage.
- `tests/test_asr_providers.py`: ASR manifest/prepared-set, measured streaming
  result, lazy loading, input bounds, cancellation, and failure-reuse coverage.
- `tests/test_tts_providers.py`: TTS manifest, benchmark metrics, provider
  routing, lazy loading, bounds, and cancellation coverage.
- `tests/test_audio_config.py`: synthetic discovery, default/manual selection,
  device rebinding, provider composition, diagnostics, and CLI argument coverage.
- `tests/test_voice_integration.py`: targeted PTT/speech control composition,
  transcript-to-prompt routing, and speech failure isolation.
- `tests/test_local_voice_config.py`: disabled-by-default CLI behavior, exact
  preflight failures, lazy provider composition, and gateway ownership cleanup.
- `tests/test_local_voice_live_tool.py`: explicit live-audio bounds and
  transcript/audio-free summary coverage.
- `tests/test_local_asr_diagnostic_tool.py`: controlled phrase selection,
  signal hints, privacy suppression, fixed failures, and metric coverage.
- `bridge/deskhelm_bridge/transport.py`: hello and protocol-error wire models.
- `bridge/deskhelm_bridge/server.py`: bounded concurrent socket handling and
  connection role dispatch.
- `bridge/deskhelm_bridge/state_store.py`: state snapshots and subscriptions.
- `bridge/deskhelm_bridge/session_registry.py`: session-to-slot projection.

## Validation

Last verified on 2026-08-19:

```bash
PYTHONPATH=bridge python3 -m unittest discover -s tests -v
```

Result: 219 tests passed under the workstation resource limiter with strict
`ResourceWarning` handling. This includes the existing Bridge, protocol,
adapter, voice, benchmark, and fake-subprocess PipeWire coverage plus streaming
capture continuity, bounds, premature-end, cleanup, compatibility, empty-ASR
classification, advisory VAD ordering/fallback/cancellation, explicit local VAD
composition, privacy-safe live-summary tests, and the bounded multi-phrase
diagnostic path. The full unit suite opened no live audio device and did not
import optional model runtimes. The focused local ASR diagnostic module has 11
tests covering corpus selection, signal/VAD metrics, provider-output
suppression, fixed failures, lazy final-only behavior, recovery boundaries,
batch phrase selection/privacy output, and the bounded exact-`ready` handshake.

The first live SenseVoice multi-phrase batch completed four fresh captures with
`ok` status and the user confirmed all four phrases were spoken. The user also
reported that the speaking pace did not fully keep up with phrase transitions;
the measurements are recorded as qualified diagnostic evidence, not a tightly
synchronized provider comparison.

The repeated live Paraformer batch also completed four fresh captures with `ok`
status. The user reported that the prompt cadence was faster than comfortable
natural speech and asked to begin analysis; all four are treated as spoken with
that caveat. Across the two qualified batches, Paraformer mean CER was `0.726123`
versus SenseVoice `0.809943`, while mean final latency was `2,158.430 ms` versus
`442.620 ms`. Both providers had `0.166667` mean keyword accuracy and zero
keyword accuracy on the three mixed coding phrases.

The new handshake mode has deterministic coverage only; no live microphone run
has used the per-phrase `ready` gate yet.

An additional startup smoke test used the current PipeWire graph and ignored
prepared Paraformer/Piper artifact paths. The opt-in Bridge composed the local
gateway, accepted one event, exited cleanly, and removed its socket without
opening audio or loading model runtimes.

The current Voice Gateway focused suite passed 18/18 tests covering streaming
and legacy capture, advisory activity ordering, output bounds, fallback,
cancellation, and event metadata validation.

The isolated real-candidate run also passed with 35/35 successful observations
for both WebRTC and Silero. Downloaded FSDD audio, prepared WAV files, the ONNX
model, the virtual environment, and raw NDJSON results remain ignored.

The isolated Paraformer run passed with 24/24 successful observations across
eight samples and three repetitions. It verified the pinned model checksum,
recorded 4.97-second cold load, 3.09 GiB process peak RSS, 0.121 mean RTF, and
the accuracy/latency results in the dated research report. The Python 3.12
environment, weights, downloaded/prepared audio, raw NDJSON, and local summary
remain ignored.

The isolated SenseVoice run passed with 24/24 successful observations across
the same eight samples and three repetitions. It verified the GitHub archive
digest plus extracted model/token checksums, recorded 681.488 ms cold load,
412.512 MiB peak RSS, 0.055813 mean RTF, and the accuracy/latency results in the
dated report. The wheel, model, raw observations, and local summary remain
ignored.

After the Paraformer cancellation-boundary change, a one-repetition public
smoke run passed 8/8. CER and keyword accuracy remained 0.4375/0.4375, final
latency p50 was 44.473 ms, estimated first-partial p50 was 373.653 ms, and peak
RSS was 3,088.203 MiB. The run used the constrained `ubuntu24-r23` container;
its raw observations and summary remain ignored.

The isolated TTS run passed with 36/36 successful observations per candidate,
and a post-change offline smoke run passed with 12/12 per candidate. The dated
research report records exact performance, memory, interruption, licensing,
signal, and ASR-proxy results. Environments, weights, generated WAV files, raw
NDJSON, proxy output, and local summaries remain ignored.

The combined Python 3.12 runtime passed 8/8 Paraformer samples and 12/12 Piper
utterances. The live four-second run resolved the default USB mono microphone
and internal stereo sink, then emitted `ptt_started`, `transcribing`,
`transcript_ready`, `speech_started`, and `speech_completed`. Release-to-final
was 7,186.167 ms and total time was 15,294.695 ms. Only the transcript character
count was reported; PCM and transcript text were neither saved nor printed.
The earlier signal diagnostic peak of 1.0 and RMS near 0.394 indicate probable
input clipping/high gain.

After the streaming migration, a four-second signal-only run captured 128,286
bytes over 4,008.938 ms with peak 0.399292 and RMS 0.061689. Two full-chain
attempts reached `transcribing` but ended without a final transcript or
playback; the user confirmed speaking during the second. PCM and transcript
text were not saved. The healthy signal makes capture failure less likely, but
the exact Paraformer failure still requires a live rerun after the new
`voice_no_transcript` classification.

The subsequent advisory WebRTC VAD run used the pinned
`webrtcvad-wheels==2.0.14` runtime. It emitted `input_speech_started` at frame 0
and `input_speech_ended` at frame 1,280 before `transcribing`, with no VAD
failure. PTT release still ended capture and final ASR returned the now-confirmed
`voice_no_transcript` classification. PCM and transcript text were neither
saved nor printed. The 80 ms activity region validates live event plumbing and
fallback only; it does not prove detection of the intended utterance or
acceptable VAD/ASR quality.

The unspoken `zh-repeat-01` microphone-only run captured 223,178 bytes over
6,974.312 ms at 16 kHz mono S16LE. Peak was 0.506927, RMS was 0.035902, clipped
sample fraction was zero, and the provisional level hint was
`within_diagnostic_range`. Paraformer returned `voice_no_transcript` after
11,329.329 ms. PCM and recognized text were neither saved nor printed. The user
later confirmed they did not speak, so these values describe a background
negative control rather than voice input or recognition quality.

The third controlled attempt was user-confirmed as spoken. It captured
256,628 bytes over 8,019.625 ms with peak 0.914215, RMS 0.120620, and no clipped
samples. Paraformer returned 27 characters, matched both expected keywords,
reported 0.545455 CER, first partial at 1,850.498 ms, and final output at
5,785.955 ms. PCM and recognized text were neither saved nor printed. The user
did not hear the attempted speaker cue, so future interpretation requires
explicit post-run speech confirmation rather than relying on an audible cue.

The advisory-activity phase found that an unspoken eight-second negative control
still produced five VAD segments and 739.625 ms of false activity. A separate
user-confirmed attempt with a ten-second unseen lead-in captured only 180 ms of
activity and no transcript, indicating a likely synchronization miss rather
than model failure. The workflow now defaults to immediate capture after an
explicit readiness handshake.

The final confirmed immediate-capture run recorded 384,970 bytes over
12,030.312 ms. WebRTC reported seven segments totaling 6,970.312 ms
(`0.579396` active fraction). Paraformer returned 18 characters with both
keywords, 0.181818 CER, first partial at 4,850.187 ms, and final output at
8,008.233 ms. Peak was 0.779144, RMS was 0.091119, and no samples clipped.

The synchronized SenseVoice comparison was also user-confirmed as spoken. It
captured 384,970 bytes over 12,030.312 ms, returned 19 characters, matched both
keywords, and recorded 0.136364 CER with 1,328.308 ms cold-process final ASR.
WebRTC reported two advisory segments totaling 4,700 ms. Peak was 0.696960, RMS
was 0.086324, and no samples clipped. PCM and recognized text were not saved or
printed.

Live local audio diagnostics resolved three sources and three sinks. Two-second
default and manual USB-source tests each captured about 1.98 seconds of 16 kHz
mono S16LE PCM and discarded it after signal measurement. The default internal
sink played a 250 ms, 660 Hz, 5% level tone. A one-second input test returned no
audio and failed explicitly; hot unplug and default changes remain untested.

Repository checks also passed:

```bash
git diff --check
python3 -m compileall -q bridge adapters/codex voice tests tools
! rg -n '[[:blank:]]+$' . --glob '!.git/**'
! rg -n 'deskhelm_bridge|bridge\.' voice/deskhelm_voice
git check-ignore -v references/vendor/paraformer-bench/py312/bin/python \
  references/vendor/tts-bench/py312/bin/python \
  voice/benchmarks/results/paraformer-v1.ndjson \
  voice/benchmarks/results/piper-chaowen-v1.ndjson \
  voice/benchmarks/results/kokoro-v1.ndjson
```

## Remaining Work

1. Choose and add the repository license; the GitHub repository is currently
   public but has no root license file.
2. Run blinded Piper/Kokoro listening, actual speaker-first-audio measurement,
   and live PipeWire interruption tests. Repeat controlled Chinese/mixed
   commands and compare an alternative ASR before selecting production defaults.
3. Expand VAD to noisy/conversational labeled audio and live-device threshold
   measurements.
4. Repeat controlled live VAD utterances and test threshold, disconnect,
   runtime-failure, and reconnect behavior without enabling auto-endpointing.
5. Add a multi-project working-directory registry before one Bridge process
   manages Agent sessions from different repositories.

## Risks and Blockers

- The text Agent Gateway is disabled by default. Without
  `--agent-provider codex`, Agent commands other than focus still return
  `handler_unavailable`.
- The current text gateway handles prompt submission and interruption.
  Approval and rejection remain unavailable; speech handlers exist only when a
  Voice Gateway is explicitly composed into the Bridge.
- PipeWire discovery, diagnostics, external PTT, streaming capture, an opt-in
  Paraformer/Piper composition, combined-runtime execution, advisory WebRTC
  VAD, and one earlier successful full path exist. Automatic VAD endpointing,
  partial transcript publication, real Codex use in the live path, and live
  recovery are not implemented or tested.
- The benchmark runner records VAD compute/detection timing, batch ASR final
  latency, Paraformer offline first-partial estimates, and provider-chunk TTS
  first audio/interruption. Live capture-to-decision/partial,
  playback-to-speaker first audio, mid-inference interruption, and recovery
  timing remain unmeasured.
- Basic live default/manual input, default output, streaming PCM capture, and one
  earlier ASR/TTS playback path are verified. The best synchronized spoken run
  recovered all expected keywords with 0.181818 CER, which is improved but still
  unsuitable for exact code-sensitive commands. Repeated command coverage,
  alternative-ASR comparison, startup calibration, hot unplug, default-device
  changes, and actual speaker-first-audio latency need explicit product behavior.
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
- Paraformer is measured on a tiny public set, one synchronized live Chinese
  command, and one qualified four-phrase live batch. The batch had lower mean
  CER than SenseVoice but zero keyword accuracy on the three mixed coding
  phrases; English spacing, isolated digits, about 3.09 GiB peak RSS, and
  cancellation only between inference calls remain product risks. It is not
  selected as the sole production ASR.
- SenseVoice is final-only and cannot provide partials or cancel its native
  decode midway. It missed most isolated English digit clips, and its custom
  FunASR Model License 1.1 requires review before packaging or production use.
- Piper and Kokoro performance, resources, provider-chunk first audio,
  interruption boundaries, and licenses are measured, but human quality and
  actual playback latency are not. Piper produced a very small full-scale
  sample fraction and its GPL runtime needs packaging review.

## Next Step

The multi-phrase ASR comparison is complete as qualified live evidence. Next,
run selected phrases with the new one-by-one readiness handshake, then measure
timeout/disconnect/default-device change and provider recovery, and retain
whisper.cpp as the next licensing/quality fallback before selecting a production
ASR. Expand live VAD threshold/noise evidence without granting endpoint control,
and separately define actual speaker-first-audio and interruption
instrumentation. Keep every provider replaceable and obtain the repository
license decision before packaging models or accepting external contributions.

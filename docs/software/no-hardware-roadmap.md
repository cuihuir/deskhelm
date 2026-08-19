# No-Hardware Software Roadmap

Date: 2026-08-18

Status: Active

## Goal

Deliver a useful local Agent Console before physical devices exist. The software
must support agent state, session targeting, voice input and interruptible voice
output through local interfaces while keeping the core Bridge dependency-free.

## Milestone 1: Bridge Core Boundaries

Status: Complete

- [x] Move slot state out of `SlotPanel` into `StateStore`.
- [x] Add in-process state subscriptions.
- [x] Add `SessionRegistry` with dynamic and legacy slot mapping.
- [x] Define session lifecycle: register, focus, disconnect, expire, restore.
- [x] Replace the sequential connection loop with a bounded concurrency model.
- [x] Expose a read-only local snapshot and subscription API.
- [x] Define adapter capabilities and captured-fixture version metadata.

Acceptance criteria:

- Existing `AgentEvent v1`, CLI, simulator, and Codex hook behavior remains
  compatible.
- Multiple local consumers can observe the same ordered state updates.
- A session is targeted by identity rather than by display slot alone.

## Milestone 2: Interaction and Control Protocols

Status: Complete

- [x] Write an ADR for the negotiated local transport and message envelopes.
- [x] Define versioned `InteractionEvent v1`.
- [x] Define versioned `ControlCommand v1`.
- [x] Add JSON fixtures and compatibility tests for both versioned models.
- [x] Define interaction ordering, correlation, and terminal-event semantics.
- [x] Define queue bounds, maximum record size, and slow-subscriber direction.
- [x] Add bounded live interaction fan-out and negotiated publishing.
- [x] Implement targeting and validation in a `ControlRouter`.
- [x] Add bounded idempotency and approval retention plus command results.
- [x] Enable the negotiated `controller` role.
- [x] Require `request_id`, target session, summary, and expiry for approvals.
- [x] Require idempotency keys for retryable prompt and control operations.

Acceptance criteria:

- Rich text never leaks into the hardware state projection.
- Invalid versions, targets, commands, and expired approvals are rejected.
- Interrupt and stop-speaking commands can be routed without hardware.

## Milestone 3: Text-Only Agent Gateway

Status: Complete

- [x] Wrap `codex exec --json` behind an agent-provider interface.
- [x] Stream Codex JSONL into normalized interaction events.
- [x] Support cancellation, process exit, timeout, and malformed output.
- [x] Add a fake provider for deterministic tests.

Acceptance criteria:

- A text prompt can target a session and stream a final response locally.
- Cancellation stops the owned process and produces a terminal event.
- Tests do not require network access or a logged-in Agent CLI.

## Milestone 4: Voice Gateway Skeleton

Status: Complete

- [x] Create an isolated `voice/` package with no import dependency from Bridge.
- [x] Define audio capture, ASR, TTS, and playback provider interfaces.
- [x] Implement PTT state handling and an interruptible speech queue.
- [x] Preserve raw and normalized transcripts separately.
- [x] Use fake providers to test the full pipeline before installing models.
- [x] Connect targeted prompt, speech, and stop-speech handling at the Bridge
  composition boundary.

Acceptance criteria:

- Fake audio can complete `PTT -> transcript -> agent -> speech queue`.
- Starting a new PTT cancels current interruptible playback.
- Audio device loss and provider failures produce recoverable states.

## Milestone 5: Local Audio Providers and Benchmarking

Status: In Progress

- [x] Add PipeWire capture from the default or manually selected input source.
- [x] Add PipeWire playback through the computer's configured/default sink.
- [x] Add application-level provider selection, stable-name validation, and
  explicit input/output diagnostics.
- [x] Add targeted external PTT press/release controls with per-activation
  correlation.
- [x] Compose provisional PipeWire, Paraformer, and Piper providers behind an
  explicit disabled-by-default Bridge option.
- [x] Define the streaming PCM and provider-neutral VAD benchmark boundary.
- [x] Migrate PipeWire and Voice Gateway capture to bounded frame-positioned
  chunks while preserving legacy batch provider compatibility.
- [x] Benchmark initial WebRTC and Silero ONNX VAD candidates on a pinned public
  audio set.
- [x] Integrate optional advisory WebRTC VAD into live streaming capture while
  keeping PTT release authoritative and final ASR independent of VAD outcome.
- [ ] Expand VAD measurements to conversational/noisy audio and live devices,
  then select the production default.
- [x] Benchmark Paraformer for streaming ASR.
- [x] Benchmark Piper and Kokoro for notification TTS.
- [x] Build one isolated Paraformer/Piper runtime and verify a real bounded
  `PTT -> final ASR -> fixed TTS -> PipeWire playback` diagnostic.
- [x] Add fixed Chinese, English, and mixed-language test utterances.
- [x] Define versioned latency, accuracy, resource, and licensing observations.
- [x] Record initial real provider and live-device measurements.
- [x] Add a controlled public-phrase ASR diagnostic that correlates privacy-safe
  input signal, accuracy, and latency metadata without playback.
- [x] Add advisory speech-active segment, duration, and ratio measurements to
  that diagnostic without allowing VAD to gate ASR.
- [ ] Record disconnect, timeout, device-change, and provider-failure recovery.

Acceptance criteria:

- The no-hardware POC completes `PTT -> ASR -> Codex -> TTS` locally.
- First partial, final transcript, TTS first audio, and interruption latency are
  measured reproducibly.
- Model weights and heavyweight runtime dependencies remain outside Bridge and
  outside version control.

## Immediate Backlog

1. Run blinded TTS listening and measure actual speaker-first-audio plus live
   PipeWire interruption before selecting a production voice.
2. Expand VAD coverage to noisy/conversational speech, threshold sweeps, and
   live PipeWire latency before selecting a default.
3. Expand ASR coverage to consented Chinese/mixed coding commands with
   controlled input gain, compare an alternative ASR, and measure recovery
   before selecting a default.
4. Test VAD disconnect/runtime failure, threshold behavior, and repeated live
   utterances without granting it endpoint control.
5. Add a multi-project working-directory registry before one Bridge process
   manages Agent sessions from different repositories.

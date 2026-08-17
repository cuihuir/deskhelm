# No-Hardware Software Roadmap

Date: 2026-08-17

Status: Active

## Goal

Deliver a useful local Agent Console before physical devices exist. The software
must support agent state, session targeting, voice input and interruptible voice
output through local interfaces while keeping the core Bridge dependency-free.

## Milestone 1: Bridge Core Boundaries

Status: In progress

- [x] Move slot state out of `SlotPanel` into `StateStore`.
- [x] Add in-process state subscriptions.
- [x] Add `SessionRegistry` with dynamic and legacy slot mapping.
- [x] Define session lifecycle: register, focus, disconnect, expire, restore.
- [x] Replace the sequential connection loop with a bounded concurrency model.
- [ ] Expose a read-only local snapshot and subscription API.
- [ ] Define adapter capabilities and captured-fixture version metadata.

Acceptance criteria:

- Existing `AgentEvent v1`, CLI, simulator, and Codex hook behavior remains
  compatible.
- Multiple local consumers can observe the same ordered state updates.
- A session is targeted by identity rather than by display slot alone.

## Milestone 2: Interaction and Control Protocols

Status: In progress

- [x] Write an ADR for the negotiated local transport and message envelopes.
- [x] Define versioned `InteractionEvent v1`.
- [x] Define versioned `ControlCommand v1`.
- [x] Add JSON fixtures and compatibility tests for both versioned models.
- [x] Define interaction ordering, correlation, and terminal-event semantics.
- [x] Define queue bounds, maximum record size, and slow-subscriber direction.
- [ ] Implement targeting and validation in a `ControlRouter`.
- [x] Require `request_id`, target session, summary, and expiry for approvals.
- [x] Require idempotency keys for retryable prompt and control operations.

Acceptance criteria:

- Rich text never leaks into the hardware state projection.
- Invalid versions, targets, commands, and expired approvals are rejected.
- Interrupt and stop-speaking commands can be routed without hardware.

## Milestone 3: Text-Only Agent Gateway

Status: Planned

- [ ] Wrap `codex exec --json` behind an agent-provider interface.
- [ ] Stream Codex JSONL into normalized interaction events.
- [ ] Support cancellation, process exit, timeout, and malformed output.
- [ ] Add a fake provider for deterministic tests.

Acceptance criteria:

- A text prompt can target a session and stream a final response locally.
- Cancellation stops the owned process and produces a terminal event.
- Tests do not require network access or a logged-in Agent CLI.

## Milestone 4: Voice Gateway Skeleton

Status: Planned

- [ ] Create an isolated `voice/` package with no import dependency from Bridge.
- [ ] Define audio capture, VAD, ASR, TTS, and playback provider interfaces.
- [ ] Implement PTT state handling and an interruptible speech queue.
- [ ] Preserve raw and normalized transcripts separately.
- [ ] Use fake providers to test the full pipeline before installing models.

Acceptance criteria:

- Fake audio can complete `PTT -> transcript -> agent -> speech queue`.
- Starting a new PTT cancels current interruptible playback.
- Audio device loss and provider failures produce recoverable states.

## Milestone 5: Local Audio Providers and Benchmarking

Status: Planned

- [ ] Add PipeWire capture and playback adapters.
- [ ] Benchmark Paraformer for streaming ASR.
- [ ] Benchmark Piper and Kokoro for notification TTS.
- [ ] Add fixed Chinese and mixed-language test utterances.
- [ ] Record latency, accuracy, resource use, recovery, and licensing results.

Acceptance criteria:

- The no-hardware POC completes `PTT -> ASR -> Codex -> TTS` locally.
- First partial, final transcript, TTS first audio, and interruption latency are
  measured reproducibly.
- Model weights and heavyweight runtime dependencies remain outside Bridge and
  outside version control.

## Immediate Backlog

1. Implement snapshot-then-live subscriptions without durable replay and
   enable the negotiated `subscriber` role.
2. Add bounded interaction fan-out and enable `interaction_event_v1`
   publishers.
3. Implement `ControlRouter`, bounded idempotency retention, command results,
   and the negotiated `controller` role.
4. Build the text-only Codex gateway before adding audio dependencies.

# ADR 0009: Isolate and Bound the Voice Gateway

- Status: Accepted
- Date: 2026-08-18

## Context

DeskHelm needs push-to-talk input and interruptible speech output without making
the dependency-minimal Bridge own audio devices, model runtimes, or private
voice content. The first implementation must be testable without hardware,
audio services, model weights, network access, or a live Agent runtime.

Voice input and output are asynchronous. Unbounded capture, synthesis,
playback, or notification queues could exhaust local resources or deliver stale
speech to the wrong session. Voice controls therefore need the same complete
session identity and explicit cancellation behavior as other controls.

## Decision

Create an isolated `voice/deskhelm_voice` package with no Bridge imports. It
owns provider-neutral models and interfaces for capture, ASR, TTS, and playback,
plus a `VoiceGateway` that provides:

- one active PTT capture and transcription flow;
- explicit press, release, and cancellation operations;
- separate raw and normalized transcripts;
- a fixed-capacity, priority-aware speech queue;
- one playback worker with per-item interruption;
- recoverable, content-free lifecycle and failure events;
- deterministic fake providers for no-hardware tests.

Every voice target is `agent_id + session_id + project_id`. Starting PTT
cancels current interruptible playback, and playback waits while PTT is active.
Speech can be stopped by `speech_id`; an empty ID stops interruptible speech for
the named target. Provider and event-sink failures do not expose audio,
transcripts, prompts, or speech text in ordinary logs.

Keep conversion between voice transcripts and Bridge controls in
`VoiceBridgeIntegration`. It submits normalized text as a targeted
`submit_prompt` command, registers `speak` and `stop_speaking` handlers, and
turns complete assistant messages into queued speech. Queue overflow produces a
Voice failure event rather than blocking or failing interaction publishers.

Voice activity detection is deferred until the local capture provider and its
streaming boundary are selected. No speculative VAD contract is added to the
batch-oriented fake pipeline.

## Consequences

- The full fake `PTT -> transcript -> Agent -> TTS -> playback` path is tested
  without hardware or external services.
- Bridge can compose a Voice Gateway in process without importing audio or
  model dependencies into its core modules.
- Provider implementations must honor cancellation and keep audio/model
  resources outside Bridge and version control.
- Voice events are currently internal process events, not a new durable wire
  protocol or replayable history.
- Local PipeWire, ASR, VAD, TTS, and playback selection remains a later
  benchmark phase.

## Implementation Status

The isolated models, provider contracts, gateway, fake providers, Bridge
composition, targeted controls, assistant speech routing, cancellation,
capacity handling, and deterministic integration tests are implemented.

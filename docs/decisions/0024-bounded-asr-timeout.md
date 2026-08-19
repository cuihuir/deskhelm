# ADR 0024: Add a Bounded Voice Gateway ASR Timeout

- Status: Accepted
- Date: 2026-08-19

## Context

Capture already has byte and duration limits, but the Voice Gateway previously
waited indefinitely for a provider's synchronous `transcribe` call. A model
startup, queued inference request, or native decoder stall could therefore keep
PTT in `transcribing` forever and prevent a later retry. Provider cancellation
is cooperative, and SenseVoice's native decode cannot be interrupted midway.

## Decision

- Give `VoiceGateway` a configurable `max_asr_seconds` deadline, defaulting to
  30 seconds and bounded to 120 seconds.
- Run one ASR request at a time on a dedicated worker. The Gateway waits in
  short bounded intervals so `cancel_ptt()` and `close()` remain responsive.
- On deadline expiry, set the provider cancel event, cancel a not-yet-started
  future, emit the fixed `voice_asr_timeout` failure, and return PTT to `idle`.
- Do not retry recognition implicitly. The next explicit PTT request is the
  retry boundary, matching ADR 0021.
- Treat provider cancellation as best effort. A provider already inside a
  native call may continue after the Gateway has returned; hard cancellation
  requires a process boundary and remains future work.

## Consequences

- A stuck or slow provider cannot hold the Gateway's user-visible PTT state
  indefinitely.
- A queued request includes time spent waiting for the single ASR worker in its
  deadline; provider instances remain serialized and state-safe.
- Timeout and cancellation are distinguishable from generic input failure, and
  no audio or transcript content is logged or included in events.
- Runtime restart and hard in-flight cancellation measurements remain open.

## Implementation Status

Implemented in `VoiceGateway`, `LocalVoiceConfig`, and the Bridge CLI. Fake
provider tests cover timeout recovery, the fixed error code, explicit retry,
and configuration validation. Native-provider hard cancellation is not yet
claimed.

# ADR 0011: Use Bounded Raw-PCM PipeWire Subprocess Providers

- Status: Accepted
- Date: 2026-08-18

## Context

DeskHelm now needs its first real local capture and playback boundary. The
no-hardware POC follows the computer's current PipeWire source and sink, with
optional manual stable-name overrides. VAD and ASR need PCM, and there is no
constrained transport link in this local path, so Opus would add latency and a
decode step without reducing a meaningful bottleneck.

The dependency-free Voice core must not gain PipeWire Python bindings or model
dependencies. Capture and playback processes must remain bounded, cancellable,
private-content-safe, and testable without opening a real audio device.

## Decision

Represent local provider audio explicitly as raw signed 16-bit little-endian
PCM with sample rate and channel count. PCM payloads must contain complete
sample frames.

Add `pw-cat` subprocess providers in the isolated Voice package:

- capture defaults to 16 kHz mono S16LE;
- omitting a source or sink target lets PipeWire resolve the current default for
  each new stream;
- an optional manual override uses a stable PipeWire node name, never a numeric
  object ID;
- an unavailable explicit target fails rather than silently falling back;
- capture is limited to 30 seconds and 1 MiB by default;
- playback is limited to 120 seconds and 16 MiB by default;
- providers own a new process session, suppress stderr, poll cancellation, and
  terminate then kill their process group within a bounded grace period;
- cancellation raises `VoiceCancelled`; other failures expose fixed provider
  errors without stderr, audio, or command output.

Use raw `pw-cat` mode with explicit rate, channels, and `s16` format. Do not
enable these providers automatically in the Bridge CLI yet. Deterministic tests
use a fake `pw-cat` subprocess and never access live microphone or speaker
devices.

Device preference above the provider remains policy, not a `pw-cat` concern:

```text
manual user selection
  -> connected DeskHelm keyboard microphone, after hardware exists
  -> computer default capture device
```

Disconnect fallback and user notification require a later device-lifecycle ADR.

## Consequences

- Local capture and playback can be implemented and validated without choosing
  VAD, ASR, or TTS models.
- The format is explicit enough for PipeWire and future benchmark recordings;
  fake providers must now emit frame-aligned PCM rather than opaque text bytes.
- Opus remains reserved for a future constrained ESP32-S3 wireless transport.
- Streaming partial ASR and TTS first-audio timing still require later provider
  interfaces.

## Implementation Status

The explicit PCM models, bounded PipeWire capture/playback providers, fake
subprocess coverage, documentation, and lifecycle tests are implemented.

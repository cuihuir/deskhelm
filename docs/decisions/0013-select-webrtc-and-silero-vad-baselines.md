# ADR 0013: Select WebRTC and Silero ONNX as Initial VAD Baselines

- Status: Accepted
- Date: 2026-08-18

## Context

The provider-neutral streaming benchmark is implemented, but it needs real
candidate adapters and a reproducible public audio run set before Voice Gateway
endpointing can be selected. The first comparison should include both a small
classical detector and a stronger neural detector without adding dependencies
to Bridge or committing model weights.

## Decision

Use these initial benchmark baselines:

- WebRTC VAD through `webrtcvad-wheels` 2.0.14, using mode 2, 20 ms frames,
  a 3-of-5 start trigger, and an 8-of-10 end trigger;
- Silero VAD 6.2.1 through ONNX Runtime 1.29.0, using 16 kHz, 512-sample
  windows, the official 64-sample recurrent context, threshold 0.5, negative
  threshold 0.35, 100 ms minimum silence, and 30 ms speech padding.

Keep imports lazy and provider-specific dependencies outside project runtime
requirements. A Silero provider may share one immutable ONNX Runtime session,
but every opened VAD stream owns and resets its recurrent state and context.
Configure ONNX Runtime for one sequential CPU thread during local benchmarks.

Use the Free Spoken Digit Dataset at commit
`26eb9aaf76e81b692f806f9140c2d2777410d7a1` for the first external run set.
Commit only the source manifest, HTTPS URLs, checksums, license identity, and
composition recipes. Downloaded audio, prepared WAV files, the Silero ONNX
model, and raw observations stay in ignored artifact directories.

Treat each source recording's full trimmed extent as speech and surround it
with generated silence. This gives deterministic derived boundaries, but it is
only a coarse label and does not prove performance on conversational speech,
noise, music, Chinese speech, or live microphone input.

## Consequences

- DeskHelm can reproduce comparable real-audio observations without changing
  Bridge dependencies or committing third-party assets.
- WebRTC provides a low-cost baseline and Silero provides a neural baseline.
- Benchmark initialization is separated from per-stream replay latency; model
  startup must be measured separately before production integration.
- The first result selects neither candidate as the final production VAD.
  Broader labeled audio, live PipeWire latency, recovery, and threshold sweeps
  remain required before Voice Gateway activation.

## Implementation Status

The manifest, deterministic preparation tool, prepared-sample loader, both
adapters, benchmark runner, tests, and first five-repetition comparison are
implemented. The aggregate result is recorded in the dated VAD research report.

# ADR 0012: Use Frame-Positioned PCM Chunks and Session-Based VAD

- Status: Accepted
- Date: 2026-08-18

## Context

DeskHelm has bounded batch PipeWire capture, but VAD must observe audio while it
arrives so push-to-talk release, endpointing, partial ASR, and interruption can
be measured without buffering an entire recording first. The boundary must stay
independent of Silero, WebRTC VAD, sherpa-onnx, or another model runtime.

Wall-clock timestamps alone are unsuitable for segmentation because scheduling
jitter can create gaps or overlaps. Model instances may also retain stream
state and native resources, so one global `detect(audio)` call would hide
lifecycle and concurrency behavior.

## Decision

Represent streaming capture as complete-frame `PcmChunk` values with:

- one immutable `PcmStreamFormat` containing sample rate, channels, and sample
  format;
- an absolute zero-based `start_frame` and a derived exclusive `end_frame`;
- at most 1 MiB of complete PCM frames in one chunk;
- contiguous chunks with no implicit resampling, channel conversion, or format
  change inside one stream.

A streaming capture provider opens an owned `PcmChunkStream`. The caller
reads one bounded chunk at a time with explicit stop and cancellation signals,
and closes the stream through its context-manager lifecycle.

A VAD provider opens one independent session per stream format. The session
accepts chunks in order and emits zero or more frame-positioned
`speech_started` / `speech_ended` events after each chunk. `finish()` flushes
lookahead at end of stream. Events must be bounded, ordered, alternating, no
later than the audio already supplied, and end every active speech region.

Extend the provider-neutral benchmark with VAD samples and observations:

- reference and predicted speech are non-overlapping frame intervals;
- observations store only derived durations and counts, never raw PCM;
- report speech precision, recall, F1, false-positive and false-negative
  duration, first-speech detection delay, processing latency, CPU time,
  real-time factor, and optional RSS/VRAM peaks;
- retain the existing 1 MiB record, 64 MiB file, and 10,000-observation limits;
- bound each sample to 100,000 chunks, 256 reference/predicted segments, and
  64 MiB of PCM;
- provider failures use a fixed error code without exception text or partial
  segmentation output.

The benchmark feeds chunks as fast as the provider can process them. It measures
compute latency and frame-relative detection delay separately; it does not
pretend that offline replay is a live end-to-end latency measurement.

## Consequences

- VAD candidates can be compared before selecting or installing a model.
- Absolute frame positions make segmentation deterministic and independent of
  scheduler timing.
- Session ownership leaves room for native model state and explicit cleanup.
- Live VAD composition, real-time endpointing, partial ASR, and device recovery
  remain later work.
- Model-specific thresholds, minimum speech/silence duration, padding, and
  resampling remain provider configuration and must be recorded with benchmark
  identity and run metadata.

## Implementation Status

The chunk/event models, provider protocols, deterministic fake capture/VAD
sessions, bounded runner, NDJSON observation format, CLI summary, metrics, and
tests are implemented. PipeWire now emits owned frame-positioned chunks, and
Voice Gateway validates and aggregates them under explicit chunk, byte, and
duration bounds while preserving the legacy batch capture protocol. PTT release
still ends capture; VAD is not yet attached to the live Gateway path.

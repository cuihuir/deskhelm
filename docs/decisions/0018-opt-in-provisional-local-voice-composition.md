# ADR 0018: Opt-In Provisional Local Voice Composition

Date: 2026-08-19

Status: Accepted

## Context

DeskHelm has independently tested PipeWire capture/playback, Paraformer ASR,
Piper TTS, external PTT controls, and a provider-neutral Voice Gateway. The
Bridge CLI still cannot compose those boundaries into one local path.

The current Voice Gateway receives one complete PCM recording after PTT
release. The VAD providers instead require a frame-positioned streaming capture
session. Inserting VAD after batch capture would not provide live endpointing,
partial recognition, or the measured streaming semantics and would create a
misleading integration.

Paraformer and Piper also remain provisional candidates. Their optional
runtimes, model artifacts, performance limits, language coverage, and Piper
packaging license are not suitable as unconditional Bridge dependencies or
production defaults.

## Decision

Add an explicitly enabled local Voice Gateway composition to `deskhelm bridge`.

- Keep voice disabled by default with `--voice-provider none`.
- `--voice-provider local` composes bounded PipeWire capture, Paraformer ASR,
  Piper TTS, and PipeWire playback through the existing Voice Gateway.
- Keep provider choices explicit in configuration so later ASR, TTS, capture,
  and playback implementations can replace the provisional candidates without
  changing the Bridge or Voice Gateway contracts.
- Require explicit Paraformer model, Piper model/config, and Piper resource
  paths. Verify the required files and the selected PipeWire source/sink before
  binding the Bridge socket.
- Keep model and runtime imports lazy. Startup validates composition without
  loading weights, opening audio streams, or importing FunASR, PyTorch, Piper,
  or ONNX Runtime.
- Retain fixed capture duration/byte limits, speech queue capacity, provider
  cancellation, complete session targeting, and correlated PTT release.
- Do not integrate VAD in this batch path. Migrate the gateway to streaming
  capture and define endpoint/partial/final timing before selecting WebRTC or
  Silero for live use.
- Do not package provider runtimes or model artifacts. They remain external and
  ignored; Piper distribution still requires the repository license decision
  and a GPL packaging review.

## Consequences

- An operator with an explicitly prepared runtime and artifacts can start the
  first real no-hardware local voice path.
- Ordinary Bridge startup remains dependency-minimal and unchanged.
- Missing manual devices or required artifacts fail before the server starts;
  there is no silent device or provider fallback.
- Model runtime availability is first exercised when ASR or TTS is requested,
  so a separate preflight/runtime diagnostic is still useful future work.
- PTT release currently marks the end of capture. Live VAD endpointing, partial
  transcript publication, and hot-device recovery remain unimplemented.

## Alternatives

- Enable local voice automatically when artifacts are found: rejected because
  audio/model activation must be deliberate and candidate status is provisional.
- Load models during Bridge startup: rejected because it adds seconds of
  latency, gigabytes of memory, and heavyweight imports before voice is used.
- Run VAD over the completed PTT recording: rejected because it does not
  implement the streaming behavior the VAD contract promises.
- Add provider-specific logic to Bridge: rejected because model construction
  belongs in the Voice composition boundary.

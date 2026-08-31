# ADR 0021: Bound Voice Recovery and Device Rebinding

- Status: Accepted
- Date: 2026-08-19

## Context

The first ASR comparison measured provider quality, but the next product risk
is what happens after a model failure, cancellation, PipeWire disconnect, or
audio-device change. Retrying blindly could duplicate a command or hide a
device loss. Keeping a failed provider permanently unusable would make a
recoverable local runtime failure unnecessarily terminal.

## Decision

- A single ASR provider instance may be reused after a model or decode failure.
  Its model/recognizer remains lazy and shared, while the inference lock is
  always released by `finally`.
- A new ASR request is the retry boundary. DeskHelm does not automatically
  retry a failed recognition or replay approval/rejection controls.
- Paraformer checks cancellation before each chunk and immediately after each
  inference call. SenseVoice checks before and after its native offline decode;
  that native call remains uninterruptible while executing.
- A PipeWire capture or playback process owns one session. Process failure,
  cancellation, or disconnect closes that session; a subsequent operation may
  start a fresh process.
- Default audio selection is resolved from a fresh PipeWire inventory snapshot.
  A changed default source/sink therefore rebinds the default configuration on
  the next resolve. A manually selected stable node name remains strict: if it
  disappears, resolution fails with a fixed unavailable error and never falls
  back to another device.

## Consequences

- Recovery behavior is deterministic and testable without live hardware.
- Callers must surface failure and decide when to issue a new request; no hidden
  recognition retries occur.
- In-flight SenseVoice cancellation can wait for one native decode call to
  return. A subprocess boundary would be required for hard cancellation.
- Device hot-plug recovery requires rediscovery/re-resolution by the caller;
  active streams are not silently migrated to another node.

## Implementation Status

Provider failure/cancellation, PipeWire process disconnect/retry, and default
versus manual device-change tests are implemented. Live hot-unplug and runtime
restart measurements remain outstanding. One persistent live Bridge session
has also completed three consecutive Codex turns, then recovered from an
explicitly interrupted Agent run and accepted a successful same-session retry.
Live owned `pw-cat` capture and playback processes have been forcibly
terminated as well; both exposed fixed private-content-free failure categories,
and the same provider instances succeeded on the next operation. Physical
device hot-unplug and default-device changes remain unverified.
The real Paraformer provider has also recovered in place from two live software
boundaries: a deliberately tiny cold-start Gateway deadline produced
`voice_asr_timeout` before a longer next request succeeded, and one controlled
`generate` failure released the inference lock before the same provider
successfully decoded the next request. No transcript, PCM, or provider failure
text was emitted by the diagnostic.

A physical PCM2902 USB microphone hot-plug cycle is also verified. Insertion
added a stable source name and WirePlumber selected it as default; explicit
stable-name capture produced valid signal. Fresh DeskHelm discovery followed
default changes in both directions. After unplug, default discovery fell back
to the built-in microphone while explicit USB selection failed closed. Replug
restored the same stable source and valid capture, after which the original
built-in default was restored. Numeric PipeWire object IDs were resolved only
for immediate `wpctl` operations and were not persisted.

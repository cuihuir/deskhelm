# ADR 0025: Isolate the whisper.cpp ASR Provider Behind a C Shim

- Status: Accepted
- Date: 2026-08-20

## Context

The whisper.cpp fallback research confirmed a useful in-memory float-PCM C API
and a cooperative `ggml_abort_callback`, but its large C parameter structure is
not a stable Python binding boundary. The runtime, model weights, and native
build must remain outside the dependency-minimal Bridge.

## Decision

- Add an opt-in `WhisperCppAsrProvider` that loads an explicit shared-library
  path and model path lazily.
- Compile a DeskHelm-specific C shim against whisper.cpp `v1.9.2`; expose only
  model lifecycle, bounded transcription, a volatile abort flag, fixed status
  codes, and a versioned ABI string.
- Accept only 16 kHz mono S16LE audio, convert it to bounded in-memory float32,
  cap transcript output at 4,096 characters with a bounded UTF-8 buffer,
  serialize one native context, and return no partial latency claim for batch
  `whisper_full()` decoding.
- Map native cancellation and empty output to the existing
  `VoiceCancelled`/`VoiceNoTranscript` boundaries. Native failures remain a
  recoverable provider failure and never trigger an implicit fallback.
- Keep the provider out of `LocalVoiceConfig`, the Bridge default, model
  packaging, and repository build dependencies until benchmark and packaging
  evidence justifies a later decision.

## Consequences

- Python does not depend on NumPy, PyTorch, or the upstream struct layout for
  this prototype.
- Native cancellation can be requested through the upstream abort callback,
  but it remains cooperative and requires an externally built compatible shim.
- The ABI and external build are additional packaging surfaces; the pinned
  upstream tag and model provenance must be recorded before benchmark claims.
- Existing Paraformer and SenseVoice candidates are unchanged and remain the
  only local composition choices.

## Validation

The provider has fake-native unit tests for lazy loading, PCM conversion,
status mapping, lock recovery, and cancellation propagation. A real shim build
against an external `v1.9.2` checkout remains an optional validation step and
does not download weights or add native artifacts to Git.

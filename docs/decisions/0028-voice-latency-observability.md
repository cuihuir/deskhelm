# ADR 0028: Voice Latency Observability

Date: 2026-08-24

Status: Accepted

## Context

The local voice alpha now completes physical PTT, ASR, Codex, and Piper
playback. Existing diagnostics can time capture release, final transcript, and
the pre-synthesis `speech_started` event, but cannot distinguish TTS synthesis
from playback handoff. Calling that event speaker-first-audio would be false.

## Decision

- Preserve the existing `speech_started` meaning for compatibility: a speech
  worker accepted an item before TTS synthesis begins.
- Add privacy-safe Voice lifecycle stages for the first complete TTS provider
  result and the first successful write to the PipeWire playback process.
- Live diagnostics report monotonic deltas between stages and retain no PCM,
  transcript, prompt, or assistant text.
- `speaker_first_audio` remains explicitly unmeasured. PipeWire process input
  is a handoff boundary, not a guarantee that a loudspeaker has emitted sound.
- Codex timing remains a Bridge-level concern. A later targeted metric
  subscriber may correlate transcript dispatch, Agent completion, and speech
  stages without retaining interaction content.

## Consequences

- The alpha can separate ASR, TTS, and PipeWire handoff delay without changing
  its provider or fallback policy.
- Existing clients retain `speech_started` semantics; new clients opt into the
  additional stages.
- Actual acoustic latency still requires a separately designed loopback or
  hardware measurement and must not be inferred from these events.

# ADR 0016: Explicit Local Audio Selection and Diagnostics

Date: 2026-08-19

Status: Accepted

## Context

DeskHelm has bounded PipeWire capture and playback providers, but applications
previously had no supported way to resolve the current defaults, validate a
stable manual target, or test the real audio path. Enabling a complete Voice
Gateway in the Bridge CLI would also require prematurely fixing ASR, TTS, VAD,
and external PTT controls that remain under evaluation.

Audio diagnostics can expose private microphone data or create unexpected
sound, so discovery and active tests need different, explicit commands.

## Decision

Add an application-level local audio configuration boundary and the nested
`deskhelm audio` CLI without activating production voice models in Bridge.

- Support `pipewire` as the first capture and playback provider kind.
- Select the current PipeWire default source and sink when no override exists.
- Accept manual targets only as stable `node.name` values. Reject numeric IDs.
- If a manual target is absent, fail explicitly; never fall back to another
  source or sink.
- Discover nodes through bounded, time-limited `pw-dump` output and resolve
  defaults through bounded `wpctl inspect` calls.
- Keep `audio status` read-only. It may list stable names and descriptions but
  must not open an audio stream.
- Make `audio test-input` and `audio test-output` explicit user actions.
  Input PCM is held only in memory, reduced to duration/peak/RMS metadata, and
  discarded. Output is a short, bounded, low-volume generated tone.
- Keep diagnostics bounded by duration, byte count, process ownership, command
  timeout, output size, and fixed private-content-safe errors.
- Keep selection process-local for now. Do not add a persistent configuration
  format until the configurator and keyboard-microphone discovery rules exist.
- Do not wire candidate VAD, ASR, or TTS providers into the Bridge CLI in this
  phase. Their final defaults and PTT control surface are separate decisions.

## Consequences

- Users and future configurator code share one validated provider/device
  selection model.
- Default and manual local devices can be checked before loading any model.
- Live diagnostics no longer require ad hoc shell pipelines or saved recordings.
- The Bridge service still needs a later composition step for PTT controls,
  VAD, ASR, TTS, and playback routing.
- Hot unplug, default-device changes during a stream, and future DeskHelm
  keyboard microphone preference remain unimplemented recovery work.

## Alternatives

- Add PipeWire flags directly to `deskhelm bridge`: rejected because they would
  appear to enable a usable voice path without PTT and selected model providers.
- Persist a configuration file now: rejected because the configurator schema,
  hardware identity, and disconnect fallback policy are not yet selected.
- Rely on numeric PipeWire IDs: rejected because IDs change as the graph is
  recreated.
- Store captured diagnostic WAV files: rejected because signal metadata is
  sufficient for this preflight and avoids retaining microphone content.

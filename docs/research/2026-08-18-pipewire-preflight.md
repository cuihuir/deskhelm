# PipeWire Provider Preflight

Date: 2026-08-18

Status: Verified local capability; bounded provider boundary implemented.

## Verified Environment

- PipeWire command-line tools `pw-cat`, `pw-cli`, `pw-dump`, and `wpctl` are
  installed.
- `pw-cat` is compiled and linked against PipeWire 1.6.8.
- WirePlumber exposes one configured default analog source and one default
  analog sink on the current Fedora workstation.
- The stable node names observed are
  `alsa_input.pci-0000_00_1f.3.analog-stereo` and
  `alsa_output.pci-0000_00_1f.3.analog-stereo`.
- `pw-cat` supports record/playback mode, raw audio, explicit rate, channels,
  channel map, sample format, target node, latency, and bounded sample count.

No microphone audio was captured and no desktop audio configuration was
changed during this preflight.

## Implemented Boundary

- Do not persist numeric PipeWire object IDs; they are process-local and can
  change. Resolve the configured default or a stable node name for each run.
- DeskHelm audio models now declare raw S16LE PCM, sample rate, channels,
  complete-frame alignment, and duration.
- `PipeWireCaptureProvider` and `PipeWirePlaybackProvider` use `pw-cat` without
  Python bindings, own process groups, enforce byte/time limits, suppress
  private stderr, and honor stop/cancel through bounded terminate/kill cleanup.
- Omitting `--target` resolves the current PipeWire default for each new stream;
  manual overrides accept stable names and fail rather than falling back.
- Deterministic fake-subprocess tests cover failures and lifecycle cleanup
  without exercising the microphone or speaker.
- System Python is 3.14.6 while DeskHelm supports Python 3.11 and newer. Model
  environments should remain separate because many ASR/TTS stacks may lag the
  system interpreter.

## Remaining Validation

ADR 0011 records the PCM, bounds, process, and device-targeting contract. Live
device enumeration, explicit application configuration, disconnect/reconnect
behavior, and latency measurements remain unverified. Those checks should be
performed only when the composition layer is ready to expose device selection;
this phase intentionally captured no microphone audio.

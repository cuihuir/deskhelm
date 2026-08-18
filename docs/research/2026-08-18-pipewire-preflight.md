# PipeWire Provider Preflight

Date: 2026-08-18

Status: Verified local capability; provider implementation not started.

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

## Implementation Implications

- Do not persist numeric PipeWire object IDs; they are process-local and can
  change. Resolve the configured default or a stable node name for each run.
- The current DeskHelm audio models carry bytes, rate, and channels but do not
  declare PCM sample format or container. A real PipeWire provider must first
  add an explicit audio-format contract; assuming every provider emits raw
  signed 16-bit PCM would create a hidden compatibility bug.
- A subprocess provider can initially use `pw-cat` without adding Python
  bindings, but it must own process groups, bound captured bytes, suppress
  private stderr, honor stop/cancel promptly, and handle default-device changes.
- Capture and playback recovery must be tested with stable fake subprocesses
  before exercising actual device disconnects.
- System Python is 3.14.6 while DeskHelm supports Python 3.11 and newer. Model
  environments should remain separate because many ASR/TTS stacks may lag the
  system interpreter.

## Next Decision

Before implementing PipeWire providers, record an ADR for the PCM/container
model, maximum capture duration and byte count, process termination semantics,
device targeting, and recovery behavior. This is independent of choosing an
ASR or TTS model.

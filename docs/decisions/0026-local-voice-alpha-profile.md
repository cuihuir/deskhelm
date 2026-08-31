# ADR 0026: Local Voice Alpha Profile

Date: 2026-08-21

Status: Accepted

Updated: 2026-08-28

## Decision

DeskHelm Alpha is hardware-independent by default. `deskhelm alpha --tui` owns
Bridge, one Codex app-server, one modern session registration, and the managed
Codex TUI without requiring audio models, microphone access, an input device,
the isolated voice runtime, or project secrets.

Local voice is an explicit `--voice` extension. In that mode, Alpha uses
Paraformer ASR and selects Piper or Kokoro TTS from the configured artifact
directory through the existing `--voice-provider local` composition, then
starts the right-Alt PTT controller. A Kokoro directory uses the female voices
`zf_xiaobei` for Chinese or mixed text and `af_heart` for English-only text.
WebRTC VAD remains disabled by default and advisory when selected. No provider
automatically falls back to SenseVoice, whisper.cpp, another TTS provider,
another device, or a cloud service.

Voice failures publish one fixed, privacy-safe system message to interaction
subscribers for the affected complete session. Agent execution and text replies
remain available when input, ASR, TTS, or speech queue work fails.

The reusable single-project startup entry point is `deskhelm alpha`. Its core
Bridge runs from the dependency-minimal root environment. Only `--voice` moves
Bridge composition into the isolated local-voice uv project and starts the PTT
controller for the exact registered target.

External model locations are not stored in the repository, profile source, or
command documentation. In `--voice` mode, the operator supplies model locations
and an optional stable manual input through these environment variables:

- `DESKHELM_ALPHA_ASR_MODEL_DIRECTORY`
- `DESKHELM_ALPHA_TTS_DIRECTORY`
- `DESKHELM_ALPHA_VOICE_SOURCE` (optional)

The Piper directory uses the pinned `zh_CN-chaowen-medium` artifact names. The
Kokoro directory uses the pinned `kokoro-v1_0.pth`, `config.json`,
`zf_xiaobei.pt`, and `af_heart.pt` artifacts. A directory containing zero or
more than one supported model fails closed. Core `--check` validates the
working directory, sockets, and Codex
executable/version. `--check --voice` additionally validates the ignored
artifacts, synchronized voice uv runtime, stable PTT device, and PipeWire
selection without opening audio or loading model weights. Startup fails if the
selected socket already exists rather than replacing another Bridge. An
explicit `--voice-source` takes precedence over the environment value. Either
manual selection fails if its stable PipeWire name is unavailable and never
falls back to the current default source.

## Consequences

- The primary Alpha path remains usable when voice configuration, models,
  microphones, PipeWire, or PTT hardware are unavailable.
- One command owns Bridge, app-server, session registration, and managed TUI
  lifetime for the current single working directory.
- `--voice` adds local audio and PTT without redefining the core product path.
- Alpha can select the lower-latency Piper baseline or the Kokoro female voice
  without adding a provider-specific CLI option to the daily startup command.
- Model paths remain explicit local environment state and do not enter Git.
- A tested microphone can be selected persistently without making one machine's
  PipeWire node name the repository default.
- The profile remains single-project; ADR 0031 provides separate strict
  multi-project Bridge mappings without adding durable sessions.
- Paraformer and Kokoro latency and memory remain accepted alpha risks.
- SenseVoice remains excluded until its matching verified native ABI is
  configured. No unverified runtime substitution is permitted.

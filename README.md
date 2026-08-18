# DeskHelm

**A local-first control surface for coding agents.**

DeskHelm is an open hardware and software platform that brings coding agents
out of the terminal and onto the desk. It combines live status, deliberate
controls, voice interaction, device firmware, and a local Bridge that works
across agent runtimes.

The project is designed around a simple idea: you should be able to see what every agent is doing and respond without hunting through windows.

## Vision

- Make concurrent agent work visible at a glance.
- Provide deliberate physical controls for approve, reject, interrupt, switch, and speak.
- Support multiple coding agents through an open event model.
- Keep prompts, code, and event data local by default.
- Build hardware that is expressive, modular, and genuinely fun to use.

## Repository

| Path | Purpose |
|---|---|
| [`docs/`](docs/) | Research, architecture, product, and engineering documentation |
| [`hardware/`](hardware/) | Electronics, PCB, mechanical, manufacturing, and test assets |
| [`firmware/`](firmware/) | Device firmware and bootloader integration |
| [`bridge/`](bridge/) | Local service connecting agents to physical devices |
| [`adapters/`](adapters/) | Integrations for individual coding-agent runtimes |
| [`voice/`](voice/) | Isolated PTT, ASR, TTS, and playback gateway |
| [`protocol/`](protocol/) | Device and agent event protocol specifications |
| [`configurator/`](configurator/) | Device setup and customization application |
| [`tools/`](tools/) | Development, manufacturing, and validation utilities |
| [`tests/`](tests/) | Cross-component fixtures and system tests |

## Status

Phase 0 software validation is implemented: a local Unix socket bridge,
four-slot terminal panel, normalized event protocol, simulator, and Codex hook
adapter. The Bridge now separates state storage, session-to-slot projection, and
terminal rendering while remaining compatible with `AgentEvent v1`.
The local server now has bounded concurrent connection handling, negotiated
state and interaction publishers, atomic state snapshot/live subscriptions,
bounded live-only rich interaction subscriptions, and negotiated controllers
with targeted routing and correlated results. Adapter publishers now declare
runtime capabilities and drive complete session registration, disconnect,
restore, and release lifecycle. Agent prompt and interruption handlers are
available through an explicitly enabled bounded text gateway. The Codex
provider streams normalized JSONL results and supports interruption, timeout,
and process-local session resume without placing prompts in process arguments.
An isolated bounded Voice Gateway now completes the fake PTT, transcript, Agent,
TTS, and playback pipeline, with targeted speech controls and no audio/model
dependencies in Bridge. Voice benchmarking now has a versioned Chinese,
English, and mixed-language corpus plus bounded provider-neutral accuracy,
latency, resource, and licensing observations. The first real local audio
boundary is now available as bounded PipeWire capture/playback providers using
explicit raw PCM, the current default devices or stable-name overrides. The CLI
does not activate live audio yet. A frame-positioned streaming PCM/VAD session
contract and bounded provider-neutral VAD benchmark are also implemented. The
first pinned FSDD comparison now runs WebRTC and Silero ONNX adapters outside
Bridge; broader noisy/live evidence is still required before selecting a
production VAD. The first pinned Paraformer streaming ASR run is also complete:
Chinese transcription and CPU real-time performance are promising, but English
segmentation and short English commands prevent selecting it as the sole
production ASR.

Current development focuses on the no-hardware Agent Console path:

```text
Bridge state and sessions
  -> interaction and control protocols
  -> text-only Agent gateway
  -> bounded Voice Gateway skeleton
  -> bounded PipeWire audio providers
  -> initial external-audio WebRTC/Silero VAD comparison
  -> initial Paraformer streaming ASR baseline
  -> broader VAD/ASR, TTS, and recovery measurements
```

Hardware, firmware, and device transport decisions remain exploratory until
recorded in an ADR.

## Quickstart

The current Bridge requires Python 3.11 or newer and has no runtime
dependencies.

Start the local Bridge:

```bash
PYTHONPATH=bridge python3 -m deskhelm_bridge bridge --plain
```

Enable the text-only Codex gateway for the current repository:

```bash
PYTHONPATH=bridge:adapters/codex python3 -m deskhelm_bridge bridge --plain \
  --agent-provider codex \
  --agent-workdir "$PWD"
```

The provider is disabled by default and uses the Codex read-only sandbox unless
`--codex-sandbox workspace-write` is selected explicitly.

In another terminal, run the four-agent simulator:

```bash
PYTHONPATH=bridge python3 -m deskhelm_bridge simulate
```

Run the complete test suite:

```bash
PYTHONPATH=bridge python3 -m unittest discover -s tests -v
```

After an editable install, use the `deskhelm` command. The pre-release
`agent-io` command and `python -m agent_io_bridge` remain temporary compatibility
aliases; new integrations should use DeskHelm names.

## Development

- [`bridge/README.md`](bridge/README.md) contains Bridge and Codex hook usage.
- [`voice/README.md`](voice/README.md) documents Voice Gateway boundaries and
  fake-provider tests.
- [`protocol/README.md`](protocol/README.md) documents the current wire event.
- [`docs/software/no-hardware-roadmap.md`](docs/software/no-hardware-roadmap.md)
  tracks the active software milestones.
- [`docs/decisions/`](docs/decisions/) records decisions before implementation
  spreads across components.
- [`Handoff.md`](Handoff.md) captures the current project state, decisions,
  validation, risks, and next steps.

Before committing, run:

```bash
git diff --check
PYTHONPATH=bridge python3 -m unittest discover -s tests -v
```

## Principles

1. **Agent-agnostic** — no single coding agent owns the experience.
2. **Local-first** — device control must not require uploading private work.
3. **Open interfaces** — integrations communicate through documented events and protocols.
4. **Safe controls** — consequential actions require clear target and state feedback.
5. **Great hardware** — this is a new physical computing product, not a terminal shortcut accessory.

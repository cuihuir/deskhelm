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
| [`bridge/`](bridge/) | The Bridge, session hosts, surfaces, and voice host |
| [`adapters/`](adapters/) | Integrations for individual coding-agent runtimes |
| [`voice/`](voice/) | Isolated PTT, ASR, TTS, and playback gateway |
| [`protocol/`](protocol/) | The local protocol v2 specification |
| [`configurator/`](configurator/) | Device setup and customization application |
| [`tools/`](tools/) | Development, manufacturing, and validation utilities |
| [`tests/`](tests/) | Cross-component fixtures and system tests |

## Status

DeskHelm 0.1.0 is the first release of the protocol v2 architecture
(ADR 0043). It runs from a source checkout on Linux and needs no DeskHelm
hardware yet.

- **Bridge**: a local control plane on one Unix socket. It owns session facts,
  focus, and approvals, runs no agent process, and can restart without
  stopping an agent.
- **Session hosts**: `deskhelm codex` runs the Codex TUI in your terminal and
  routes its approvals through a local proxy. `deskhelm agent claude` and
  `deskhelm agent grok` run Claude Code and Grok.
- **Surfaces**: a loopback web page, a terminal dashboard, and the `sessions`
  and `focus` commands, all reading one console projection.
- **Voice**: hold right Alt to speak to the focused session, with local
  Paraformer ASR and Piper, Kokoro, or IndexTTS notices. Exact phrases answer
  a single pending approval. Voice is optional and needs its own runtime.

Hardware, firmware, and device transport remain exploratory until recorded in
an ADR. The VAD, ASR, and TTS evaluations behind the voice choices are in
[`voice/README.md`](voice/README.md) and [`docs/research/`](docs/research/).

## Quickstart

DeskHelm needs Python 3.11 or newer, [uv](https://docs.astral.sh/uv/), and
the agent CLIs you want to use. From a clone:

```bash
uv sync --extra ptt --locked
ln -s "$PWD/tools/deskhelm-user-launcher" ~/.local/bin/deskhelm
```

Then, from an agent project directory:

```bash
deskhelm codex --check     # find Codex and check the Bridge socket
deskhelm codex             # the managed Codex TUI as one session
deskhelm agent claude      # or: deskhelm agent grok
deskhelm web               # open the printed http://127.0.0.1:8766/?token=... link
deskhelm dashboard         # or watch and steer from a terminal
deskhelm simulate          # a demo session that needs no agent
```

The first host starts the Bridge at `$XDG_RUNTIME_DIR/deskhelm/bridge.sock`
and leaves it running. `deskhelm --help` lists every command, and
[`bridge/README.md`](bridge/README.md) describes them.

Local voice needs the pinned voice runtime and model directories kept outside
Git:

```bash
(cd voice/runtime && uv sync --locked --python 3.12)
deskhelm codex --voice     # or attach to a running Bridge: deskhelm voice
```

The launcher loads `DESKHELM_ALPHA_VOICE_SOURCE`,
`DESKHELM_ALPHA_ASR_MODEL_DIRECTORY`, and `DESKHELM_ALPHA_TTS_DIRECTORY`
(plus an optional `DESKHELM_ALPHA_VOICE_SINK`) through `project-secrets`.
Without that tool, export them and run `.venv/bin/deskhelm` directly. Add
`--default-source` to use the computer's current microphone. Inspect audio
devices with `deskhelm audio status --list`.

## Development

- [`bridge/README.md`](bridge/README.md): the Bridge, hosts, surfaces, and
  voice host.
- [`adapters/README.md`](adapters/README.md) and
  [`adapters/codex/README.md`](adapters/codex/README.md): the agent drivers.
- [`voice/README.md`](voice/README.md): the Voice Gateway, providers, and
  benchmarks.
- [`protocol/deskhelm-v2.md`](protocol/deskhelm-v2.md): the wire contract.
- [`docs/decisions/`](docs/decisions/): decisions recorded before
  implementation spreads across components.
- [`Handoff.md`](Handoff.md): the current state, risks, and next steps.

Before committing, run:

```bash
git diff --check
uv run --project voice/runtime --locked --no-sync python -m unittest discover -s tests -v
```

## Principles

1. **Agent-agnostic** — no single coding agent owns the experience.
2. **Local-first** — device control must not require uploading private work.
3. **Open interfaces** — integrations communicate through documented events and protocols.
4. **Safe controls** — consequential actions require clear target and state feedback.
5. **Great hardware** — this is a new physical computing product, not a terminal shortcut accessory.

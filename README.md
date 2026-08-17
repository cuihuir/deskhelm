# agent-io

**Physical I/O for coding agents.**

`agent-io` is an open hardware and software platform that brings coding agents out of the terminal and onto the desk. It combines illuminated status controls, programmable inputs, device firmware, and a local bridge that works across agent runtimes.

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
| [`protocol/`](protocol/) | Device and agent event protocol specifications |
| [`configurator/`](configurator/) | Device setup and customization application |
| [`tools/`](tools/) | Development, manufacturing, and validation utilities |
| [`tests/`](tests/) | Cross-component fixtures and system tests |

## Status

Phase 0 software validation is implemented: a local Unix socket bridge,
four-slot terminal panel, normalized event protocol, simulator, and Codex hook
adapter. The Bridge now separates state storage, session-to-slot projection, and
terminal rendering while remaining compatible with `AgentEvent v1`.

Current development focuses on the no-hardware Agent Console path:

```text
Bridge state and sessions
  -> interaction and control protocols
  -> text-only Agent gateway
  -> PTT, ASR, interruptible TTS
```

Hardware, firmware, and device transport decisions remain exploratory until
recorded in an ADR.

## Quickstart

The current Bridge requires Python 3.11 or newer and has no runtime
dependencies.

Start the local Bridge:

```bash
PYTHONPATH=bridge python3 -m agent_io_bridge bridge --plain
```

In another terminal, run the four-agent simulator:

```bash
PYTHONPATH=bridge python3 -m agent_io_bridge simulate
```

Run the complete test suite:

```bash
PYTHONPATH=bridge python3 -m unittest discover -s tests -v
```

## Development

- [`bridge/README.md`](bridge/README.md) contains Bridge and Codex hook usage.
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

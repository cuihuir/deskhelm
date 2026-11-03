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
dependencies in Bridge. Negotiated controllers can now issue correlated,
session-targeted PTT press/release controls when that gateway is composed;
stale releases cannot stop a newer capture. Voice benchmarking now has a
versioned Chinese, English, and mixed-language corpus plus bounded
provider-neutral accuracy, latency, resource, and licensing observations. The
first real local audio boundary is now available as bounded PipeWire
capture/playback providers using explicit raw PCM, the current default devices
or stable-name overrides. The CLI now provides read-only discovery plus
explicit input/output diagnostics; the Bridge can also compose provisional
PipeWire, Paraformer, and Piper
providers only through an explicit disabled-by-default option. Models remain
lazy and external. PipeWire capture now exposes continuous complete-frame PCM
chunks with absolute frame positions; Voice Gateway validates and aggregates
that stream under explicit chunk, byte, and duration bounds until PTT release.
Legacy batch capture providers remain compatible. Optional advisory WebRTC VAD
now observes the same bounded streaming capture while PTT release remains
authoritative and final ASR always receives the complete recording; partial ASR
is still deferred. A unified ignored Python 3.12 runtime has completed a
real bounded `PTT -> final ASR -> fixed TTS -> PipeWire playback` diagnostic on
the current USB microphone and computer sink. This is not yet the real
Codex-response path, and actual first-speaker-audio latency remains unmeasured. A
frame-positioned streaming PCM/VAD session contract and bounded
provider-neutral VAD benchmark are also implemented. The
first pinned FSDD comparison now runs WebRTC and Silero ONNX adapters outside
Bridge. A second pinned AMI/ESC-10 set adds annotated meeting speech, real rain
and clock noise, WebRTC/Silero parameter sweeps, and a privacy-safe live
PipeWire timing diagnostic. WebRTC mode 0 led aggregate replay F1, but confirmed
live spoken and broader language/gain evidence are still required before
selecting a production VAD. The first pinned Paraformer streaming ASR run is
also complete:
Chinese transcription and CPU real-time performance are promising, but English
segmentation and short English commands prevent selecting it as the sole
production ASR. The first pinned Piper/Kokoro TTS comparison is complete:
Piper is the initial low-latency notification baseline, while Kokoro remains a
quality candidate pending human listening and live playback evidence.
Voice Gateway ASR now waits under a configurable bounded deadline and reports a
fixed timeout outcome; native-provider hard cancellation remains unverified.

Current development focuses on the no-hardware Agent Console path:

```text
Bridge state and sessions
  -> interaction and control protocols
  -> text-only Agent gateway
  -> bounded Voice Gateway skeleton
  -> bounded PipeWire audio providers
  -> frame-positioned streaming capture migration
  -> initial external-audio WebRTC/Silero VAD comparison
  -> pinned noisy conversation VAD sweeps and live timing
  -> initial Paraformer streaming ASR baseline
  -> initial Piper/Kokoro notification TTS comparison
  -> broader live VAD/ASR/TTS and recovery measurements
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

For multiple repositories, replace `--agent-workdir` with repeated strict
project mappings:

```bash
PYTHONPATH=bridge:adapters/codex python3 -m deskhelm_bridge bridge --plain \
  --agent-provider codex \
  --agent-project deskhelm=/path/to/deskhelm \
  --agent-project another-project=/path/to/another-project
```

`project_id` remains an opaque protocol identity. Paths are approved only at
Bridge startup; unmapped projects fail without falling back to another working
directory. `--agent-workdir` and `--agent-project` are mutually exclusive.

The reusable single-project Alpha treats the managed Codex CLI as the product
core. It owns Bridge, one Codex app-server, and one modern DeskHelm session
registration without requiring audio models, microphone access, an input
device, or project secrets. Local voice and the right-Alt PTT controller are an
explicit `--voice` extension.
For daily source-checkout use, link the user launcher once:

```bash
ln -s /absolute/path/to/deskhelm/tools/deskhelm-user-launcher \
  ~/.local/bin/deskhelm
```

Then start DeskHelm from any project directory without a repository-specific
command:

```bash
deskhelm codex --check
deskhelm codex --tui
deskhelm codex --tui --voice
```

The default CLI path needs only the synchronized root environment and Codex.
The launcher preserves the invoking directory as the Agent working directory
and uses a bounded standard-library PTY relay for interactive TUI rendering and
the two-press Ctrl-C guard. Core mode still does not read project secrets. Only
`--voice` loads encrypted settings through `project-secrets`; that mode
additionally requires `uv sync --extra ptt --locked` plus the synchronized
`voice/runtime`. Model locations remain outside Git and are never copied into
the launcher. Both profiles refuse existing
Bridge or app-server sockets and close their session and owned children on
exit. After startup the two-terminal form
prints a manual `codex --remote unix://... --sandbox read-only
--ask-for-approval on-request` command. That TUI and the DeskHelm adapter share
the same app-server thread. DeskHelm can target a newly loaded empty thread
immediately and keeps a bounded resume retry active until manual TUI turns are
observable; an unrelated plain `codex` process cannot be attached after
launch. A manual source is validated by stable name and fails without fallback
when unavailable; an explicit `--voice-source` overrides the environment
value. The configured TTS directory selects either Piper or Kokoro. The Kokoro
profile uses female voice `zf_xiaobei` for Chinese or mixed notifications and
female voice `af_heart` for English-only notifications.

Add `--tui` for the single-terminal path. Alpha launches that same remote TUI
in the foreground after the core services are ready. Every launcher-managed TUI
uses the bounded relay: the first raw or Kitty-protocol Ctrl-C clears input, and
only a second press within the confirmation window reaches Codex. Timeout or
other input resets the guard. Core mode remains hardware-independent; voice mode
adds boundary-safe redaction because `project-secrets` otherwise line-buffers
terminal output. TUI exit is the Alpha shutdown boundary. Repeated interrupts
during teardown are ignored while every owned process group and socket is
independently cleaned. The relay also stops waiting when its primary child exits
even if a descendant temporarily retains a PTY file descriptor. Without
`--tui`, Alpha keeps printing the command for an independently started terminal.

When the managed TUI reports `waitingOnApproval`, Alpha speaks one fixed
confirmation notice. That status alone is notification-only and cannot be
approved through DeskHelm because it has no correlated request ID. When exactly
one actionable approval is pending for the target session, only the bounded
exact approval phrases `批准`, `确认`, `同意`, `允许`, and `可以`, or rejection
phrases `拒绝`, `不同意`, `不允许`, and `取消`, can decide it. Other text is never
inferred as an approval. Permission approval grants only the requested subset
for the current turn. Single non-secret user-input questions can be answered by
the next PTT transcript; ambiguous, multi-question, or secret requests stay in
the TUI.

The provisional model-backed Voice Gateway is also disabled by default. See
[`bridge/README.md`](bridge/README.md) for its explicit artifact and runtime
configuration. Optional WebRTC VAD can publish advisory input-activity events,
but PTT release remains the only capture endpoint and VAD is disabled by
default.

The pinned optional Python 3.12 uv runtime is documented in
[`voice/runtime/pyproject.toml`](voice/runtime/pyproject.toml) and its lock
file.
The live diagnostic and its privacy limits are documented in
[`voice/README.md`](voice/README.md) and the dated
[`research report`](docs/research/2026-08-19-local-voice-runtime-and-live-path.md).
The separate controlled ASR diagnostic uses a versioned public phrase and
reports only signal, matching, and latency metrics without speaker playback.
It supports explicit Paraformer and final-only SenseVoice selection; neither is
the production default. The same diagnostic can run a bounded batch by
repeating `--utterance-id`; each phrase remains separately confirmation-gated.
For chat-driven timing, `--await-phrase-ready` adds a bounded exact-`ready`
handshake before every capture.

Inspect the current PipeWire defaults without opening audio:

```bash
PYTHONPATH=bridge:voice python3 -m deskhelm_bridge audio status --list
```

Explicit diagnostics capture PCM only in memory and discard it, or play a short
low-volume tone:

```bash
PYTHONPATH=bridge:voice python3 -m deskhelm_bridge audio test-input --seconds 2
PYTHONPATH=bridge:voice python3 -m deskhelm_bridge audio test-output
```

In another terminal, run the four-agent simulator:

```bash
PYTHONPATH=bridge python3 -m deskhelm_bridge simulate
```

Run the complete test suite:

```bash
PYTHONPATH=bridge uv run --project voice/runtime --locked --no-sync python -m \
  unittest discover -s tests -v
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
PYTHONPATH=bridge uv run --project voice/runtime --locked --no-sync python -m \
  unittest discover -s tests -v
```

## Principles

1. **Agent-agnostic** — no single coding agent owns the experience.
2. **Local-first** — device control must not require uploading private work.
3. **Open interfaces** — integrations communicate through documented events and protocols.
4. **Safe controls** — consequential actions require clear target and state feedback.
5. **Great hardware** — this is a new physical computing product, not a terminal shortcut accessory.

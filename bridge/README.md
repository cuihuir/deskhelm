# Bridge

Local-first service that normalizes coding-agent events for DeskHelm clients and
future devices.

## Internal Boundaries

- `StateStore` owns the current state projection and publishes snapshots.
- `SessionRegistry` maps agent, session, and project identity to display slots,
  tracks active and disconnected lifecycle state, and owns explicit focus.
- `SlotPanel` is a terminal subscriber and does not own Bridge state.

The current wire protocol still accepts the explicit `slot` required by
`AgentEvent v1`. Dynamic session allocation is an internal capability until a
future state protocol replaces that presentation mapping.

The server accepts legacy first-frame `AgentEvent v1` publishers and negotiated
`publisher` connections using `client_hello`, `server_hello`, and
self-describing lifecycle, state, or interaction frames. Publishers using
`adapter_session_v1` explicitly register complete sessions and declare runtime
capabilities before publishing owned events. It limits frames to 1 MiB and
handles 16 connections concurrently by default. Negotiated state and
interaction subscribers and `control_command_v1` controllers are enabled. See
[`protocol/local-transport-v1.md`](../protocol/local-transport-v1.md).

## Phase 0 Quickstart

The prototype requires Python 3.11 or newer and has no runtime dependencies.

Start the four-slot bridge:

```bash
PYTHONPATH=bridge python3 -m deskhelm_bridge bridge
```

Use `--max-connections` to lower or raise the bounded connection limit.
The default subscriber limit is shared by state and interaction subscribers and
is half the connection limit. Each subscriber has an 8-frame output queue.
Override these with `--max-subscribers` and `--subscriber-queue-frames`.

Control routing retains at most 1024 idempotency entries and 1024 approval
records by default. Override these with `--control-idempotency-entries`,
`--control-idempotency-retention-ms`, and `--control-approval-records`.
`focus` is handled internally; other command kinds reject with
`handler_unavailable` until an Agent or Voice Gateway registers a bounded
non-blocking handler. Enabling `--agent-provider codex` installs bounded
`submit_prompt` and `interrupt` handlers. A dispatched result means the run was
accepted by the fixed-capacity gateway; completion arrives separately as an
interaction terminal event.

`--agent-provider codex-app-server` instead composes one long-lived external
runtime. It connects to a local Codex app-server Unix WebSocket, optionally
binds a separate `--codex-tui-proxy-socket` for the managed remote TUI, directly
attaches to one newly loaded manual remote-TUI thread, resumes known threads
after reconnect, and installs prompt, interrupt, approve, and reject handlers.
The owned-process gateway and long-lived runtime are mutually exclusive.

The dependency-free TUI Dashboard is an optional local client. It consumes the
state and interaction subscriptions, queries bounded complete session metadata,
and sends explicit focus or interrupt controls without owning Agent processes:

```bash
PYTHONPATH=bridge python3 -m deskhelm_bridge dashboard --socket "$SOCKET"
```

Use `j`/`k` to move, `f` to focus the selected complete session, `i` to send a
targeted interrupt, `a`/`r` to approve or reject one visible unexpired pending
request, `?` for help, and `q` to quit. Approval controls copy the exact
request metadata and remain subject to Bridge correlation and expiry checks.
Dashboard disconnect only closes its client connections. It does not persist
transcript content or attach to arbitrary terminals.

The loopback web client is the Codex-layout browser for the user bridge.
It does not create sessions, change focus when a thread is selected, or load
history from before the page connected:

```bash
PYTHONPATH=bridge python3 -m deskhelm_bridge web
```

Omit `--socket` to read `bridge.sock`. Every DeskHelm session attached there
appears on the page. An explicit `--socket` still selects one bridge,
including a private Alpha bridge. An empty path is rejected.

Open `http://127.0.0.1:8766`. `--allow-remote` is required before the process
binds anywhere other than loopback, because the page shows interaction content.
Model, reasoning, MCP, and skills open from the composer and do not sit in
the transcript. The page fetches that catalog when the selected session
changes, and again on refresh. Choosing a model or effort sends
`configure_session` for the next new turn and does not write Codex
configuration. The Codex adapter must be running before that catalog is
available. The same page shows Claude and Grok sessions started with
`deskhelm agent`. Each session carries its runtime name and declared
capabilities. Vendor-declared names appear under 声明 when that session has
any.

```bash
PYTHONPATH=bridge python3 -m deskhelm_bridge agent claude --socket "$SOCKET"
PYTHONPATH=bridge python3 -m deskhelm_bridge agent grok --socket "$SOCKET"
```

Run the command in the project directory. The prompt stays on the vendor
stdin. The attach frame does not carry the working directory. Codex stays on
`deskhelm codex --tui`.

Python composition can pass an isolated `VoiceGateway` to `run_bridge`. The
Bridge then registers targeted speech and PTT handlers and converts normalized
final transcripts into `submit_prompt` commands. Assistant response content,
Agent progress, and tool events remain silent. Completion, failure, approval,
and required-input events use fixed content-free attention notifications. The
CLI can now compose the provisional local candidates, but they remain disabled
by default.

The separate application-level audio diagnostics resolve PipeWire defaults or
manual stable node names without implying that Bridge voice is enabled:

```bash
PYTHONPATH=bridge:voice python3 -m deskhelm_bridge audio status --list
PYTHONPATH=bridge:voice python3 -m deskhelm_bridge audio test-input --seconds 2
PYTHONPATH=bridge:voice python3 -m deskhelm_bridge audio test-output
```

`status` is read-only. `test-input` is an explicit microphone action that keeps
PCM in memory only long enough to calculate signal metadata; `test-output`
plays a bounded low-volume tone. A missing manual target fails instead of
falling back to another device.

## Managed Codex

`deskhelm codex` starts Codex app-server for one working directory and registers
that one session. `deskhelm alpha` is a hidden alias for the same command.
By default it joins the user bridge at `bridge.sock` and
leaves that process running on exit. It detaches only its own runtime and
removes this directory's Codex sockets. Explicit `--socket` keeps a private
bridge and stops that bridge on exit. Local voice and PTT are explicit
additions. The command never stores local model paths in repository files:

```bash
deskhelm codex --check
deskhelm codex --tui
deskhelm codex --tui --voice
deskhelm codex --tui --yolo
```

For a source checkout, `tools/deskhelm-user-launcher` is linked once as the
user-level `deskhelm` command. It preserves the caller's current directory and
runs the managed Codex command from the synchronized root environment. The default command does
not receive a private `--socket`. A rebuild stops this directory's Codex
app-server only and does not signal the user bridge. Every real TUI
launch uses `tools/run-with-pty.py` for live terminal rendering and the bounded
two-press Ctrl-C guard. Core TUI startup does not use `project-secrets`.
`--voice` dynamically loads the required encrypted settings and adds streaming
redaction to the same relay. The launcher never syncs dependencies during
startup.

The optional `--voice` profile fixes Paraformer, prefers IndexTTS-2.5 when its
model directory and speaker reference are present, and otherwise selects Piper
(with explicit Kokoro compatibility), keeps advisory VAD disabled, and uses
the stable `/dev/input/deskhelm-ptt-kbd` source. Kokoro uses `zf_xiaobei` for
Chinese or mixed notifications and `af_heart` for English-only notifications.
The PTT listener reads that input source without an exclusive grab, so the
desktop keyboard keeps working. A flock admits one listener; a second
voice-enabled Alpha using the same device fails closed instead of dispatching
the same key event to another session. Right Alt still reaches the compositor. Each accepted PTT press resolves the
Bridge's current explicit focus; with no focus, capture is not started.
Core
`--check` validates the working directory, socket ownership, and Codex version.
`--check --voice` additionally validates model files, both uv environments, the
PTT device, and PipeWire selection without opening audio or loading model
weights. Runtime startup
prints the exact manual remote-TUI command, including the selected read-only or
workspace-write sandbox and `on-request` approval policy. `--voice-source` and
`--voice-sink` override `DESKHELM_ALPHA_VOICE_SOURCE` and
`DESKHELM_ALPHA_VOICE_SINK`; either form uses the same strict stable-name
selection and never falls back when a configured device is unavailable. Use
`--voice-pw-cat-command-prefix` or `--codex-sandbox
workspace-write` only as explicit local overrides. `--yolo` is a separate,
explicit remote-TUI mode that passes Codex's full-access, no-approval flag and
therefore bypasses the sandbox and approval flow; it is intentionally never the
default. `--voice-turn-timing` remains opt-in.

When a client changes the visible session, it must update Bridge focus
explicitly. The CLI helper is suitable for a terminal or desktop client:

```bash
deskhelm focus --socket "$SOCKET" \
  --agent-id codex --session-id session-42 --project-id deskhelm
```

Run the physical listener with `deskhelm ptt --focused` so each press reads the
current focus. A missing focus or a target that changed between query and route
is rejected without starting capture. Approval and attention notifications do
not change focus. On the default user bridge, focus is one value shared by
every attached session. Explicit `--socket` keeps focus local to that private
bridge.

`--tui` starts the printed remote command as a foreground child in the current
interactive terminal. The launcher relay preserves terminal input, output,
resizing, and child exit status. Its bounded guard maps the first raw or
Kitty-protocol Ctrl-C to clear-input and forwards only a second press within the
confirmation window; timeout or other input resets it. Alpha monitors the
app-server, plus PTT only when `--voice` is selected. On exit it stops this
directory's Codex app-server. Shared mode does not stop the user bridge.
Explicit `--socket` also stops that private bridge. Repeated interrupts cannot abort
teardown. The relay exits when its primary child exits even if another process
still holds the PTY open. The two-terminal mode remains available when `--tui`
is omitted.

The remote TUI must connect to the Alpha-managed socket printed at startup. A
plain independently launched Codex TUI is not attachable. DeskHelm discovers
and directly attaches to one loaded thread, including a new thread with no
rollout yet, while bounded resume retries separately establish the event
subscription needed to observe manually entered turns. One positively
identified ephemeral internal thread is ignored; multiple user threads require
terminal cleanup or a future explicit selector. The bounded exact approval and
rejection phrase sets can decide one pending approval because the printed
socket is a bounded proxy to a distinct app-server upstream socket.
Keyboard-first and voice-first approval races resolve once on the original
upstream channel. Permission approvals grant only the requested subset for the
current turn. Persistent approval, secret input, and multi-question input
remain terminal-only.

Each Alpha process registers one session. Several default Alphas share the
user bridge, so one page lists every directory. `--agent-workdir` applies to
that one session. The text-only `bridge --agent-project` gateway stays a
separate path and cannot be combined with `--agent-runtime-directory`. The
first `alpha --voice` must be the process that starts the user bridge. A
bridge already running without voice is left as it is; stop it before starting
voice.

Start the provisional local voice composition only from an environment that
contains the optional FunASR/PyTorch and selected Piper/ONNX or Kokoro runtime.
Model artifacts
must remain under ignored storage or another external directory:

```bash
PYTHONPATH=bridge:voice:adapters/codex python3 -m deskhelm_bridge bridge --plain \
  --agent-provider codex \
  --agent-workdir "$PWD" \
  --voice-provider local \
  --voice-asr-provider paraformer \
  --voice-asr-model-directory /ignored/paraformer-snapshot \
  --voice-tts-provider piper \
  --voice-tts-model /ignored/piper/voice.onnx \
  --voice-tts-config /ignored/piper/voice.onnx.json \
  --voice-tts-resource-directory /ignored/piper/resources \
  --voice-vad-provider webrtc \
  --voice-turn-timing
```

`--voice-turn-timing` is explicit and requires both Voice and Agent providers.
It writes one target-scoped numeric JSON record to stderr for each
Voice-originated Agent turn. Records contain only terminal status and monotonic
software-stage durations from accepted PTT release through ASR, Agent, TTS, and
PipeWire handoff; they contain no transcript, prompt, assistant text, tool
output, turn identifier, PCM, or acoustic speaker-latency claim. The record
separates notification readiness, speech/TTS start, complete TTS audio
readiness, and PipeWire first write so warm-path Agent and TTS costs can be
compared directly.

When Bridge runs in a Distrobox whose `pw-cat` lacks compatible raw-PCM
options, add `--voice-pw-cat-command-prefix "host-spawn -no-pty pw-cat"` to use
the host PipeWire client. Native host execution keeps the default `pw-cat`.

Startup resolves the configured PipeWire devices and checks required artifact
files without loading models or opening audio. The first ASR/TTS request loads
its runtime lazily. PipeWire audio reaches Voice Gateway as contiguous
frame-positioned chunks and is aggregated under fixed bounds until PTT release.
ASR waits at most 30 seconds by default; tune the bounded deadline with
`--voice-max-asr-seconds` (1-120 seconds). A deadline emits
`voice_asr_timeout`, and the next explicit PTT request is the retry boundary.
`--voice-vad-provider webrtc` optionally publishes advisory, frame-positioned
input activity. It is disabled by default, cannot stop or trim capture, and
falls back to the unchanged PTT/final-ASR path on any VAD failure. Partial
transcript publication is not integrated.
If `--agent-provider codex` is omitted, speech controls remain available but a
completed PTT transcript cannot be dispatched and reports the recoverable
`voice_prompt_failed` category because no prompt handler is registered.

Run the Bridge with the text-only Codex provider:

```bash
PYTHONPATH=bridge:adapters/codex python3 -m deskhelm_bridge bridge --plain \
  --agent-provider codex \
  --agent-workdir "$PWD" \
  --agent-max-active-runs 4 \
  --agent-session-records 64 \
  --agent-run-timeout-seconds 300
```

The provider is opt-in and defaults to `--codex-sandbox read-only`. Prompts are
sent through stdin, Codex stderr is not logged, JSONL records are bounded to
1 MiB, and owned processes are terminated on interruption or timeout.

To route sessions from multiple repositories, replace `--agent-workdir` with
bounded operator-configured mappings:

```bash
PYTHONPATH=bridge:adapters/codex python3 -m deskhelm_bridge bridge --plain \
  --agent-provider codex \
  --agent-project deskhelm=/path/to/deskhelm \
  --agent-project another-project=/path/to/another-project \
  --agent-project-records 8
```

The mapping key is the session's exact opaque `project_id`, not a filesystem
path derived from protocol data. Once any mapping is present, the mode is
strict: unmapped projects reject before an Agent run or provider session record
is created. Mappings are immutable until Bridge restart, validate directories
at startup, default to a 64-project bound, and cannot be combined with
`--agent-workdir`.

Python integrations can use `deskhelm_bridge.send_negotiated_event` for a
single negotiated state event while the CLI remains a legacy compatibility
client.

In another terminal, run the demo sequence:

```bash
PYTHONPATH=bridge python3 -m deskhelm_bridge simulate
```

Send one event manually:

```bash
PYTHONPATH=bridge python3 -m deskhelm_bridge emit \
  --agent-id project-a:codex:1 \
  --slot 0 \
  --state waiting_approval \
  --label backend
```

For an editable CLI installation:

```bash
uv sync
uv run deskhelm bridge
```

The default socket is `$XDG_RUNTIME_DIR/deskhelm/bridge.sock`, or
`/tmp/deskhelm-<uid>/bridge.sock` when `XDG_RUNTIME_DIR` is unavailable.

The pre-release `agent-io` CLI and `python -m agent_io_bridge` remain temporary
compatibility aliases. They use the DeskHelm socket path.

## Codex Hook

Install the editable CLI, start `deskhelm bridge`, then adapt
[`adapters/codex/hooks.example.json`](../adapters/codex/hooks.example.json) into
`~/.codex/hooks.json`. Each command receives a Codex hook payload on standard
input and forwards its lifecycle state to the configured slot.

## Tests

```bash
PYTHONPATH=bridge uv run --project voice/runtime --locked --no-sync python -m \
  unittest discover -s tests -v
```

# Bridge

The standard-library packages behind the `deskhelm` command: the protocol v2
Bridge, the session-host runtime, the shared client runtime, and the
surfaces. They need Python 3.11 or newer and no runtime dependency; only the
right-Alt listener needs the optional `ptt` extra (`evdev`). Agent adapters
live in [`adapters/`](../adapters/) and the voice library in
[`voice/`](../voice/).

| Package | Role |
|---|---|
| `deskhelm_protocol` | Protocol v2 models, NDJSON framing, and runtime paths |
| `deskhelm_core` | The Bridge: a single-threaded actor without I/O behind a Unix socket server |
| `deskhelm_host` | The session-host runtime: Bridge link and start, vendor process helpers, and `simulate` |
| `deskhelm_client` | The client runtime every surface shares, with one copy of result wording and the transcript fold |
| `deskhelm_web` | The loopback web page |
| `deskhelm_dashboard` | The terminal dashboard |
| `deskhelm_voice_host` | The voice host and its right-Alt listener |
| `deskhelm_cli` | The `deskhelm` command, including `deskhelm audio` |

[`protocol/deskhelm-v2.md`](../protocol/deskhelm-v2.md) is the wire contract,
and ADR 0043 records the architecture.

## Bridge

The Bridge owns session facts, focus, command routing, idempotency receipts,
and approval tickets. It runs no agent process and imports no adapter or voice
code. Its default socket is `$XDG_RUNTIME_DIR/deskhelm/bridge.sock`, or
`/tmp/deskhelm-<uid>/bridge.sock` without `XDG_RUNTIME_DIR`.

A host starts the Bridge before its first session opens, with output in
`bridge.log` in the same directory, and leaves it running on exit. A socket
held by anything other than a v2 Bridge is named and refused. To run the
Bridge in the foreground instead:

```bash
deskhelm bridge [--socket PATH]
```

Restarting the Bridge does not stop agents. Each host keeps its vendor
process, reconnects with jittered backoff, and reopens its session with the
requests that are still open.

## Session Hosts

Each session runs in its own host process, started in the project directory.
The host's Bridge connection is the session lease.

```bash
deskhelm codex [--voice [--default-source]] [--yolo] [--check]
deskhelm agent claude
deskhelm agent grok
deskhelm simulate
```

`deskhelm codex` runs the managed Codex TUI in this terminal; see
[`adapters/codex/README.md`](../adapters/codex/README.md). `deskhelm agent`
keeps prompts on the vendor's stdin. `deskhelm simulate` opens a demo
session that needs no agent: each prompt gets a scripted turn, and a prompt
containing `approve` or `批准` first asks for an approval.

## Surfaces

Surfaces connect as clients through `deskhelm_client`: one connection per
process, a console resumed from its cursor on the same Bridge instance and
reset on a new one, and commands that are never retried. Approvals are
decided by Bridge approval id, so a reconnecting surface still sees them.

```bash
deskhelm web [--port 8766] [--allow-remote]
deskhelm dashboard
deskhelm sessions
deskhelm focus --agent-id ID --session-id ID --project-id ID
```

Every command takes `--socket` to name another Bridge.

`deskhelm web` serves on `127.0.0.1:8766` and prints a `?token=` link once.
Opening it sets an HttpOnly, SameSite=Strict cookie for 30 days and redirects
to a token-free address. The token lives with mode 0600 in the runtime
directory. A loopback bind accepts only `127.0.0.1` and `localhost` Host
headers, and a POST needs a JSON body and, when the browser sends one, a
same-origin Origin. `--allow-remote` is required to bind elsewhere. A page
opened later receives the Bridge's bounded recent window and the pending
approvals, which is not durable history. Browsing a session does not focus
it.

The page lists sessions by project with their state, a preview, pending
approvals, and a mark for activity you have not looked at; the tab title
counts the approvals waiting anywhere. A reply renders Markdown; a tool row shows its command or path
and opens to its output; an approval waits inline with a countdown; the stop
button interrupts a working turn; and a details panel shows the runtime,
capabilities, model, MCP servers, and skills. Enter sends and Shift+Enter
adds a line. The server builds each transcript with
`deskhelm_client.transcript` and sends a snapshot, then revisioned patches
(ADR 0044); the page is plain ES modules in `deskhelm_web/assets/js/` with no
build step. `tools/check-web-page.py` checks it in Chrome.

The dashboard uses `j`/`k` to move, `f` to focus, `i` to interrupt, `a`/`r`
to approve or reject, and `q` to quit. Focus decides which session voice
speaks to.

## Voice Host

```bash
deskhelm voice [--default-source] [--check]
deskhelm codex --voice [--default-source]
```

The voice host runs in the [`voice/runtime`](../voice/runtime/) environment
through `uv` and attaches at the voice tier, which may only submit prompts and
answer approvals. Holding right Alt speaks to the focused session. An
approval phrase answers that session's single answerable approval and never
becomes a prompt; with no approval or several, it is refused. Spoken `取消`
discards the utterance. Notices come from console transitions, so the voice
tier never sees content.

Model and audio settings come from `DESKHELM_ALPHA_VOICE_SOURCE`,
`DESKHELM_ALPHA_VOICE_SINK`, `DESKHELM_ALPHA_ASR_MODEL_DIRECTORY`, and
`DESKHELM_ALPHA_TTS_DIRECTORY`; the source-checkout launcher injects them
through `project-secrets`. A configured device that is not connected fails
with a message that names the setting, not the device; `--default-source` or
`--default-sink` uses the computer's current default instead. `--check`
validates models, audio, and the key without opening audio.

The right-Alt listener runs as a child with the root environment's Python. It
reads `/dev/input/deskhelm-ptt-kbd` without a grab, and a lock on `ptt.lock`
admits one listener.

## Audio Diagnostics

```bash
deskhelm audio status --list
deskhelm audio test-input --seconds 2
deskhelm audio test-output
```

`status` resolves PipeWire devices without opening audio. `test-input` keeps
PCM in memory only long enough to measure it, and `test-output` plays a short
low-volume tone. A configured device that is missing fails instead of falling
back.

## Tests

```bash
uv run --locked python -m unittest tests.test_core tests.test_core_server \
  tests.test_host_link tests.test_drivers tests.test_codex_host \
  tests.test_replay_transcripts tests.test_client tests.test_web_surface \
  tests.test_dashboard_v2 tests.test_voice_host tests.test_cli_switch \
  tests.test_simulate tests.test_user_launcher -v
```

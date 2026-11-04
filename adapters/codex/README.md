# Codex Adapter

`deskhelm codex` runs one Codex session host in the current directory. The
host owns this directory's `codex app-server`, a TUI approval proxy, and a PTY
relay that runs `codex --remote` in this terminal. `CodexSession` in
`driver.py` wraps `CodexAppServerAdapter` and turns its events into protocol
v2 facts.

## Running

```bash
deskhelm codex --check [--voice [--default-source]]
deskhelm codex
deskhelm codex --voice [--default-source]
deskhelm codex --yolo
```

- The TUI runs in the read-only sandbox and asks for approval on request.
  `--codex-sandbox workspace-write` lets it write. `--yolo` passes Codex's
  full-access, no-approval flag, which bypasses the sandbox and every
  approval; use it only when that access is intended.
- The relay turns a first raw or Kitty-protocol Ctrl-C into clear-input and
  forwards Ctrl-C only on a second press within a short window.
- A second host in the same directory is refused, and stale directory sockets
  are removed. The sockets are `codex-<digest>-app.sock` and
  `codex-<digest>-tui.sock` in the runtime directory, never derived from the
  Bridge socket. The host logs to `codex-<digest>-host.log`.
- On exit the host closes the session and stops this directory's app-server;
  the Bridge keeps running.
- `--voice` preflights voice before the TUI takes the terminal, focuses this
  session, runs the voice host as a child that logs to
  `codex-<digest>-voice.log`, and stops it on exit.
- `--check` finds Codex and, with `--voice`, checks the voice models, the
  PipeWire selection, and the right-Alt key without opening audio.

The relay is `tools/run-with-pty.py`, so the host runs from a source checkout.

## App-Server Behavior

Observed with Codex CLI 0.166.0:

- The adapter attaches directly to the one loaded thread, so a new empty TUI
  is targetable before its first rollout exists. It then retries bounded
  `thread/resume` calls until its own connection is subscribed. Only a resume
  result, a server request, or turn events on that connection prove the
  subscription; `turn/start` and `thread/status/changed` reach every
  connection and do not.
- Before the first user message, `thread/resume` reports that no rollout
  exists. That is retried only for a discovered thread; an explicit thread
  failure stays fail-closed.
- One positively identified ephemeral internal thread is ignored. Several user
  threads need an explicit selector.
- The `/resume` picker may use one extra short-lived TUI connection. After it
  confirms a thread, the adapter attaches to that thread.
- A subscribed adapter also receives each approval request the TUI receives;
  with the proxy it ignores that copy.
- A user message arrives as `item/started` and `item/completed` and counts
  once, at `item/completed`. A prompt DeskHelm accepted is published once, and
  the matching vendor echo is not published again (ADR 0042).
- A steer that fails because no turn is active starts one turn with the same
  prompt (ADR 0041). Any other rejected steer becomes a system notice and does
  not end the turn.

## Approvals

Command, file, and permission approvals travel on the TUI's original JSON-RPC
response channel through the proxy. A keyboard answer clears DeskHelm's
record; a DeskHelm answer suppresses the later TUI response for that request.
Proxy disconnect, expiry, or capacity fails closed without inferring a
decision, and excess approvals stay answerable from the keyboard. A permission
approval grants only the requested subset for the current turn and is never
persisted. Approval summaries are fixed; the command, the paths of the item
an approval names, or Codex's reason travel as operator-only detail
(ADR 0045).

The `waitingOnApproval` thread status alone is notification-only attention
with no request id; the proxy supplies the real request.

## Questions

Codex asks in two ways, and the page answers both (ADR 0046). An
`item/tool/requestUserInput` request waits for its answer, which DeskHelm
sends on its JSON-RPC channel by question id. A request with a secret
question stays in the TUI, and while one question is open, a prompt answers
it.

The `request_user_input_async` tool does not wait. Its `agentMessage` carries
`questions`, and DeskHelm shows them as a card in place of that message,
answerable for an hour, also after its turn. A DeskHelm answer is the user
message the TUI would send: a `<send_user_message_question_reply>` JSON list
that names each question as `[tool, call id, index]`, steering the active
turn or starting one. Codex CLI 0.166.0's TUI then takes the question off its
queue and shows the answer as its own. An answer chosen in the TUI
(`shift+←`, then Enter) resolves the card with that answer, and the wrapper
is never shown.

## Catalog and Configuration

The host pushes its catalog (models, MCP status, and skill names) when its
thread attaches, after a turn at most once a minute, and after a model choice.
The Bridge caches it, so a catalog query never waits for the host. A
`configure` command stores a model and reasoning effort for the next
`turn/start`, but only after a catalog read from the last 110 seconds;
otherwise it is undelivered and a fresh read starts. It never writes Codex
configuration.

## Limits and Evidence

The app-server WebSocket transport is experimental, so this integration is
Alpha-only. DeskHelm does not inject terminal keystrokes and cannot attach to
an unrelated plain `codex` process.

Schema-derived synthetic JSON-RPC fixtures for locally inspected Codex CLI
0.149.0 live under `tests/fixtures/adapters/codex-app-server-v2/`, and
synthetic replay transcripts under `tests/fixtures/replay/codex/`. The
protocol follows the official Codex app-server contract:
<https://learn.chatgpt.com/docs/app-server>.

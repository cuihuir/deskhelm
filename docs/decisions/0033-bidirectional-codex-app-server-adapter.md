# ADR 0033: Bidirectional Codex App-Server Adapter

Date: 2026-08-27

Status: Accepted for Alpha

## Context

DeskHelm's existing Codex provider owns bounded `codex exec --json` processes.
That is appropriate for non-interactive turns but cannot observe or control a
terminal session that a user drives independently. Terminal keystroke injection
would couple the integration to one TUI rendering, expose private screen
content, and make approval correlation unsafe.

Codex app-server provides the supported bidirectional JSON-RPC surface for rich
clients. It can resume a thread, stream turn and item events, request command or
file approvals, request structured user input, start or steer turns, and
interrupt an active turn. Its Unix transport is WebSocket over a local Unix
socket. The app-server command and WebSocket transport are experimental and are
not supported for production workloads.

Official protocol reference:
<https://learn.chatgpt.com/docs/app-server>.

## Decision

DeskHelm adds an Alpha-only, long-lived Codex app-server adapter alongside the
existing `codex exec` provider. The Alpha supervises one local app-server and
prints the `codex --remote unix://...` command that a user can start manually.
An optional `--tui` convenience mode launches that exact command as a
foreground child in the current interactive terminal and treats TUI exit as
the Alpha shutdown boundary.
An explicit Alpha `--yolo` option passes Codex's corresponding full-access,
no-approval flag only to that remote TUI. It is disabled by default and does
not alter the bounded Bridge or proxy; because Codex does not request
approvals in this mode, its normal keyboard and voice approval paths have no
request to route.
"Manual Codex session" in this integration means a manually operated TUI that
connects to this DeskHelm-managed app-server. DeskHelm cannot attach safely to
an unrelated plain `codex` process that was started without `--remote`.

The managed TUI uses a bounded local protocol proxy so DeskHelm observes the
server-initiated approval request on the same connection that owns its JSON-RPC
response:

```text
Codex TUI
  -> DeskHelm TUI proxy socket
     -> Codex app-server upstream socket

CodexAppServerAdapter observer/control connection
  -> Codex app-server upstream socket
```

The proxy forwards ordinary JSON messages unchanged. It retains only bounded
approval correlation metadata and never copies command text, file changes,
reasons, or other request content into DeskHelm events or logs.

The managed TUI is the hardware-independent Alpha core. The source-checkout
launcher does not load voice settings for this path, but every real `--tui`
launch uses a bounded PTY relay to preserve interactive rendering and enforce a
two-press Ctrl-C guard. The first raw or Kitty-protocol Ctrl-C becomes
clear-input; only a second press within the confirmation window reaches Codex.
The optional `--voice --tui` path adds boundary-safe redaction required by the
encrypted-settings wrapper.

Alpha owns the app-server process group and gives it closed standard input plus
discarded standard output and error; its upstream Unix socket is the only
app-server protocol surface. Bridge owns the separate TUI proxy socket. Alpha
waits for the upstream before Bridge startup and for the proxy before TUI
startup. It installs explicit termination handlers, ignores repeated
termination signals once cleanup begins, and attempts every cleanup step
independently. TUI exit or supervisor termination restores terminal ownership,
releases the registered session, stops Bridge and app-server, and removes the
Bridge, proxy, and upstream sockets. The optional PTY relay reaps its primary
child after a bounded output drain even if a descendant still retains the PTY
slave.

The adapter uses a dependency-free, bounded Unix WebSocket client. It performs
the required `initialize`/`initialized` handshake, discovers at most one loaded
user thread, and attaches to that already-loaded thread directly. This avoids
requiring a rollout before the remote TUI's first turn. Attachment and event
subscription are separate states: a discovered empty thread is immediately
targetable, while bounded `thread/resume` retries continue until the adapter
connection is subscribed to manually started turn and item events. A
DeskHelm-originated `turn/start` also subscribes that connection. Only the
specific missing-rollout error is retriable for a discovered thread;
explicitly configured thread failures remain fail-closed. Bounded
`thread/read` metadata checks may exclude one positively identified ephemeral
internal thread, while multiple non-ephemeral threads remain ambiguous until a
later explicit selection interface exists. Frames, queues, requests, pending
approvals, pending user-input requests, timeouts, and reconnect backoff remain
bounded.

The TUI proxy is also dependency-free and bounded. It accepts one primary local
TUI connection plus at most one short-lived auxiliary connection used by the
Codex `/resume` session picker, opening one separate upstream WebSocket for
each. The auxiliary connection forwards normal app-server traffic but never
replaces the primary approval channel or participates in voice approval routing.
After its `thread/resume` response confirms a thread identity, the proxy tells
the separate adapter observer to resume the same thread before later voice
controls are sent. This keeps the visible TUI thread and DeskHelm's voice target
aligned without copying prompt or approval content between connections.
Frames, queues, approval records, connection timeouts, and cleanup remain
bounded. A full forwarding queue pauses upstream reads with bounded stop-aware
polling so the local socket applies backpressure; transient saturation never
drops, reorders, or disconnects an otherwise healthy streamed turn. Voice
decisions use a separate bounded priority queue so they do not compete with
streamed notifications. A command or file approval request on the primary TUI
is forwarded before the adapter is notified with a proxy-local correlation
token. A voice decision sends the response on that original upstream channel.
A later duplicate TUI response for the same JSON-RPC request is suppressed even
when it arrives after
`serverRequest/resolved`. The suppression record is bounded, retained only
until the late response or a fixed post-resolution expiry, and reports one
resolution callback so the next approval in the same turn can notify again. If
the TUI answers first, its response is forwarded and the adapter clears the
corresponding DeskHelm approval. Disconnect and expiry clear proxy-owned records;
`serverRequest/resolved` acknowledges voice-first delivery and shortens the
remaining duplicate-suppression window. Capacity exhaustion leaves additional
requests keyboard-actionable in the TUI rather than guessing or automatically
deciding them.

Bridge gains a generic long-lived Agent runtime composition boundary. A runtime
publishes normalized `InteractionEvent` values and registers explicit control
handlers, while provider-specific JSON-RPC and process behavior stays in
`adapters/codex/`. The runtime and the owned-process Agent Gateway are mutually
exclusive in one Bridge instance.

The adapter maps `turn/completed` to one normalized terminal event. Command,
file, and `item/permissions/requestApproval` server requests become
fixed-summary `approval_request` events; command text, file changes, reasons,
working directories, and other private request content are not copied into the
summary or spoken. A granted permission response contains only the requested
permission subset and is scoped to the current turn; rejection returns an empty
permission set. Structured input requests become fixed-prompt
`user_input_required` events. A single non-secret question may be answered by
the next prompt control; multi-question and secret input remains in the Codex
TUI for the initial implementation.

The adapter also observes `thread/status/changed` and the status returned by
`thread/resume`. Entering `active` with `waitingOnApproval` produces an active,
notification-only `attention` event, and leaving that state clears it. This is
a fallback notice only. In the managed TUI topology, the proxy observes the
real server request and creates the correlated `approval_request`. Status alone
never becomes actionable, so `批准` and `拒绝` remain ordinary prompts unless
DeskHelm owns one correlated, non-expired approval request. Voice deduplicates
the status notice and actionable request for the same target while the
attention state is active.

Approval decisions retain the existing DeskHelm safety contract:

- Voice never infers a decision from arbitrary text. Only the bounded exact
  normalized approval phrases `批准`, `确认`, `同意`, `允许`, and `可以`, or
  rejection phrases `拒绝`, `不同意`, `不允许`, and `取消`, can become approval
  controls.
- Voice decides only when exactly one non-expired approval is pending for the
  complete target session. Multiple pending approvals require the Codex TUI.
- The control copies the exact pending request ID, fixed summary, and expiry.
- Command and file approval use app-server `accept` or `decline`. Permission
  approval grants only the requested subset for the current turn; rejection
  grants none. `acceptForSession`, persistent permission scope, policy
  amendments, and `cancel` are not used by voice.
- A dispatch attempt consumes the DeskHelm pending approval even if downstream
  delivery later fails, because the app-server decision may already apply.
- When no approval is pending, the same phrases remain ordinary prompt text.

Project identity remains operator-owned. The adapter receives a complete
DeskHelm session target from composition and never derives `project_id` or a
working directory from app-server protocol fields.

## Consequences

- DeskHelm can observe completion and human-attention events from a manually
  operated remote Codex TUI and continue the same thread through PTT controls.
- Existing `codex exec` behavior and compatibility remain available.
- Real Codex turns may emit notification bursts larger than the in-process
  forwarding queue. The proxy keeps memory bounded and preserves the connection
  by propagating backpressure to the app-server socket.
- Consecutive approvals in one turn are independent. A voice-first response
  cannot leak the TUI's late duplicate into app-server or leave the voice
  attention state active for the next approval.
- The Alpha owns an additional app-server child, while Bridge owns the TUI
  proxy. The profile uses distinct Bridge, proxy-frontend, and app-server
  upstream sockets. The user may start the TUI with the printed `codex
  --remote` command or let `--tui` launch it in the current terminal.
- Core TUI operation and cleanup do not depend on voice models, audio devices,
  PTT hardware, or `project-secrets`. The small PTY relay is part of the core
  terminal-safety contract.
- A plain already-running Codex TUI, multiple simultaneous user threads,
  multi-question input, secret input, and persistent voice approvals remain out
  of scope. Positively identified ephemeral internal threads do not make a
  single user thread ambiguous.
- Production adoption requires a later review after Codex declares the
  app-server WebSocket transport supported and stable.

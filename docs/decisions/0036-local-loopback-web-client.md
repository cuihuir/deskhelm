# ADR 0036: Local Loopback Web Client

Date: 2026-10-20

Status: Accepted

## Context

Operators asked for a Codex-style page: a project sidebar, a live transcript,
a composer, and a review rail. DeskHelm already exposes that information
through the Bridge Unix socket. A browser cannot speak that socket, and the
page must not become another agent runtime, transcript store, or remote API.

The managed Codex app-server publishes approvals, attention, user-input
notices, and terminal task events. Completed thread items are the text and
tool activity a transcript needs. Token deltas are already opted out so a
stream cannot fill the subscriber queue.

## Decision

Add a replaceable loopback web client inside the Bridge process. It is a
client adapter in front of the existing socket, not a new protocol.

- The process holds one `state_subscription_v1` connection and one
  `interaction_subscription_v1` connection. Browsers receive a bounded
  server-sent-events fan-out. Short controller connections send
  `session_query` and `control_command_v1`.
- An explicit `--socket` selects one Bridge. An empty path is rejected.
  When the flag is omitted, the client reads the user bridge from ADR 0038.
- Subscription sockets are read without a buffered reader. `select` only
  sees bytes still in the kernel, so every complete NDJSON frame from one
  `recv` is applied before the loop waits again. A burst of item or approval
  frames must not stall behind the first line.
- At most 8 browser streams are open. Each stream queues 32 frames. A full
  queue drops that browser and requires a fresh snapshot.
- The page keeps at most 120 interaction events for each session and 480
  events overall. One event larger than 48,000 bytes is dropped with a fixed
  notice. There is no replay. The page says that earlier thread history stays
  in Codex.
- The page ignores the Bridge's initial per-slot offline placeholder unless
  that event's `agent_id` matches the session. A connected Alpha session with
  no agent-activity event shows its lifecycle, currently “在线”.
- Selecting a thread is local to the browser. It does not send `focus`.
  Focus, interrupt, `submit_prompt`, approve, and reject are explicit
  controls. Approval and rejection copy the stored request id, summary, and
  expiry. Status-derived attention is not an actionable approval.
  `user_input_required` stays in the Codex TUI. The composer sends a normal
  `submit_prompt` and says so.
- The default bind is `127.0.0.1`. `localhost` normalizes to that address.
  Any other host requires `--allow-remote`, because the page shows private
  interaction content.
- The Codex app-server adapter publishes `item/started` and `item/completed`
  for the one attached thread. Public item examples may omit `threadId`; an
  item without one stays on that attached thread. Deltas stay opted out.
  Reasoning, working directories, image bytes, local image paths, and tool
  arguments are omitted. Item text is clipped at 16,384 characters. Approval
  and user-input notifications stay private-content-free.
- The client is dependency-free. Static files are same-origin HTML, CSS, and
  JavaScript served with a restrictive content security policy.

## Consequences

The TUI Dashboard's bounded detail can now include the same message and tool
text, because it already renders interaction text. That text stays on the
local machine and is not written to ordinary logs. Voice still does not speak
assistant text.

Activity from before the page connects is invisible. Assistant and tool text
appears when an item starts or completes, not as a token stream. The page
does not create sessions.

## Alternatives considered

Embedding the Codex TUI, reading Codex transcript files, or opening a general
remote HTTP API were rejected. Those choices would either own agent history
or expose interaction content beyond the local operator.

# ADR 0005: Use One Negotiated Local Socket

- Status: Accepted
- Date: 2026-08-17

## Context

Phase 0 accepts newline-delimited `AgentEvent v1` objects on one Unix stream
socket. Rich interaction events, control commands, snapshots, and live
subscriptions require bidirectional and long-lived connections. Adding a socket
per message plane would duplicate discovery, permissions, lifecycle, and error
handling, while allowing a subscriber on the current sequential server would
block publishers.

The protocol must preserve current one-way clients while providing an explicit
upgrade path. It must also bound untrusted local input and avoid implying event
history or replay guarantees that do not exist.

## Decision

Use one local Unix stream socket at the DeskHelm Bridge endpoint for state
publishers, interaction publishers, subscribers, and controllers.

New clients start with a `client_hello` frame that declares:

- `client_id`
- role: `publisher`, `subscriber`, or `controller`
- supported protocol versions
- requested capabilities

The Bridge replies with `server_hello` containing the selected version,
accepted capabilities, a process-local stream identifier, and limits.

After negotiation, each newline-delimited JSON frame is self-describing through
`message_type`. Roles restrict which messages a connection may send. A
connection has one role for its lifetime.

Legacy compatibility is limited to a connection whose first frame matches
`AgentEvent v1`. Such a connection is treated as a legacy state publisher and
does not gain subscription or control capabilities.

Use these transport constraints:

- UTF-8 newline-delimited JSON, one object per frame
- maximum encoded frame size: 1 MiB
- bounded per-connection input and output queues
- ordered delivery per connection
- no implicit retries for control commands
- local socket permissions restricted to the owning user by default

Subscribers receive a requested current snapshot followed by live events.
Version 1 does not persist or replay event history. After disconnect or a
detected sequence gap, a subscriber reconnects and requests a new snapshot.

Adapter-provided interaction events contain a source-local monotonic `sequence`
for ordering within one session. A future Bridge implementation may add a
process-local stream sequence for gap detection; it must not be presented as a
durable global event offset.

The current server must adopt bounded concurrent connection handling before
subscription connections are enabled.

## Consequences

- All local clients share one discovery and permission boundary.
- New message planes do not require more socket files.
- Legacy Phase 0 emitters remain compatible.
- The server implementation must distinguish negotiated and legacy first
  frames, enforce roles, and isolate slow subscribers.
- Restart and reconnect recovery depends on snapshots until persistence is
  explicitly designed.

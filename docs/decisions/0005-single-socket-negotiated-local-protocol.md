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

State subscribers receive a current snapshot followed by live events.
Interaction subscribers receive a sequence-zero start marker followed only by
new live events because rich interaction has no meaningful current snapshot.
Version 1 does not persist or replay event history. After disconnect or a
detected state sequence gap, a state subscriber reconnects and requests a new
snapshot; missed rich interaction remains unavailable.

Adapter-provided interaction events contain a source-local monotonic `sequence`
for ordering within one session. A future Bridge implementation may add a
process-local stream sequence for gap detection; it must not be presented as a
durable global event offset.

The server must use bounded concurrent connection handling before subscription
connections are enabled.

## Consequences

- All local clients share one discovery and permission boundary.
- New message planes do not require more socket files.
- Legacy Phase 0 emitters remain compatible.
- The server implementation must distinguish negotiated and legacy first
  frames, enforce roles, and isolate slow subscribers.
- State recovery depends on snapshots until persistence is explicitly designed;
  missed rich interaction remains unavailable.

## Implementation Status

Bounded concurrent connection handling, frame-size enforcement, legacy
first-frame detection, the negotiated `publisher` role, and the negotiated
`subscriber` role are implemented. Publishers expose `agent_event_v1` and
`interaction_event_v1`, including both on one adapter connection. Subscribers
select exactly one of `state_subscription_v1` and
`interaction_subscription_v1` per connection.

Subscriber registration and snapshot capture are atomic. Live updates use a
bounded non-blocking queue, and slow subscribers are disconnected and recover
through a new snapshot. Subscriber capacity is separately bounded below the
total connection limit so long-lived readers cannot consume every worker.
The default limits are 8 subscribers, 8 queued frames per subscriber, and a
two-second first-frame deadline plus a two-second subscriber write deadline
within the 16-connection server limit.

Interaction-event fan-out uses the same bounded subscriber capacity, queue
size, write deadline, read-only enforcement, and slow-consumer isolation as
state subscriptions. It retains no rich event history and never updates the
state projection. The `controller` role remains pending.

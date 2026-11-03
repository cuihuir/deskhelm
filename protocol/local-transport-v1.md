# Local Transport v1

Status: State, interaction, and controller roles implemented

## Framing and Limits

DeskHelm uses one owning-user Unix stream socket. Each frame is one UTF-8 JSON
object followed by `LF`. A trailing `CR` before `LF` is accepted. The JSON
object, excluding the line ending, must not exceed 1 MiB.

The Bridge handles at most the configured number of concurrent connections
(16 by default). Each publisher is read and processed synchronously, so it has
no unbounded application input queue. Subscribers use bounded output queues
and a separate subscriber limit so they cannot occupy every connection worker.
Every connection must provide its complete first frame within two seconds.

## Negotiation

New clients send `client_hello` as their first frame:

```json
{
  "protocol_version": 1,
  "message_type": "client_hello",
  "client_id": "codex-hook-1",
  "role": "publisher",
  "supported_versions": [1],
  "capabilities": ["agent_event_v1"]
}
```

The Bridge accepts publishers with `adapter_session_v1`, `agent_event_v1`,
`interaction_event_v1`, or a valid combination. A subscriber requests exactly
one of `state_subscription_v1` and `interaction_subscription_v1`; requesting
both returns `capability_conflict`.
The Bridge returns a process-local stream identifier and the active limits:

```json
{
  "protocol_version": 1,
  "message_type": "server_hello",
  "selected_version": 1,
  "accepted_capabilities": ["agent_event_v1"],
  "stream_id": "550e8400-e29b-41d4-a716-446655440000",
  "limits": {
    "max_frame_bytes": 1048576,
    "max_connections": 16,
    "max_subscribers": 8,
    "subscriber_queue_frames": 8,
    "control_idempotency_entries": 1024,
    "control_idempotency_retention_ms": 300000,
    "control_approval_records": 1024
  }
}
```

The role is fixed for the life of the connection. Controllers request
`control_command_v1`; their `client_id` is the required `issued_by` identity for
every command on that connection. A bridge with the runtime directory also
accepts `agent_runtime_attach_v1` when the controller requests it. That
capability covers attach, detach, and a directory status query. See
[`agent-runtime-attach-v1.md`](agent-runtime-attach-v1.md). A bridge without
the directory does not accept the capability.

## Negotiated Publisher Frames

After negotiation, a publisher sends only message types covered by its accepted
capabilities. An `agent_event_v1` publisher sends self-describing `agent_event`
frames. The remaining fields are the unchanged `AgentEvent v1` payload:

```json
{
  "protocol_version": 1,
  "message_type": "agent_event",
  "agent_id": "codex:session-1",
  "slot": 0,
  "state": "thinking",
  "label": "backend"
}
```

An `interaction_event_v1` publisher sends complete `interaction_event` frames
as defined by [`interaction-event-v1.md`](interaction-event-v1.md). Sending an
unnegotiated or unsupported message type returns `invalid_frame` and closes the
connection.

An `adapter_session_v1` publisher sends explicit register, disconnect, and
release frames and receives a lifecycle acknowledgement. It must register
before publishing owned events. See
[`adapter-session-v1.md`](adapter-session-v1.md).

## Subscriber Frames

A negotiated state subscriber receives an atomic current `state_snapshot`
followed by ordered `state_update` frames. It is read-only and has no replay or
resume offset. See [`state-subscription-v1.md`](state-subscription-v1.md).

A negotiated interaction subscriber receives
`interaction_subscription_started` followed only by live `interaction_update`
frames. It has no snapshot, history, replay, or resume offset. See
[`interaction-subscription-v1.md`](interaction-subscription-v1.md).

## Controller Frames

A controller may send a `focus_query` frame between control commands to read
the current Bridge-local focus. A local operator client may also send a
`session_query` frame to read a bounded current session registry snapshot. Both
queries are read-only and use the already negotiated controller connection.
The Bridge returns a bounded `focus_query_result` with either a complete
session identity or `null`:

```json
{
  "protocol_version": 1,
  "message_type": "focus_query_result",
  "stream_id": "550e8400-e29b-41d4-a716-446655440000",
  "focused_session": {
    "agent_id": "codex",
    "session_id": "session-42",
    "project_id": "deskhelm"
  }
}
```

The query is read-only and does not change focus. A UI that changes the visible
session sends a normal targeted `focus` control command; registration,
attention, and approval events never change focus implicitly. A `press_ptt`
control is accepted only when its complete target still matches this focus; PTT
release remains bound to the target captured by the accepted press.

`session_query` returns `session_query_result` with complete identities, slots,
bounded lifecycle timestamps, adapter identity, declared capabilities, and the
current focused identity. It is intended for local dashboards and the web
client that need to map the small `AgentEvent v1` slot projection back to a
targetable session and show which runtime is attached. It does not provide
history or a live session stream. `adapter_id`, `adapter_version`,
`runtime_name`, and `runtime_version` are empty strings when the session has
no adapter record. `capabilities` is an array of known `AdapterCapability`
values, or an empty array:

```json
{
  "protocol_version": 1,
  "message_type": "session_query_result",
  "stream_id": "550e8400-e29b-41d4-a716-446655440000",
  "focused_session": null,
  "sessions": [
    {
      "agent_id": "codex",
      "session_id": "session-42",
      "project_id": "deskhelm",
      "slot": 0,
      "state": "active",
      "registered_at": 1787011201000,
      "updated_at": 1787011202000,
      "disconnected_at": null,
      "adapter_id": "deskhelm-codex",
      "adapter_version": "0.1.0",
      "runtime_name": "codex-cli",
      "runtime_version": "0.163.0",
      "capabilities": ["interaction_events", "submit_prompt", "interrupt"]
    }
  ]
}
```

`session_catalog_query` asks for one active session's bounded model, MCP, and
skills catalog. The Bridge returns `session_catalog_result` with the same
identity. The query is read-only, has no cursor, and does not change focus or
session state. See [`session-catalog-v1.md`](session-catalog-v1.md).

```json
{
  "protocol_version": 1,
  "message_type": "session_catalog_query",
  "agent_id": "codex",
  "session_id": "session-42",
  "project_id": "deskhelm"
}
```

A controller sends one complete `control_command` and receives one correlated
`control_result` before sending the next command. Routing is synchronous and
does not create an unbounded input queue. Malformed or unsupported frames return
`invalid_frame` and close the connection. See
[`control-command-v1.md`](control-command-v1.md) and
[`control-result-v1.md`](control-result-v1.md).

`server_hello.limits` advertises `control_idempotency_entries`,
`control_idempotency_retention_ms`, and `control_approval_records`. A full
idempotency table refuses new dispatches instead of evicting live entries.

## Legacy Compatibility

When the first frame has no `message_type` and is a valid `AgentEvent v1`, the
connection is treated as a legacy publisher. Later frames use the same legacy
shape. A malformed later event is reported and skipped without closing the
connection, preserving Phase 0 behavior.

Legacy clients cannot obtain subscriber or controller capabilities.

## Errors

Negotiated failures use a self-describing frame before disconnect when the
socket remains writable:

```json
{
  "protocol_version": 1,
  "message_type": "protocol_error",
  "code": "capability_unavailable",
  "message": "controller must request control_command_v1"
}
```

Current codes include `invalid_hello`, `version_unavailable`,
`capability_unavailable`, `capability_conflict`, `role_unavailable`,
`subscriber_capacity`, `subscriber_read_only`, `slow_subscriber`,
`snapshot_too_large`, `state_update_too_large`,
`interaction_update_too_large`, and `invalid_frame`.

Version 1 provides no durable history or replay. State subscribers reconnect
and request a fresh snapshot before consuming live events. Interaction
subscribers reconnect to a new live-only stream and accept that missed rich
content is unavailable.

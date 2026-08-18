# Local Transport v1

Status: State and interaction publishing/subscription implemented; controller
role is reserved but not yet enabled

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

The Bridge accepts publishers with `agent_event_v1`, `interaction_event_v1`,
or both. A subscriber requests exactly one of `state_subscription_v1` and
`interaction_subscription_v1`; requesting both returns `capability_conflict`.
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
    "subscriber_queue_frames": 8
  }
}
```

The role is fixed for the life of the connection. `controller` is a valid role
name but currently receives `role_unavailable` and is disconnected.

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

## Subscriber Frames

A negotiated state subscriber receives an atomic current `state_snapshot`
followed by ordered `state_update` frames. It is read-only and has no replay or
resume offset. See [`state-subscription-v1.md`](state-subscription-v1.md).

A negotiated interaction subscriber receives
`interaction_subscription_started` followed only by live `interaction_update`
frames. It has no snapshot, history, replay, or resume offset. See
[`interaction-subscription-v1.md`](interaction-subscription-v1.md).

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
  "code": "role_unavailable",
  "message": "controller connections are not enabled yet"
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

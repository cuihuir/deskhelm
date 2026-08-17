# Local Transport v1

Status: Publisher negotiation implemented; subscriber and controller roles are
reserved but not yet enabled

## Framing and Limits

DeskHelm uses one owning-user Unix stream socket. Each frame is one UTF-8 JSON
object followed by `LF`. A trailing `CR` before `LF` is accepted. The JSON
object, excluding the line ending, must not exceed 1 MiB.

The Bridge handles at most the configured number of concurrent connections
(16 by default). Each publisher is read and processed synchronously, so it has
no unbounded application input queue. Subscriber output queues will be bounded
when that role is enabled.

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

The Bridge currently accepts the `publisher` role with the
`agent_event_v1` capability. It returns a process-local stream identifier and
the active limits:

```json
{
  "protocol_version": 1,
  "message_type": "server_hello",
  "selected_version": 1,
  "accepted_capabilities": ["agent_event_v1"],
  "stream_id": "550e8400-e29b-41d4-a716-446655440000",
  "limits": {
    "max_frame_bytes": 1048576,
    "max_connections": 16
  }
}
```

The role is fixed for the life of the connection. `subscriber` and
`controller` are valid role names but currently receive a `role_unavailable`
error and are disconnected.

## Negotiated Publisher Frames

After negotiation, a publisher sends self-describing `agent_event` frames.
The remaining fields are the unchanged `AgentEvent v1` payload:

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

Sending another message type on that connection returns `invalid_frame` and
closes the connection.

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
  "message": "subscriber connections are not enabled yet"
}
```

Current codes are `invalid_hello`, `version_unavailable`,
`capability_unavailable`, `role_unavailable`, and `invalid_frame`.

Version 1 provides no durable history or replay. Future subscribers reconnect
and request a fresh snapshot before consuming live events.

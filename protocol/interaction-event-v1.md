# InteractionEvent v1

Status: Accepted protocol model; negotiated socket transport is not yet enabled

## Purpose

`InteractionEvent` carries rich, session-scoped Agent activity to Voice
Gateway, TUI, and desktop clients. It is not a hardware projection and must not
be forwarded to physical devices without an explicit minimized projection.

```text
Agent adapter -> InteractionEvent -> Bridge subscribers
                       |
                       +-> Voice, TUI, desktop

AgentEvent v1 ---------------------> state projection and devices
```

## Envelope

Every event is one UTF-8 JSON object.

| Field | Type | Requirement |
|---|---|---|
| `protocol_version` | integer | Must be `1` |
| `message_type` | string | Must be `interaction_event` |
| `event_id` | string | Unique event identifier |
| `kind` | string | One of the supported kinds below |
| `agent_id` | string | Target Agent identity |
| `session_id` | string | Non-empty Agent session identity |
| `project_id` | string | Non-empty project identity |
| `source` | string | Adapter or producer name |
| `source_version` | string | Producing runtime or adapter version |
| `sequence` | integer | Source-local, zero-based or greater |
| `occurred_at` | integer | Positive Unix time in milliseconds |
| `correlation_id` | string | Required for message, tool, and approval events |
| `payload` | object | Kind-specific payload |

`sequence` is monotonic within one source session. It is not a durable global
offset. Consumers that detect a gap reconnect and request a current snapshot;
version 1 does not guarantee history replay.

Example message delta:

```json
{
  "protocol_version": 1,
  "message_type": "interaction_event",
  "event_id": "event-message-2",
  "kind": "message",
  "agent_id": "codex",
  "session_id": "session-42",
  "project_id": "deskhelm",
  "source": "codex-exec",
  "source_version": "1.0",
  "sequence": 2,
  "occurred_at": 1786935000000,
  "correlation_id": "message-1",
  "payload": {
    "role": "assistant",
    "phase": "delta",
    "text": "任务正在执行"
  }
}
```

## Kinds and Payloads

### `message`

| Field | Type | Requirement |
|---|---|---|
| `role` | string | `user`, `assistant`, or `system` |
| `phase` | string | `start`, `delta`, or `complete` |
| `text` | string | Required and non-empty for `delta` |

Events belonging to one logical message share `correlation_id`.

### `tool`

| Field | Type | Requirement |
|---|---|---|
| `tool_call_id` | string | Non-empty and equal to `correlation_id` |
| `name` | string | Non-empty tool name |
| `phase` | string | `start`, `output`, or `complete` |
| `text` | string | Required and non-empty for `output` |
| `stream` | string or null | `stdout` or `stderr` for `output`; otherwise null |
| `exit_code` | integer or null | Allowed only for `complete` |

Tool arguments and raw tool output may contain code or secrets. Adapters should
emit only content required by subscribed local clients and must not copy these
fields into ordinary logs.

### `approval_request`

| Field | Type | Requirement |
|---|---|---|
| `request_id` | string | Non-empty and equal to `correlation_id` |
| `summary` | string | Human-readable action summary |
| `expires_at` | integer | Must be later than `occurred_at` |

This event informs clients that approval is pending. It does not authorize an
action. Approval and rejection are separate targeted `ControlCommand` messages.

### `user_input_required`

Payload field `prompt` is a non-empty explanation of the required input.

### `task_completed`

Payload field `message` may be empty when the source provides no summary.
`error_code` must be empty.

### `task_failed`

Payload field `message` is required and non-empty. `error_code` is optional.

## Terminal and Ordering Semantics

- `message.complete` terminates one logical message correlation, not the whole
  Agent task.
- `tool.complete` terminates one tool correlation.
- `task_completed` and `task_failed` are terminal for the current task.
- Process exit alone is not a successful terminal event. An adapter that exits
  without a final event emits `task_failed`.
- Consumers reject unknown protocol versions and kinds.
- Unknown additional fields may be ignored for forward-compatible additions
  within version 1, but required fields and known payloads are validated
  strictly.

## Transport Status

ADR 0005 defines one negotiated Unix socket with a 1 MiB frame limit and
bounded queues. The current Phase 0 server still accepts only legacy
`AgentEvent v1`; publishing and subscribing to `InteractionEvent v1` will be
enabled after bounded concurrent connection handling and role negotiation are
implemented.

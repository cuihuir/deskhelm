# Interaction Subscription v1

Status: Implemented

## Purpose

`interaction_subscription_v1` provides bounded live delivery of complete
`InteractionEvent v1` objects to local Voice Gateway, TUI, and desktop clients.
It is a rich-content plane and remains separate from state projection and
physical-device output.

Interaction subscriptions have no snapshot, retained history, replay, or
resume offset. A new subscription starts at the next event published after its
registration.

## Negotiation

A client requests the `subscriber` role with exactly one subscription
capability:

```json
{
  "protocol_version": 1,
  "message_type": "client_hello",
  "client_id": "desktop-1",
  "role": "subscriber",
  "supported_versions": [1],
  "capabilities": ["interaction_subscription_v1"]
}
```

A subscriber cannot combine `state_subscription_v1` and
`interaction_subscription_v1` on one connection. Separate connections preserve
independent sequencing and recovery semantics for each plane.

## Subscription Start

After `server_hello`, the Bridge sends:

```json
{
  "protocol_version": 1,
  "message_type": "interaction_subscription_started",
  "stream_id": "550e8400-e29b-41d4-a716-446655440000",
  "subscription_id": "9acbb77a-5f5f-4e7b-b01a-4b17e14abf82",
  "sequence": 0
}
```

This frame confirms registration. It does not represent a snapshot or imply
that any earlier event is available.

## Live Updates

Each later frame wraps one complete `InteractionEvent v1`:

```json
{
  "protocol_version": 1,
  "message_type": "interaction_update",
  "stream_id": "550e8400-e29b-41d4-a716-446655440000",
  "subscription_id": "9acbb77a-5f5f-4e7b-b01a-4b17e14abf82",
  "sequence": 1,
  "event": {
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
}
```

The wrapper `sequence` is positive and monotonic within one
`subscription_id`. The nested event keeps its source-local sequence. Neither
sequence is a durable global offset.

## Bounds and Recovery

- Interaction subscribers share the configured subscriber capacity with state
  subscribers.
- Each subscriber has the negotiated bounded frame queue and write deadline.
- Enqueue never blocks a publisher.
- Queue overflow is terminal and returns `slow_subscriber` when possible.
- An oversized live frame returns `interaction_update_too_large`.
- The connection is read-only; client frames return `subscriber_read_only`.
- Reconnect starts a new live subscription with a new `subscription_id`; lost
  rich events cannot be recovered in version 1.

Consumers should use state snapshots for current operational state and treat
rich interaction gaps as unavailable content, not reconstruct state from
partial interaction history.

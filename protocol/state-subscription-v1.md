# State Subscription v1

Status: Implemented for negotiated local subscribers

## Purpose

`state_subscription_v1` exposes the small `AgentEvent v1` state projection to
local TUI, desktop, and diagnostic clients. It does not carry prompts, tool
arguments, model output, control commands, or durable history.

```text
StateStore
  -> atomic current snapshot
  -> ordered live state updates
  -> local subscriber
```

## Negotiation

A client connects with role `subscriber` and requests the
`state_subscription_v1` capability. A successful `server_hello` is immediately
followed by one `state_snapshot`; the client sends no further frames.

The Bridge reserves connection capacity for publishers. With the default 16
connection limit, at most 8 subscribers are accepted. The active
`max_subscribers` and `subscriber_queue_frames` limits are returned in
`server_hello`.

## Snapshot

The first subscription data frame is:

```json
{
  "protocol_version": 1,
  "message_type": "state_snapshot",
  "stream_id": "bridge-process-id",
  "subscription_id": "subscription-id",
  "sequence": 0,
  "events": [
    {
      "agent_id": "codex:backend",
      "slot": 1,
      "state": "thinking",
      "label": "backend",
      "progress": 0.25,
      "updated_at": 1787011201000,
      "protocol_version": 1
    }
  ]
}
```

The subscriber is registered atomically with snapshot capture. Updates that
arrive after registration are queued and cannot overtake the snapshot on the
wire.

If the complete snapshot cannot fit the negotiated 1 MiB frame limit, the
Bridge returns `snapshot_too_large` and closes the connection. Version 1 does
not paginate snapshots.

## Live Updates

Each later state change is one frame:

```json
{
  "protocol_version": 1,
  "message_type": "state_update",
  "stream_id": "bridge-process-id",
  "subscription_id": "subscription-id",
  "sequence": 1,
  "event": {
    "agent_id": "codex:backend",
    "slot": 1,
    "state": "running_tool",
    "label": "backend",
    "progress": 0.5,
    "updated_at": 1787011202000,
    "protocol_version": 1
  }
}
```

Sequences are positive, monotonic, and local to one `subscription_id`. A new
connection receives a new subscription ID and restarts at snapshot sequence
zero. `stream_id` changes when the Bridge process restarts.

## Slow Subscribers and Recovery

Each subscriber has a non-blocking output queue of 8 frames by default. A full
queue is terminal: the Bridge returns `slow_subscriber` when possible and
disconnects the client. Socket writes also have a two-second deadline so a
client that stops reading cannot hold its worker indefinitely.

After overflow, disconnect, a sequence gap, or a Bridge restart, the client
reconnects and obtains a fresh snapshot. Version 1 has no event replay or
resume offset.

Subscriber connections are read-only. Sending data after negotiation returns
`subscriber_read_only` and closes the connection.

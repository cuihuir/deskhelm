# Protocol

Specifications for agent lifecycle events, device capabilities, transport framing, commands, and compatibility rules.

## Phase 0 Event

Events are newline-delimited JSON objects. Unknown protocol versions and states are rejected.

```json
{
  "protocol_version": 1,
  "agent_id": "project-a:codex:1",
  "slot": 0,
  "state": "waiting_approval",
  "label": "backend",
  "progress": null,
  "updated_at": 1784210000000
}
```

Required fields are `agent_id`, `slot`, and `state`. Valid states are:

- `offline`
- `idle`
- `thinking`
- `running_tool`
- `waiting_approval`
- `waiting_user`
- `completed`
- `failed`

`slot` is zero-based. `progress`, when present, is a number from `0` to `1`. `updated_at` is Unix time in milliseconds. Phase 0 transports one event per line over a local Unix domain stream socket.

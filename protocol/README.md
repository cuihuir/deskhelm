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

## Rich Interaction Protocol

- [`local-transport-v1.md`](local-transport-v1.md) defines framing,
  negotiation, negotiated publisher messages, limits, compatibility, and
  errors.
- [`interaction-event-v1.md`](interaction-event-v1.md) defines session-scoped
  message, tool, approval, input, completion, and failure events.
- [`control-command-v1.md`](control-command-v1.md) defines targeted, expiring,
  idempotent focus, prompt, interruption, approval, and speech controls.
- [`control-result-v1.md`](control-result-v1.md) defines correlated accepted,
  rejected, and duplicate outcomes without private command content.
- [`state-subscription-v1.md`](state-subscription-v1.md) defines atomic state
  snapshots, ordered live updates, queue bounds, and reconnect recovery.
- [`interaction-subscription-v1.md`](interaction-subscription-v1.md) defines
  bounded live-only rich interaction delivery and gap behavior.
- ADR 0005 defines the negotiated single-socket transport direction.

State and interaction publisher/subscriber negotiation and the
`InteractionEvent v1` and `ControlCommand v1` Python models and fixtures are
implemented. Negotiated controllers use `control_command_v1` and receive one
correlated result per structurally valid command.

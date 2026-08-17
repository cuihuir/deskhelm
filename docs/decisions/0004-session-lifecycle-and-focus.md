# ADR 0004: Define Session Lifecycle and Focus

- Status: Accepted
- Date: 2026-08-17

## Context

`SessionRegistry` currently maps a session identity to a display slot, but it
does not distinguish an active session from a temporarily disconnected one and
does not define which session receives untargeted user interaction. Voice input,
interrupts, and future control commands require deterministic focus and recovery
semantics before richer protocols are introduced.

Slots are a presentation resource, not session identity. A process disconnect
must not immediately erase a resumable session, but stale sessions must not
remain valid control targets indefinitely.

## Decision

Represent registered sessions with an immutable `SessionRecord` containing:

- the `SessionKey`
- display slot
- lifecycle state
- registration time
- last update time
- disconnection time when applicable

The initial lifecycle states are:

- `active`: the adapter or Bridge considers the session connected and usable.
- `disconnected`: the session is retained for recovery but cannot be focused.

Expiration removes a disconnected record rather than storing an `expired`
record in the live registry.

Use these transitions:

```text
missing --register--> active --disconnect--> disconnected
   ^                      ^                       |
   |                      |                       |
   +------ expire --------+-------- restore ------+
```

Focus is an explicit registry pointer:

- registration and observation do not change focus
- only an active registered session may be focused
- disconnecting, releasing, replacing, or expiring the focused session clears
  focus
- restoring a session does not restore focus automatically

Disconnected sessions retain their display slot until explicit expiration,
release, or replacement by a legacy event with an authoritative preferred slot.
Dynamic allocation does not silently reclaim disconnected slots.

`AgentEvent v1` remains compatible. Observing a legacy event registers or
restores an `agent_id`-only session in the event's explicit slot and uses the
event timestamp as the lifecycle update time.

## Consequences

- Controls can reject missing or disconnected targets before reaching an
  adapter.
- Temporary adapter loss can recover without changing the slot projection.
- Focus cannot silently jump because an Agent emitted an event.
- Callers must choose and apply an expiration policy; the registry only enforces
  a supplied retention duration.
- Persistence across Bridge restarts remains out of scope.

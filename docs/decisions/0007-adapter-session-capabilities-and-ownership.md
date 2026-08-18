# ADR 0007: Declare Adapter Sessions, Capabilities, and Connection Ownership

- Status: Accepted
- Date: 2026-08-18

## Context

Legacy `AgentEvent v1` identifies only an agent and display slot. Controllers,
rich interactions, reconnection, and runtime-specific behavior require a full
session identity and an explicit statement of what an adapter can produce or
accept. The Bridge also needs to distinguish a current adapter connection from
an older connection for the same session.

## Decision

Negotiated publisher connections may request `adapter_session_v1`. Such a
connection sends `adapter_session` frames with `register`, `disconnect`, or
`release` actions. Every frame names:

- adapter ID and version
- runtime name and version
- `agent_id + session_id + project_id`
- declared adapter capabilities
- occurrence time and, for registration, an optional preferred slot

The Bridge assigns each publisher connection an internal owner ID. Registration
binds the session to that owner. Re-registering the same session transfers
ownership and restores it to active state without restoring focus. Closing a
connection disconnects only sessions still owned by that connection; an older
connection cannot disconnect a replacement owner. Release removes the session
and its slot mapping.

The Bridge acknowledges each accepted lifecycle frame with
`adapter_session_result`, including the action, complete session identity,
Bridge timestamp, and assigned slot. Release acknowledgements use a null slot.

Lifecycle-managed publishers must register before publishing session events.
State events must match an active owned `agent_id + slot`; interaction events
must match the exact active owned session. Declared `state_events` requires the
negotiated `agent_event_v1` transport capability. Declared interaction, tool,
or approval event production requires `interaction_event_v1`.

Lifecycle-managed state events update `StateStore` but do not invoke the legacy
agent-only session observation path. This preserves the complete session record
used by control targeting.

Version 1 does not persist adapter registrations across Bridge restarts and
does not deliver control commands over publisher connections. Provider command
handlers are a separate integration boundary.

## Consequences

- Live controllers can target modern sessions by complete identity.
- Runtime and adapter versions are explicit evidence for compatibility work.
- Connection replacement and closure have deterministic lifecycle behavior.
- Adapters must keep transport negotiation and declared capabilities
  consistent.
- Reconnect requires explicit registration and explicit focus restoration.
- Legacy `AgentEvent v1` publishers remain compatible but cannot provide full
  modern session identity.

## Implementation Status

The protocol model, connection-owned registry, negotiated publisher handling,
acknowledgements, event ownership checks, protocol fixtures, and end-to-end
control-targeting tests are implemented.

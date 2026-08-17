# ADR 0002: Separate Bridge State and Session Projection

- Status: Accepted
- Date: 2026-08-17

## Context

The Phase 0 bridge stores slot state inside `SlotPanel`. This makes the terminal
renderer the source of truth and treats the display slot as part of an agent's
identity. Voice, TUI, and desktop clients need the same state without depending
on a terminal panel, while future sessions need stable identities independent
of the number of visible slots.

`AgentEvent v1` must remain compatible because current CLI and Codex hook
adapters still send an explicit `slot`.

## Decision

Split the Bridge into three responsibilities:

- `StateStore` owns the current slot projection and publishes immutable
  snapshots to in-process subscribers.
- `SessionRegistry` maps `agent_id + session_id + project_id` identities to
  display slots.
- `SlotPanel` renders snapshots and does not own state.

Legacy `AgentEvent v1` events are observed as sessions containing only an
`agent_id`, and their explicit slot remains authoritative. New internal callers
may ask `SessionRegistry` to allocate the first free slot dynamically.

The first subscription interface is in-process. A later ADR will decide whether
external subscribers share the existing Unix socket or use a separate local
endpoint.

## Consequences

- Additional frontends can consume Bridge state without copying display logic.
- Session identity can evolve without changing the Phase 0 wire event.
- The Bridge can later introduce concurrent connection handling behind a
  thread-safe store and registry.
- Slot replacement currently removes the previous session projection; history
  and session persistence remain out of scope.
- Rich interaction and control messages still require separate protocols.

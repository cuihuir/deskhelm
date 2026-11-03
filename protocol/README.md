# Protocol

Specifications for the local protocol between the DeskHelm Bridge, session
hosts, and surfaces. Device and hardware transports are not specified yet.

## Protocol v2

[`deskhelm-v2.md`](deskhelm-v2.md) is the current protocol (ADR 0043): host
and client roles on one Unix socket, content-free facts, effects with
receipts, a resumable console projection, a bounded interaction window, and
commands that name Bridge approval ids. `bridge/deskhelm_protocol` holds its
Python models and `bridge/deskhelm_core` the Bridge that implements it.

## Removed v1 Documents

DeskHelm 0.1.0 removed the v1 Bridge, including the Phase 0 `AgentEvent v1`
slot event. Nothing implements the documents below; they remain as the record
that older ADRs link to.

- [`local-transport-v1.md`](local-transport-v1.md): framing, negotiation, and
  the publisher, subscriber, and controller roles.
- [`adapter-session-v1.md`](adapter-session-v1.md): adapter identity,
  capabilities, lifecycle, and connection ownership.
- [`interaction-event-v1.md`](interaction-event-v1.md): session-scoped rich
  interaction events.
- [`control-command-v1.md`](control-command-v1.md) and
  [`control-result-v1.md`](control-result-v1.md): targeted, expiring,
  idempotent controls and their correlated results.
- [`state-subscription-v1.md`](state-subscription-v1.md) and
  [`interaction-subscription-v1.md`](interaction-subscription-v1.md): the
  state and interaction planes.
- [`session-catalog-v1.md`](session-catalog-v1.md): the model, MCP, and skills
  catalog.
- [`agent-runtime-attach-v1.md`](agent-runtime-attach-v1.md): runtime attach
  on the shared user Bridge.

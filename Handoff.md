# Handoff

Date: 2026-08-18

Project: DeskHelm

Repository: <https://github.com/cuihuir/deskhelm>

## Current Objective and Status

Build the no-hardware DeskHelm software path before committing to physical
devices:

```text
Bridge state and sessions
  -> interaction and control protocols
  -> text-only Agent gateway
  -> PTT, ASR, and interruptible TTS
  -> physical controls
```

Phase 0 is working and pushed to `main`. The Bridge has a Unix socket transport,
normalized `AgentEvent v1`, CLI simulator, Codex hook adapter, `StateStore`,
in-process subscriptions, `SessionRegistry`, terminal projection, bounded
concurrent connections, and negotiated state publishers. `InteractionEvent v1`
and `ControlCommand v1` are implemented and covered by compatibility fixtures,
and negotiated state subscribers receive atomic snapshots followed by ordered
live updates. Interaction publishing, control routing, and the controller role
remain unimplemented. Voice Gateway implementation has not started.

## Completed Work

- Created and pushed the initial project baseline.
- Selected `DeskHelm` as the product and repository name.
- Migrated the distribution, primary Python package, CLI, Codex hook CLI, and
  runtime socket path to DeskHelm names under ADR 0003.
- Preserved temporary `agent-io`, `agent-io-codex-hook`, and
  `python -m agent_io_bridge` compatibility entry points.
- Added repository Git attributes, ignore rules, local commit identity, and an
  HTTPS `origin`.
- Separated Bridge state storage, session projection, and terminal rendering.
- Added explicit session lifecycle and focus semantics: register, restore,
  focus, disconnect, release, and expiration.
- Accepted ADR 0005: one negotiated Unix socket with fixed connection roles,
  bounded frames and queues, legacy first-frame compatibility, and
  snapshot-based reconnect recovery.
- Replaced the sequential accept/read loop with a 16-connection bounded worker
  pool, configurable through `--max-connections`.
- Added byte-accurate 1 MiB UTF-8 NDJSON frame enforcement and process-local
  stream identifiers.
- Implemented `client_hello` / `server_hello` for negotiated publishers using
  the `agent_event_v1` capability and self-describing `agent_event` frames.
- Added a validated `send_negotiated_event` Python client path for integrations.
- Added structured protocol errors for invalid negotiation, unavailable roles
  or capabilities, and invalid negotiated frames.
- Preserved no-handshake `AgentEvent v1` clients, including continuing after a
  malformed event on an established legacy connection.
- Enabled negotiated `state_subscription_v1` clients with an atomic current
  snapshot followed by subscription-local ordered state updates.
- Added a separate subscriber limit below the total connection limit, an
  8-frame non-blocking queue per subscriber, a two-second first-frame deadline,
  and a two-second subscriber write deadline.
- Added terminal recovery behavior for slow, read/write-invalid, oversized, or
  capacity-exceeding subscriptions; reconnect always starts from a new snapshot.
- Implemented the `InteractionEvent v1` model for messages, tools, approvals,
  user-input requests, and task terminal events.
- Added four complete wire fixtures and validation/round-trip tests for
  `InteractionEvent v1`.
- Accepted ADR 0006: controls require a complete session target, issuer,
  timestamps, expiry, and idempotency key; approvals copy pending request
  metadata and are never automatically replayed.
- Implemented `ControlCommand v1` for focus, prompt submission, interruption,
  approval, rejection, speech, and stopping speech.
- Added seven complete control wire fixtures plus validation and expiry tests.
- Added unit and end-to-end coverage for events, display, Codex hooks,
  `StateStore`, and `SessionRegistry`.
- Recorded Phase 0 and Bridge-boundary ADRs.
- Added local voice-stack research and a no-hardware software roadmap.
- Reviewed two public Agent I/O projects and extracted streaming, adapter,
  fixture, observability, privacy, and idempotency lessons.
- Added the project constitution and durable DeskHelm-specific collaboration
  rules to `AGENTS.md`.

## Key Decisions

- DeskHelm integrates existing coding agents; it is not a new general Agent or
  LLM runtime.
- Bridge, Voice Gateway, and Physical Surface remain separate components.
- State projection, rich interaction, and control commands remain separate
  protocol planes.
- `AgentEvent v1` remains compatible while richer protocols are designed.
- Session identity is `agent_id + session_id + project_id`; `slot` is a display
  mapping.
- Vendor parsing belongs inside adapters, which must declare capabilities.
- Streams and queues require explicit bounds, ordering, cancellation,
  correlation, and slow-consumer behavior.
- Approval and rejection require precise targets and must not be blindly
  retried.
- Bridge remains dependency-minimal; voice models and GPU runtimes stay outside
  it.
- Canonical runtime identifiers are `deskhelm`, `deskhelm_bridge`, and
  `deskhelm-codex-hook`; legacy names are compatibility aliases only.
- Registration and Agent events never change focus implicitly. Only active
  sessions may be focused; disconnect, release, replacement, and expiration
  clear focus, while restore requires a new explicit focus action.
- New local clients negotiate one fixed role through
  `client_hello` / `server_hello`; only a first-frame `AgentEvent v1` receives
  legacy publisher compatibility.
- Local protocol frames are UTF-8 NDJSON limited to 1 MiB, with bounded
  per-connection queues and no automatic retry for control commands.
- The Bridge accepts at most 16 concurrent connections by default. It never
  places accepted connections into an unbounded application work queue.
- Negotiated publishers support `agent_event_v1`; negotiated subscribers
  support `state_subscription_v1`. The controller role remains unavailable.
- Version 1 has no durable event history or replay. Subscribers recover by
  requesting a fresh snapshot before live events.
- Snapshot capture and subscriber registration are atomic. Snapshot sequence is
  zero; later sequences are monotonic within one `subscription_id`.
- At most half of the connection workers are subscribers by default. Queue
  overflow or a blocked write disconnects the subscriber without blocking
  publishers.
- Every control targets `agent_id + session_id + project_id`; slots never route
  controls. Voice controls also retain session ownership.
- Control idempotency is scoped by `issued_by + idempotency_key`. An allowed
  retry preserves the complete command identity and content.
- Approval and rejection echo the pending request ID, summary, and expiry. The
  command expiry equals the request expiry, and automatic replay is forbidden.

## Important Files

- `AGENTS.md`: constitution and repository rules.
- `README.md`: project overview and current quickstart.
- `docs/software/no-hardware-roadmap.md`: active milestone plan.
- `docs/architecture/multimodal-agent-console.md`: component architecture.
- `docs/research/2026-08-14-local-voice-stack.md`: ASR and TTS research.
- `docs/research/2026-08-17-agent-io-design-lessons.md`: external design review.
- `docs/decisions/0001-phase-0-python-unix-socket.md`: Phase 0 transport.
- `docs/decisions/0002-separate-bridge-state-and-session-projection.md`:
  Bridge state and session boundary.
- `docs/decisions/0003-adopt-deskhelm-name.md`: naming and compatibility plan.
- `docs/decisions/0004-session-lifecycle-and-focus.md`: session lifecycle and
  safe focus semantics.
- `docs/decisions/0005-single-socket-negotiated-local-protocol.md`: negotiated
  local transport, roles, bounds, compatibility, and reconnect behavior.
- `docs/decisions/0006-targeted-expiring-idempotent-controls.md`: targeting,
  expiry, idempotency, retry, and approval safety decisions.
- `protocol/interaction-event-v1.md`: rich session event contract.
- `protocol/control-command-v1.md`: targeted control command contract.
- `protocol/state-subscription-v1.md`: state snapshot, live update, sequencing,
  resource bounds, and reconnect contract.
- `protocol/local-transport-v1.md`: implemented framing, handshake, publisher,
  compatibility, limits, and error contract.
- `bridge/deskhelm_bridge/interaction.py`: `InteractionEvent v1` model and
  validation.
- `bridge/deskhelm_bridge/control.py`: `ControlCommand v1` payload models,
  validation, serialization, and expiry checks.
- `bridge/deskhelm_bridge/subscription.py`: subscription wire models and bounded
  per-subscriber update queue.
- `bridge/deskhelm_bridge/transport.py`: hello and protocol-error wire models.
- `bridge/deskhelm_bridge/server.py`: bounded concurrent socket handling and
  connection role dispatch.
- `bridge/deskhelm_bridge/state_store.py`: state snapshots and subscriptions.
- `bridge/deskhelm_bridge/session_registry.py`: session-to-slot projection.

## Validation

Last verified on 2026-08-18:

```bash
PYTHONPATH=bridge python3 -m unittest discover -s tests -v
```

Result: 73 tests passed.

Repository checks also passed:

```bash
git diff --check
PYTHONPATH=bridge python3 -m compileall -q bridge tests
! rg -n '[[:blank:]]+$' . --glob '!.git/**'
```

## Remaining Work

1. Choose and add the repository license; the GitHub repository is currently
   public but has no root license file.
2. Add bounded interaction fan-out and enable `interaction_event_v1`
   publishers.
3. Implement `ControlRouter`, bounded idempotency retention, a command result
   contract, and the negotiated `controller` role.
4. Define the adapter capability contract and add versioned Codex fixtures.
5. Build the text-only Codex gateway with a deterministic fake provider.
6. Build the Voice Gateway skeleton with fake audio, ASR, TTS, and playback
   providers before installing models.

## Risks and Blockers

- The controller role is not enabled; command routing and results remain
  unimplemented.
- Session disconnect and restore APIs exist but are not yet driven by adapter
  connection lifecycle events.
- `InteractionEvent v1` and `ControlCommand v1` are accepted and modeled, but
  neither is transported by the Bridge server yet.
- Live target, pending approval, idempotency conflict, and dispatch validation
  await `ControlRouter`; protocol parsing alone does not authorize a command.
- The default socket path changed during the pre-release rename; existing
  Bridge and hook processes must restart together after updating.
- Legacy CLI and module-execution aliases still exist and need a deprecation
  review before the first stable release.
- The public repository has no selected open-source license.
- The renamed Python distribution has not been built as a wheel on this
  machine because the system Python environment does not include `setuptools`;
  source-tree imports, both module entry points, and `pyproject.toml` metadata
  were validated directly.
- Codex, Claude, and Gemini compatibility is not yet backed by captured,
  versioned fixture sets.
- ASR and TTS quality, latency, recovery, resource use, and model licensing have
  not been benchmarked on the target machine.

## Next Step

Add bounded `InteractionEvent v1` fan-out and enable negotiated interaction
publishers without mixing rich content into the state projection. Then build
`ControlRouter`. Obtain the license decision from the user when packaging or
external contributions require it.

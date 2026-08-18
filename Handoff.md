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
live updates. Negotiated interaction publishers and bounded live-only
interaction subscribers are implemented without entering state projection.
`ControlRouter`, bounded control state, correlated results, and negotiated
controller connections are implemented. Modern adapter publishers can now
declare capabilities and drive complete session registration, disconnect,
restore, and release lifecycle with connection ownership. Versioned Codex JSONL
evidence is present. Agent and Voice Gateway handlers are not registered yet.
Voice Gateway implementation has not started.

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
- Enabled negotiated `interaction_event_v1` publishers and
  `interaction_subscription_v1` live subscribers with bounded non-blocking
  queues and no retained history.
- Allowed one adapter publisher connection to negotiate both state and rich
  interaction capabilities while requiring each subscriber connection to
  select exactly one plane.
- Verified rich interaction events do not update `StateStore`, session
  projection, terminal rendering, or ordinary Bridge logs.
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
- Implemented `ControlResult v1` with fixed accepted/rejected codes and no
  private command or handler-error content.
- Implemented `ControlRouter` validation for controller identity, expiry,
  active full-session targets, approval metadata, and idempotency conflicts.
- Added bounded idempotency and pending/decided approval records. Capacity
  refuses new work instead of evicting live deduplication state.
- Enabled negotiated `control_command_v1` controller connections with one
  correlated result per structurally valid command.
- Made `focus` an internal router action and exposed explicit non-blocking
  handler registration for Agent and Voice Gateway commands.
- Consumed approval requests after any dispatch attempt, including ambiguous
  handler failure, so approval decisions cannot be replayed.
- Added unit and end-to-end coverage for events, display, Codex hooks,
  `StateStore`, and `SessionRegistry`.
- Recorded Phase 0 and Bridge-boundary ADRs.
- Added local voice-stack research and a no-hardware software roadmap.
- Accepted ADR 0007: adapter sessions declare adapter/runtime identity,
  capabilities, full session identity, and connection-owned lifecycle.
- Implemented `adapter_session_v1` register, disconnect, and release frames plus
  correlated lifecycle acknowledgements.
- Bound modern sessions to server-assigned publisher owners so an old
  connection cannot disconnect a replacement registration.
- Required lifecycle publishers to register active owned sessions before state
  or interaction publishing, while preserving the full session record through
  state updates.
- Made complete modern sessions targetable by `ControlRouter` and verified
  focus, disconnect rejection, restore, and release behavior end to end.
- Added versioned Codex `exec --json` fixtures with explicit official-document
  versus synthetic provenance and malformed/unknown/failure boundaries.
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
- Modern adapter lifecycle uses `adapter_session_v1`. Registration binds the
  complete session to one publisher owner; re-registration transfers ownership
  and restores activity without restoring focus.
- Declared state production requires negotiated `agent_event_v1`; declared
  interaction, tool, or approval production requires `interaction_event_v1`.
- Lifecycle-managed state publishing bypasses legacy agent-only session
  observation so control routing retains full identity.
- Adapter registrations are process-local; version 1 has no durable persistence
  and no control delivery over the publisher connection.
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
- Negotiated publishers support `adapter_session_v1`, `agent_event_v1`,
  `interaction_event_v1`, or a valid combination. Negotiated subscribers select
  exactly one of `state_subscription_v1` and `interaction_subscription_v1`.
  Negotiated controllers use `control_command_v1`.
- Version 1 has no durable event history or replay. State subscribers recover
  through a fresh snapshot; interaction subscribers restart live-only delivery.
- Snapshot capture and subscriber registration are atomic. Snapshot sequence is
  zero; later sequences are monotonic within one `subscription_id`.
- Interaction subscriptions start at sequence zero and then deliver only new
  rich events. They have subscription-local wrapper sequences but no snapshot,
  retained history, replay, or state-projection side effects.
- At most half of the connection workers are subscribers by default. Queue
  overflow or a blocked write disconnects the subscriber without blocking
  publishers.
- Every control targets `agent_id + session_id + project_id`; slots never route
  controls. Voice controls also retain session ownership.
- Control idempotency is scoped by `issued_by + idempotency_key`. An allowed
  retry preserves the complete command identity and content.
- Controller `client_id` is bound to `issued_by`. Exact retained retries return
  the original result without redispatch; changed content is a conflict.
- Live idempotency entries are never evicted to admit new work. The default
  bounds are 1024 idempotency entries, five-minute minimum retention, and 1024
  combined pending/decided approval records.
- Approval and rejection echo the pending request ID, summary, and expiry. The
  command expiry equals the request expiry, and automatic replay is forbidden.
  Any dispatch attempt consumes the request because downstream failure may be
  ambiguous.

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
- `docs/decisions/0007-adapter-session-capabilities-and-ownership.md`: adapter
  identity, capabilities, lifecycle, ownership, and restore semantics.
- `protocol/adapter-session-v1.md`: lifecycle frames, acknowledgements,
  declared capabilities, and event ownership validation.
- `protocol/interaction-event-v1.md`: rich session event contract.
- `protocol/control-command-v1.md`: targeted control command contract.
- `protocol/control-result-v1.md`: correlated control outcomes and fixed result
  codes.
- `protocol/state-subscription-v1.md`: state snapshot, live update, sequencing,
  resource bounds, and reconnect contract.
- `protocol/interaction-subscription-v1.md`: live-only rich updates, bounds,
  sequencing, and gap behavior.
- `protocol/local-transport-v1.md`: implemented framing, handshake, publisher,
  compatibility, limits, and error contract.
- `bridge/deskhelm_bridge/interaction.py`: `InteractionEvent v1` model and
  validation.
- `bridge/deskhelm_bridge/control.py`: `ControlCommand v1` payload models,
  validation, serialization, and expiry checks.
- `bridge/deskhelm_bridge/control_result.py`: correlated result model and fixed
  status codes.
- `bridge/deskhelm_bridge/control_router.py`: live target checks, approval
  tracking, bounded idempotency, focus, and handler dispatch.
- `bridge/deskhelm_bridge/adapter.py`: adapter lifecycle and acknowledgement
  protocol models.
- `bridge/deskhelm_bridge/adapter_registry.py`: connection-owned registration,
  lifecycle, and event validation.
- `bridge/deskhelm_bridge/subscription.py`: subscription wire models and bounded
  per-subscriber update queue.
- `bridge/deskhelm_bridge/interaction_subscription.py`: rich subscription wire
  models, bounded queue, and in-process fan-out hub.
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

Result: 111 tests passed, including strict `ResourceWarning` handling.

Repository checks also passed:

```bash
git diff --check
PYTHONPATH=bridge python3 -m compileall -q bridge tests
! rg -n '[[:blank:]]+$' . --glob '!.git/**'
```

## Remaining Work

1. Choose and add the repository license; the GitHub repository is currently
   public but has no root license file.
2. Register Agent command handlers and build the text-only Codex gateway with a
   deterministic fake provider.
3. Build the Voice Gateway skeleton with fake audio, ASR, TTS, and playback
   providers before installing models.

## Risks and Blockers

- The running Bridge has no Agent or Voice Gateway control handlers yet, so
  non-focus commands safely return `handler_unavailable`.
- Adapter registrations are process-local and must be re-established after a
  Bridge restart. No durable session history or replay is promised.
- Approval tracking is bounded. When its capacity is exhausted, new approval
  requests remain visible to interaction subscribers but cannot be decided
  through the router and return `approval_not_found`.
- The default socket path changed during the pre-release rename; existing
  Bridge and hook processes must restart together after updating.
- Legacy CLI and module-execution aliases still exist and need a deprecation
  review before the first stable release.
- The public repository has no selected open-source license.
- The renamed Python distribution has not been built as a wheel on this
  machine because the system Python environment does not include `setuptools`;
  source-tree imports, both module entry points, and `pyproject.toml` metadata
  were validated directly.
- Codex compatibility currently has official-document and synthetic JSONL
  fixture evidence, but no locally captured authenticated run. Claude and
  Gemini compatibility still lack versioned fixture sets.
- ASR and TTS quality, latency, recovery, resource use, and model licensing have
  not been benchmarked on the target machine.

## Next Step

Register bounded Agent provider/control handlers and build the text-only Codex
gateway with deterministic fake-provider tests. Obtain the license decision
from the user when packaging or external contributions require it.

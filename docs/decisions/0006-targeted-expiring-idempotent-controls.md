# ADR 0006: Require Targeted, Expiring, Idempotent Controls

- Status: Accepted
- Date: 2026-08-17

## Context

DeskHelm controls can change Agent focus, submit private text, interrupt work,
decide approval requests, and control speech. A display slot is not a stable
identity, retries can duplicate consequential actions, and an approval may be
stale by the time a physical or voice action reaches the Bridge.

The protocol needs one consistent safety envelope before controller transport
or routing is enabled. It must distinguish duplicate delivery from a new user
intent without suggesting that every command is safe to retry.

## Decision

Every `ControlCommand v1` names the complete target session using `agent_id`,
`session_id`, and `project_id`. Slots are never control targets. Each command
also includes:

- a unique `command_id`
- the controller identity in `issued_by`
- positive `issued_at` and `expires_at` timestamps
- a non-empty `idempotency_key`
- a kind-specific payload

The Bridge rejects a command at or after `expires_at`. Idempotency keys are
scoped by `issued_by`; reusing a key with different command content is a
conflict. A retry preserves the command ID, idempotency key, target, timestamps,
kind, and payload.

Idempotency and retry policy remain separate. Prompt and speech submission may
be retried only with the unchanged command identity. Interrupt, focus, and
stop-speaking may be repeated only through deduplication. Approval and rejection
are never retried automatically.

Approval decisions must copy the pending request's `request_id`, `summary`, and
`expires_at`. The command envelope expiry must exactly equal the copied request
expiry. A future `ControlRouter` additionally verifies that the request is
pending, the target matches, the summary and expiry match the stored request,
and no decision has already been applied.

Version 1 includes these command kinds:

- `focus`
- `submit_prompt`
- `interrupt`
- `approve`
- `reject`
- `speak`
- `stop_speaking`

Controller transport uses the negotiated `control_command_v1` capability and a
correlated `ControlResult v1` response.

## Consequences

- Controls cannot accidentally target whichever session happens to occupy a
  display slot.
- Stale commands have a deterministic rejection boundary.
- Duplicate delivery can be suppressed without treating approvals as safely
  replayable.
- Controllers must retain stable command identity when retry is allowed.
- The Bridge needs bounded idempotency retention and conflict detection in the
  future `ControlRouter`.
- Approval routing must retain pending request metadata until decision or
  expiry.

## Implementation Status

`ControlRouter`, bounded idempotency retention, bounded pending/decided approval
tracking, and negotiated controller transport are implemented. The controller
`client_id` is bound to `issued_by`. Exact retained retries return the original
outcome without redispatch; conflicting reuse is rejected.

The router refuses a new dispatch when the idempotency table is full instead of
evicting a live entry. Fixed result codes do not expose command content or
handler exceptions. `focus` is handled internally. Other commands require a
registered non-blocking handler; the current Bridge process does not yet
install Agent or Voice Gateway handlers.

An approval request is consumed after any downstream dispatch attempt,
including a handler failure, because the external outcome may be ambiguous and
approval decisions must never be replayed automatically.

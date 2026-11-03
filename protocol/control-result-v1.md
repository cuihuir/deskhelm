# ControlResult v1

Status: Removed in DeskHelm 0.1.0 (ADR 0043). Nothing implements this
document; it remains as the record that older ADRs link to. The current
protocol is [`deskhelm-v2.md`](deskhelm-v2.md).

## Purpose

Every structurally valid `ControlCommand v1` received on a negotiated
controller connection produces one correlated `control_result`. Results contain
only fixed status codes and timing metadata, so prompts, approval summaries,
speech text, reasons, and downstream exception details do not enter ordinary
Bridge output.

## Envelope

```json
{
  "protocol_version": 1,
  "message_type": "control_result",
  "command_id": "command-focus-1",
  "status": "accepted",
  "code": "focused",
  "processed_at": 1786935100001,
  "duplicate": false
}
```

| Field | Type | Requirement |
|---|---|---|
| `protocol_version` | integer | Must be `1` |
| `message_type` | string | Must be `control_result` |
| `command_id` | string | Correlates to the submitted command |
| `status` | string | `accepted` or `rejected` |
| `code` | string | Fixed machine-readable outcome code |
| `processed_at` | integer | Positive Bridge time in milliseconds |
| `duplicate` | boolean | True only for an exact retained retry |

Accepted codes are `focused` and `dispatched`. Current rejection codes are:

- `expired`
- `issuer_mismatch`
- `idempotency_conflict`
- `idempotency_capacity`
- `target_not_found`
- `target_inactive`
- `target_not_focused`
- `handler_unavailable`
- `dispatch_failed`
- `approval_not_found`
- `approval_target_mismatch`
- `approval_summary_mismatch`
- `approval_expiry_mismatch`
- `approval_already_decided`

`dispatched` means an explicit bounded handler accepted the work. Completion,
cancellation, timeout, or downstream failure is reported separately through
the relevant interaction terminal event; dispatch is not a completion claim.

`dispatch_failed` intentionally carries no downstream error text. A handler may
have performed a side effect before failing, so the result is retained for
deduplication. For approval and rejection, a dispatch that may have reached the
runtime consumes the pending request because its downstream outcome is
ambiguous and automatic replay is forbidden.

`handler_unavailable` means no handler took the command. Either none is
registered, or the handler proved that the command never reached its runtime,
for example because the runtime is detached, still attaching, or its relay is
closed. Nothing happened downstream, so the result is not retained for
deduplication, an approval request stays pending, and the same command may be
sent again.

A duplicate of a command that is still being dispatched waits for that dispatch
and returns its result with `duplicate: true`. If the dispatch does not finish
within the router's bound, the duplicate receives `dispatch_failed` with
`duplicate: true`, because the first outcome is still unknown.

Malformed controller frames return `protocol_error` with `invalid_frame` and
close the connection instead of producing a `control_result`.

# ControlResult v1

Status: Implemented

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
- `handler_unavailable`
- `dispatch_failed`
- `approval_not_found`
- `approval_target_mismatch`
- `approval_summary_mismatch`
- `approval_expiry_mismatch`
- `approval_already_decided`

`dispatch_failed` intentionally carries no downstream error text. A handler may
have performed a side effect before failing, so the result is retained for
deduplication. For approval and rejection, any dispatch attempt consumes the
pending request because its downstream outcome is ambiguous and automatic
replay is forbidden.

Malformed controller frames return `protocol_error` with `invalid_frame` and
close the connection instead of producing a `control_result`.

# ControlCommand v1

Status: Implemented model, routing, and negotiated controller transport

## Purpose

`ControlCommand` carries a deliberate action from a local controller to one
Agent session or to Voice Gateway behavior attributed to that session. It is
separate from state and interaction events.

```text
Physical surface, Voice, TUI, desktop
                 -> ControlCommand
                 -> ControlRouter
                 -> Agent adapter or Voice Gateway
```

## Envelope

Every command is one UTF-8 JSON object.

| Field | Type | Requirement |
|---|---|---|
| `protocol_version` | integer | Must be `1` |
| `message_type` | string | Must be `control_command` |
| `command_id` | string | Unique command identity, retained across retries |
| `kind` | string | One of the supported kinds below |
| `agent_id` | string | Non-empty target Agent identity |
| `session_id` | string | Non-empty target session identity |
| `project_id` | string | Non-empty target project identity |
| `issued_by` | string | Non-empty controller identity |
| `issued_at` | integer | Positive Unix time in milliseconds |
| `expires_at` | integer | Must be later than `issued_at` |
| `idempotency_key` | string | Non-empty key scoped by `issued_by` |
| `payload` | object | Kind-specific payload |

No command targets a display slot. Voice commands retain a session target so
speech ownership and interruption are not ambiguous.

Example prompt submission:

```json
{
  "protocol_version": 1,
  "message_type": "control_command",
  "command_id": "command-prompt-1",
  "kind": "submit_prompt",
  "agent_id": "codex",
  "session_id": "session-42",
  "project_id": "deskhelm",
  "issued_by": "voice-gateway",
  "issued_at": 1786935101000,
  "expires_at": 1786935161000,
  "idempotency_key": "prompt-session-42-transcript-8",
  "payload": {
    "text": "请运行测试并总结失败原因"
  }
}
```

## Kinds and Payloads

### `focus`

Payload is an empty object. Routing succeeds only for an active registered
session. Focus is never changed implicitly by another command or event.

### `submit_prompt`

Payload field `text` is required and non-empty. Prompts may contain private
source material and must not enter ordinary logs.

### `interrupt`

Payload field `reason` is an optional string for local UI context. The command
targets the current interruptible work owned by the named session.

### `approve` and `reject`

| Field | Type | Requirement |
|---|---|---|
| `request_id` | string | Exact pending approval request identity |
| `summary` | string | Exact non-empty pending action summary |
| `request_expires_at` | integer | Exact pending request expiry |

The command envelope `expires_at` must equal `request_expires_at`. A router
rejects a missing, mismatched, expired, or already decided request. Approval and
rejection are never automatically replayed, even though duplicate delivery is
deduplicated.

### `speak`

| Field | Type | Requirement |
|---|---|---|
| `text` | string | Non-empty speech text |
| `priority` | string | `low`, `normal`, or `high` |
| `interruptible` | boolean | Whether a later action may stop playback |

### `stop_speaking`

Payload field `speech_id` is optional. A non-empty value targets one speech
item; an empty value stops interruptible speech owned by the target session.

## Expiry and Idempotency

A command is expired when Bridge time is greater than or equal to
`expires_at`. Structural parsing does not prove that a target or approval is
currently valid; `ControlRouter` performs those live checks before dispatch.

The idempotency scope is `issued_by + idempotency_key`. Reusing a key with
different command content is an error. A permitted retry resends the unchanged
command, including `command_id`, timestamps, target, and payload.

The controller connection's negotiated `client_id` must equal `issued_by`.
Exact retries return the retained result with `duplicate: true` and are not
dispatched again. The Bridge retains at most the advertised number of records;
when the table is full it rejects a new dispatch rather than evicting a live
record and risking a duplicate side effect.

Idempotency does not authorize automatic retry:

- `submit_prompt` and `speak` may retry only with the same command identity.
- `focus`, `interrupt`, and `stop_speaking` require Bridge deduplication before
  repeated dispatch.
- `approve` and `reject` never retry automatically.

## Validation and Forward Compatibility

- Unknown protocol versions and command kinds are rejected.
- Required envelope and known payload fields are validated strictly.
- Unknown additional fields may be ignored for compatible additions within
  version 1.
- Text, summaries, and reasons must not be copied into ordinary Bridge logs.

The negotiated `controller` role uses `control_command_v1`. Each valid command
receives a correlated result defined by
[`control-result-v1.md`](control-result-v1.md). `focus` is handled internally.
Other kinds require an explicitly registered non-blocking handler; absent
handlers return `handler_unavailable`. The opt-in text Agent Gateway currently
handles `submit_prompt` and `interrupt`; Voice Gateway and approval handlers
remain separate future integrations.

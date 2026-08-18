# Adapter Session v1

Status: Implemented

## Purpose

`AdapterSession v1` lets a negotiated publisher declare its runtime identity,
capabilities, complete session identity, and lifecycle. It supplements rather
than replaces the state and interaction event planes.

## Transport Capability

A publisher requests `adapter_session_v1` during `client_hello`. A
lifecycle-managed connection must register a session before sending events for
it.

Declared production capabilities must match negotiated transports:

| Adapter capability | Required transport capability |
|---|---|
| `state_events` | `agent_event_v1` |
| `interaction_events` | `interaction_event_v1` |
| `tool_events` | `interaction_event_v1` |
| `approval_requests` | `interaction_event_v1` |

`session_resume`, `submit_prompt`, `interrupt`, and `approval_decisions`
describe adapter behavior for future provider integration; they do not create a
control delivery channel in version 1.

## Lifecycle Frame

```json
{
  "protocol_version": 1,
  "message_type": "adapter_session",
  "action": "register",
  "adapter_id": "deskhelm-codex-exec",
  "adapter_version": "0.1.0",
  "runtime_name": "codex-cli",
  "runtime_version": "0.147.0",
  "agent_id": "codex",
  "session_id": "session-42",
  "project_id": "deskhelm",
  "capabilities": ["state_events", "interaction_events"],
  "occurred_at": 1787035000000,
  "preferred_slot": 1
}
```

All identity and version strings are non-empty. `capabilities` is a non-empty,
duplicate-free array of known values. `occurred_at` is a positive Unix time in
milliseconds. `preferred_slot` is optional and is valid only for `register`.

Actions have these meanings:

- `register`: create, replace, or restore the session and assign a slot.
- `disconnect`: retain identity and slot but mark the session inactive.
- `release`: remove the session and slot mapping.

Re-registering restores activity but never restores focus automatically.

## Acknowledgement

Each accepted lifecycle frame receives one acknowledgement:

```json
{
  "protocol_version": 1,
  "message_type": "adapter_session_result",
  "action": "register",
  "agent_id": "codex",
  "session_id": "session-42",
  "project_id": "deskhelm",
  "occurred_at": 1787035000001,
  "slot": 1
}
```

`occurred_at` is the Bridge acknowledgement time. Register and disconnect
results include the retained slot; release uses `null`.

## Ownership and Event Validation

The Bridge assigns an internal owner ID to each publisher connection. A session
has one current owner. Re-registration transfers ownership. Connection close
disconnects only sessions still owned by that connection.

For lifecycle-managed publishers:

- an `agent_event` must match an active owned `agent_id + slot` and require the
  declared `state_events` capability;
- an `interaction_event` must match the exact active owned session and require
  the capability appropriate to its event kind;
- invalid ownership, inactive targets, or capability mismatches return
  `invalid_frame` and close the connection.

Registrations are process-local. Version 1 provides no durable session
persistence or replay.

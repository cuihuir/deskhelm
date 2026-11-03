# DeskHelm Protocol v2

Status: the only DeskHelm protocol since 0.1.0 (ADR 0043), implemented by
`bridge/deskhelm_protocol` and `bridge/deskhelm_core`. The user Bridge listens
on `$XDG_RUNTIME_DIR/deskhelm/bridge.sock`, or `/tmp/deskhelm-<uid>/bridge.sock`
without `XDG_RUNTIME_DIR`. Hosts start it on demand, and
`deskhelm bridge [--socket PATH]` runs it in the foreground.

## Framing

- One local Unix stream socket, mode owner-only. A lock file next to it keeps
  one Bridge per socket.
- Each frame is one UTF-8 JSON object followed by `\n`, at most 1 MiB, with a
  string `type`.
- The first frame must be `hello`, within 5 seconds. A malformed frame gets
  an `error` frame and the connection closes.

## Handshake

```json
{"type": "hello", "protocol_version": 2, "role": "client", "client_id": "web", "tier": "operator"}
{"type": "hello_ok", "protocol_version": 2, "connection_id": "…", "instance_id": "…"}
```

`role` is `host` or `client`. A host has no `tier`. A client's `tier` is
`operator`, `voice`, `device`, or `observer`. `instance_id` changes whenever
the Bridge restarts; ids it issued before are then unknown.

Refusals use `{"type": "error", "code": "…", "message": "…"}` and close the
connection. Codes include `protocol_violation`, `unsupported_version`,
`capacity`, `already_active`, `not_permitted`, `handshake_timeout`, and
`invalid_frame`.

## Hosts

A host connection opens exactly one session. The connection is its lease.

```json
{"type": "session_open",
 "session": {"agent_id": "grok", "session_id": "local-…", "project_id": "demo"},
 "runtime": {"adapter_id": "…", "adapter_version": "…", "runtime_name": "…", "runtime_version": "…"},
 "capabilities": {…},
 "open_requests": [{"provider_ref": "…", "kind": "approval", "summary": "…", "expires_at": 0, "answerable": true}]}
```

The answer is `session_open_result` with `status` `opened` or `rejected`. A
session that is still active on another connection is rejected with
`already_active`. A disconnected session may be reopened; its approvals are
rebuilt from `open_requests` with new approval ids.

`capabilities` describes what the vendor really does, and the Bridge enforces
it:

| Field | Values |
|---|---|
| `version` | integer ≥ 1 |
| `prompt_while_busy` | `steer`, `queue`, `reject` |
| `interrupt` | boolean |
| `approvals` | `answerable`, `notify_only`, `none` |
| `approval_overflow` | `decline`, `native_ui` |
| `user_input` | `answerable`, `native_ui`, `none` |
| `resume` | boolean |
| `configure` | subset of `["model", "effort"]` |
| `catalog` | subset of `["models", "mcp", "skills"]` |

### Facts

`{"type": "fact", "seq": n, "kind": "…", "meta": {…}, "content": {…}}`.
`seq` starts at 1 and increases by one; a gap closes the connection. `meta`
is content-free and may reach every tier. `content` reaches only the operator
interaction stream.

| Kind | `meta` | `content` |
|---|---|---|
| `ready` | — | — |
| `turn_started` | `turn_ref`, optional `command_id` | — |
| `message` | `message_ref`, `role` (`user`, `assistant`, `system`), `phase` (`delta`, `complete`), optional `command_id` | `text` |
| `tool` | `tool_ref`, `label`, `phase` (`start`, `output`, `complete`), optional `exit_code`, `stream`, `replace` | `text` |
| `request_opened` | `provider_ref`, `kind` (`approval`, `input`), `summary`, `expires_at`, `answerable` | — |
| `request_resolved` | `provider_ref`, `by` (`native`, `deskhelm`, `cancelled`, `expired`) | — |
| `turn_terminal` | `status` (`completed`, `interrupted`, `failed`), optional `failure`, `reusable`, `turn_ref` | optional `message` |
| `status` | `attention` (boolean) | — |
| `catalog` | — | `catalog` object, at most 256 KiB |
| `capabilities` | `capabilities` (a full matrix) | — |
| `session_ended` | `reason` | — |

An `output` fact's text extends the tool's output; with `replace: true` it
replaces it, for a vendor that reports the whole output each time.

A `capabilities` fact replaces the matrix once the driver knows what its
vendor allows, for example after the vendor handshake.

`summary` and `label` must be content-free: a tool kind or a bare tool name,
never a command line or a path. `failure` is one of `provider`,
`usage_limit`, `auth`, `protocol`, `transport_lost`, `process_exit`, or
`timeout`.

### Effects

The Bridge asks the host to act:

```json
{"type": "effect", "effect_id": "e-…", "kind": "submit", "payload": {"command_id": "…", "text": "…"}}
```

| Kind | Payload besides `command_id` |
|---|---|
| `submit` | `text` |
| `interrupt` | — |
| `respond` | `provider_ref`, `decision` (`approve`, `reject`) |
| `configure` | `model` and/or `effort` |

The host answers every effect promptly with
`{"type": "effect_result", "effect_id": "…", "outcome": "accepted"}`.
`undelivered` means nothing reached the vendor. `unsupported` means the
vendor cannot do it. Effect handling must not block.

`session_close` ends the session and the connection.

## Clients

### Subscriptions

`{"type": "subscribe", "stream": "console"}` returns a snapshot:

```json
{"type": "snapshot", "stream": "console", "instance_id": "…", "seq": 12,
 "state": {"focus": null, "sessions": [SessionView, …]}}
```

and then `delta` frames with `seq` increasing by one and a list of
`changes`: `{"op": "session", "session": SessionView}`,
`{"op": "session_removed", …key}`, or `{"op": "focus", "focus": key|null}`.
A session appears in a delta only when its view changed, so a streaming
reply sends no console deltas.
A client that saw `seq` from the same `instance_id` may send
`"after": {"instance_id": "…", "seq": 12}` and receives only the missed
deltas when the Bridge still holds them, otherwise a snapshot.

A `SessionView` carries the key, `runtime`, `capabilities`, `lifecycle`
(`active`, `disconnected`), `activity` (`starting`, `idle`, `working`,
`waiting_approval`, `waiting_input`), `turn_id`, `last_outcome`,
`approvals` (each with `approval_id`, `kind`, `summary`, `expires_at`,
`answerable`), `attention`, `opened_at`, and `catalog_rev`, which counts the
catalogs the host has pushed (0 before the first), so a client knows when to
query the catalog again.

`{"type": "subscribe", "stream": "interaction", "sessions": [key, …]}` is
for the operator tier only; `sessions` is optional. The Bridge answers with
`{"type": "snapshot", "stream": "interaction", "seq": n, "events": count}`,
replays each session's recent window as `interaction` frames with
`"replay": true`, and then sends live `interaction` frames. The window is
bounded per session (200 events, 256 KiB), held in memory only, never
written to disk, and dropped when the session ends. Interaction events keep
the v1 `InteractionEvent` shape; approval events name the Bridge approval id
as `request_id`.

### Commands

```json
{"type": "command", "command": {"command_id": "…", "kind": "approve",
 "agent_id": "…", "session_id": "…", "project_id": "…",
 "issued_by": "web", "issued_at": 0, "expires_at": 0,
 "idempotency_key": "…", "payload": {"approval_id": "a-…"}}}
```

| Kind | Payload | Tiers |
|---|---|---|
| `focus` | — | operator, device |
| `submit_prompt` | `text` | operator, voice |
| `interrupt` | — | operator, device |
| `approve`, `reject` | `approval_id` | operator, voice, device |
| `configure_session` | `model` and/or `effort` | operator |

`issued_by` must equal the client's `client_id`. Every valid command gets one
`command_result` with `command_id`, `status` (`accepted`, `rejected`),
`code`, and `duplicate`. A dispatched command is answered only after the
host's `effect_result`.

| Code | Meaning |
|---|---|
| `focused`, `dispatched` | accepted |
| `expired`, `issuer_mismatch`, `invalid_command` | the envelope is wrong |
| `idempotency_conflict`, `idempotency_capacity` | the receipt table refused it |
| `target_not_found`, `target_inactive` | no active session has that key |
| `not_permitted` | the client's tier may not send it |
| `unsupported` | the session's capabilities exclude it |
| `approval_not_found`, `approval_already_decided` | the approval id is unknown, expired, or decided |
| `undelivered` | the host proved nothing reached the vendor |
| `dispatch_failed` | the outcome is unknown: no receipt within 5 seconds or the host left |

Receipts are scoped by `issued_by + idempotency_key` and fingerprinted with
SHA-256. A duplicate of a dispatching command waits for the first result. A
retry must repeat the complete command. `undelivered` and `unsupported`
results are not retained, so the same command may be sent again; an approval
stays pending. Every other result is retained for at least five minutes, and
an approval counts as decided. Approvals are never retried automatically.

### Queries

`{"type": "query", "query_id": "…", "query": "catalog", "session": key}`
returns `query_result` with `status` (`ok`, `unavailable`, `not_permitted`)
and the session's cached `catalog`. Only the operator tier reads catalogs.

## Lifecycle and bounds

- Focus changes only by an explicit `focus` command on an active session, and
  clears when that session disconnects or ends.
- When a host connection closes, its session becomes `disconnected`, its
  approvals are dropped, and in-flight effects answer `dispatch_failed`. A
  disconnected session is removed after two minutes unless a host reopens it.
- Expired approvals leave the console on the next tick.
- Defaults: 32 sessions, 32 hosts, 16 clients, 64 connections, 1024
  receipts, 16 approvals per session, 512 queued frames per connection. A
  full outbound queue closes that connection; the client resubscribes.

# Session Catalog v1

Status: Removed in DeskHelm 0.1.0 (ADR 0043). Nothing implements this
document; it remains as the record that older ADRs link to. The current
protocol is [`deskhelm-v2.md`](deskhelm-v2.md).

## Purpose

A controller asks one active session for a bounded, private-content-free view
of its model choices, MCP connection status, skill names, and vendor-declared
capability tokens. The query does
not change the session and does not create a replayable record.

`configure_session` is a separate control. It stores a model and reasoning
override for the next new turn. See
[`control-command-v1.md`](control-command-v1.md).

## Query

A negotiated controller sends one UTF-8 JSON object on the existing
`control_command_v1` connection:

| Field | Type | Requirement |
|---|---|---|
| `protocol_version` | integer | Must be `1` |
| `message_type` | string | Must be `session_catalog_query` |
| `agent_id` | string | Non-empty target, at most 512 characters, no whitespace or control characters |
| `session_id` | string | Same constraint |
| `project_id` | string | Same constraint |

```json
{
  "protocol_version": 1,
  "message_type": "session_catalog_query",
  "agent_id": "codex",
  "session_id": "local-alpha",
  "project_id": "deskhelm"
}
```

There is no cursor. The Bridge does not follow a vendor cursor.

## Result

The Bridge returns one `session_catalog_result` and keeps the connection open.

| Field | Type | Requirement |
|---|---|---|
| `protocol_version` | integer | `1` |
| `message_type` | string | `session_catalog_result` |
| `agent_id`, `session_id`, `project_id` | string | The requested identity |
| `available` | boolean | `true` only when `notice` is empty |
| `notice` | string | Empty or one code below |
| `model` | string | Current model slug, or empty |
| `reasoning_effort` | string | Session override, or empty when unset |
| `models` | array | At most 40 models |
| `models_notice` | string | Empty or `unavailable` |
| `mcp_servers` | array | At most 24 servers |
| `mcp_notice` | string | Empty or `unavailable` |
| `skills` | array | At most 80 skills |
| `skills_notice` | string | Empty or `unavailable` |
| `agent_capabilities` | array | At most 32 vendor-declared tokens. These are names, not `AdapterCapability` values |
| `truncated` | boolean | A section hit its cap or the vendor returned another page |

```json
{
  "protocol_version": 1,
  "message_type": "session_catalog_result",
  "agent_id": "codex",
  "session_id": "local-alpha",
  "project_id": "deskhelm",
  "available": true,
  "notice": "",
  "model": "gpt-6.1-sol",
  "reasoning_effort": "medium",
  "models": [
    {
      "id": "gpt-6.1-sol",
      "name": "Sol",
      "is_default": true,
      "default_effort": "medium",
      "efforts": [
        {"id": "medium", "description": "balanced"}
      ]
    }
  ],
  "models_notice": "",
  "mcp_servers": [
    {
      "name": "docs",
      "status": "connected",
      "auth": "oAuth",
      "tools": ["search"]
    }
  ],
  "mcp_notice": "",
  "skills": [
    {
      "name": "review",
      "description": "Review a change",
      "scope": "repo",
      "enabled": true
    }
  ],
  "skills_notice": "",
  "agent_capabilities": ["interrupt_receipt_v1"],
  "truncated": false
}
```

The example is synthetic. Model, MCP, and skill fields follow the public
app-server schema. `agent_capabilities` shows the field shape and is not a
captured vendor payload.

## Notices

| `notice` | When |
|---|---|
| empty | The provider returned a catalog |
| `target_not_found` | No registered session |
| `target_inactive` | The session is not active |
| `catalog_unavailable` | No provider, or the provider has no attached thread |
| `catalog_failed` | The provider raised, or the body was not a catalog |

A non-empty notice forces `available` false and each section notice to
`unavailable`. A single vendor call can fail without failing the whole catalog.
That section's notice becomes `unavailable`, and the other sections remain.

## Bounds and omitted fields

Tokens are at most 128 characters. Reasoning efforts are at most 64, with at
most 8 efforts on a model. Labels collapse whitespace. Model names stop at 128
characters and descriptions at 240, with a final ellipsis when clipped.

MCP status is `notStarted`, `starting`, `connected`,
`authenticationRequired`, `failed`, `cancelled`, `disabled`, or `unknown`.
Auth is `unknown`, `unsupported`, `notLoggedIn`, `bearerToken`, `oAuth`, or
empty. Skill scope is `user`, `repo`, `system`, `admin`, or `unknown`. Each
server lists at most 12 tool names. `agent_capabilities` keeps at most 32
unique tokens of at most 128 characters. A token with whitespace, a slash, or
`..` is dropped. The field defaults to an empty array when a provider omits it.

The result never includes a working directory, skill path, tool schema,
resource, origin, vendor error text, or skill body. Unknown fields are
dropped at the Bridge boundary.

## Provider rules

Bridge checks the session before calling the provider. The provider callback
is copied under the router lock and invoked outside that lock. The
`configure_session` handler must not perform vendor I/O while the router holds
its lock. It validates against the provider's in-memory model cache.

The Codex adapter reads one attached thread, sequentially, and stops after one
page. Catalog timeouts fail the waiter. They do not publish `task_failed` and
do not disconnect a healthy app-server connection.

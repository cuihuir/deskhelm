# Agent Runtime Attach v1

Status: Removed in DeskHelm 0.1.0 (ADR 0043). Nothing implements this
document; it remains as the record that older ADRs link to. The current
protocol is [`deskhelm-v2.md`](deskhelm-v2.md).

## Purpose

One user bridge hosts many DeskHelm-started sessions. A local controller
attaches one runtime for one session, then detaches that runtime when the
session ends. Codex uses its app-server socket and a TUI proxy path. Claude
and Grok use one already-listening stdio relay socket. The frame does not
carry a working directory, a prompt, or a transcript.

The capability is `agent_runtime_attach_v1`. The bridge accepts it only while
the runtime directory is enabled, and only on a controller that also requested
`control_command_v1`.

## Directory status

```json
{
  "protocol_version": 1,
  "message_type": "agent_runtime_directory_query"
}
```

The result is `agent_runtime_directory_status`:

| Field | Type | Meaning |
|---|---|---|
| `enabled` | boolean | The directory is installed |
| `voice_enabled` | boolean | This bridge process loaded voice |
| `capacity` | integer | Maximum attached runtimes |
| `attached` | integer | Runtimes attached now |

The result has no socket path.

## Attach

| Field | Type | Requirement |
|---|---|---|
| `protocol_version` | integer | Must be `1` |
| `message_type` | string | Must be `agent_runtime_attach` |
| `agent_id` | string | Non-empty, at most 512 characters, no whitespace or controls |
| `session_id` | string | Same constraint |
| `project_id` | string | Same constraint |
| `source_version` | string | Non-empty, at most 128 characters, no control characters |
| `runtime_kind` | string | Optional. Omitted means `codex_app_server`. `claude_stream` and `grok_stdio` select a relay |
| `app_server_socket` | string | Required for Codex. Absolute path inside the DeskHelm runtime directory. The final component may be a symlink to the socket, as Codex app-server does. The parent must stay inside the directory. Forbidden when `runtime_kind` is a relay |
| `tui_proxy_socket` | string | Required for Codex. Absolute path that does not exist yet, inside that same directory, and different from the app-server socket and the bridge socket. Forbidden when `runtime_kind` is a relay |
| `relay_socket` | string | Required for Claude and Grok. Absolute path of an existing socket inside the runtime directory, different from the bridge socket. Forbidden for Codex |

A `working_directory` or `cwd` field is rejected. The result is
`agent_runtime_attach_result` with the same identity and one status:
`attached`, `already_attached`, `capacity`, `invalid`, `failed`, or
`detached`. It contains no path and no exception text.

## Detach

`agent_runtime_detach` names the same identity. The result is
`agent_runtime_detach_result` with status `detached`, `not_attached`,
`invalid`, or `failed`.

Detaching a runtime that is still starting cancels it. The runtime is closed
after start returns. Closing the bridge detaches every runtime. Closing the
controller does not.

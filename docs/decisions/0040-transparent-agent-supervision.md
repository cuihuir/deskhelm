# ADR 0040: Transparent Supervision of Codex, Claude, and Grok

Date: 2026-10-21

Status: Accepted

## Context

DeskHelm already hosts many Codex sessions on one user bridge. The web client
and the dashboard could list those sessions, but a client could not see which
runtime was attached or which capabilities that runtime declared. Claude Code
and Grok were available on this machine and stayed outside the bridge.

The product goal is local supervision of agents DeskHelm starts. It is not a
remote monitoring app, an IM channel, or a reader of transcripts DeskHelm did
not create.

## Decision

- One bridge remains the control plane. Codex, Claude, and Grok are three
  runtime kinds on the existing directory, not three bridges.
- Omitted `runtime_kind` stays `codex_app_server` and still names
  `app_server_socket` plus `tui_proxy_socket`. `claude_stream` and
  `grok_stdio` name `relay_socket` only. Mixing the two socket sets is
  invalid.
- The attach frame still rejects a working directory. Grok's ACP handshake
  may include the directory only inside the launcher process that owns the
  vendor stdin.
- `deskhelm agent claude` and `deskhelm agent grok` start the vendor in the
  current directory, register an adapter session, and attach the relay. The
  prompt is never a process argument. Vendor stderr is discarded. A line over
  1 MiB or a full relay queue stops the process instead of dropping output.
- `session_query` always includes `adapter_id`, `adapter_version`,
  `runtime_name`, `runtime_version`, and `capabilities`. Missing adapter
  records use empty strings and an empty capability list. Clients do not
  invent a second capability channel.
- The session catalog gains `agent_capabilities`: at most 32 vendor-declared
  tokens. These names are not new `AdapterCapability` values. Paths and
  whitespace are dropped. Claude contributes its init capability list. Grok
  contributes boolean-true paths and empty capability objects from
  `agentCapabilities`, skipping `auth` and keys that start with `_`.
- Interaction output stays on the existing interaction plane. Tool arguments,
  thoughts, working directories, and vendor error text are not published.
  A failed turn uses the fixed message `这一轮失败` or `这一轮没有完成`.
- `configure_session` for Claude and Grok accepts the current model or a
  clear, and rejects a different model or a non-empty effort. It does not
  write vendor configuration.
- Web and the dashboard render the same session-query fields. The capability
  panel shows vendor-declared names under 声明 and hides that section when
  the list is empty. Selecting a session still does not change Bridge focus.
- Agents DeskHelm did not start stay off the page. This ADR does not add an
  IM channel or a remote app.

## Consequences

- A bridge process started before this decision cannot attach Claude or Grok
  until that process is restarted. Restart is an operator action. A running
  user bridge is not killed by DeskHelm itself.
- The next `deskhelm agent` against a bridge that has this factory loads the
  relay without changing the PTT listener. Voice still follows ADR 0039.
- Codex approval behavior is unchanged. Claude and Grok approvals are bounded
  to eight pending requests. A request past that cap is not published.
- Grok's permission result shape is the ACP `selected` outcome. It was not
  captured from a live allow that persists a permission.

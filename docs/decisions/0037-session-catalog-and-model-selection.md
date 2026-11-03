# ADR 0037: Session Catalog and Model Selection

Date: 2026-10-20

Status: Accepted

## Context

The loopback web client can focus, interrupt, prompt, approve, and reject a
Bridge-registered session. Codex CLI and the Codex app also let an operator
switch the model, see MCP connection status, and insert a skill. Those reads
come from the attached Codex app-server thread, not from DeskHelm.

The page must stay a Bridge client. It must not write `config.toml`, call MCP
tools, read skill bodies, follow an unbounded cursor, or treat another Codex
thread as a control target. DeepSeek harness shows a similar composer surface,
and it stays an external reference. DeskHelm does not import it.

`thread/settings/update` is not part of the inspected app-server surface.
`turn/start` accepts `model` and `effort` for that turn. `config/value/write`
would change the operator's Codex configuration, so it is out of scope.

## Decision

Add a read-only controller frame, `session_catalog_query` /
`session_catalog_result`, and a `configure_session` control.

- Bridge checks that the session is registered and active, then calls the one
  catalog provider outside the control-router lock. The Codex app-server
  adapter is that provider. A missing provider returns `catalog_unavailable`.
- The adapter reads only its attached thread: `thread/read` with
  `includeTurns: false`, one page of `model/list`, one page of
  `mcpServerStatus/list` with `toolsAndAuthOnly`, and `skills/list` for that
  thread's working directory. The working directory stays inside the adapter.
- The public result keeps model ids, display labels, MCP names, connection and
  auth status, tool names, and skill name, description, scope, and enabled
  flag. It drops paths, tool schemas, resources, origins, vendor errors, and
  skill bodies. One page is enough. A cursor sets `truncated` and stops.
- A failed section leaves the others in place and marks that section
  `unavailable`. Catalog waits do not publish `task_failed` or drop the
  app-server connection.
- `configure_session` may set or clear `model` and `reasoning_effort`. The
  handler checks the choice against the model list cached by the last catalog
  read for 120 seconds. It does not call app-server while the router lock is
  held. The override is added to later `turn/start` requests, including prompts
  that enter through the same handler, and is omitted from `turn/steer`.
- The web page keeps the transcript clear. Model and reasoning sit on the
  composer and open one popover; skills and MCP open from 能力. The page
  fetches when the selected session changes, and again on open if that
  catalog is missing, or on refresh. Choosing a model or effort sends
  `configure_session` immediately. 不覆盖 clears both. A skill click inserts
  `$name` into the composer. The popover says the choice starts on the next
  new turn and does not write Codex configuration.

`/compact`, `/plan`, `/fast`, `/status`, `/goal`, and `/rename` stay deferred.
Destructive or global commands stay out: archive, delete, fork, cloud,
worktree, feedback, plugin install, MCP tool calls, MCP OAuth, skill config
writes, filesystem APIs, and `thread/shellCommand`. `plugin/list` stays unused
while it is under development.

## Consequences

An operator must restart Alpha so the adapter registers the provider, then
restart the web process so the page loads the panel. Until then the button can
appear while the catalog says it is unavailable.

An active turn keeps its current model. The next new turn uses the override.
Clearing the override returns later turns to the thread's own model. The
catalog is not stored on the page across reloads.

## Alternatives considered

Putting the catalog in a control result was rejected because results stay
private-content-free and handlers must return without waiting on app-server.
Writing `config.toml`, starting an empty turn, or porting the DeepSeek harness
plugin host were rejected because they change Codex outside the attached
session or pull another agent runtime into DeskHelm.

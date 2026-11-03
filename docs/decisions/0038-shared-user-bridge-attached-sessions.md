# ADR 0038: Shared User Bridge for Attached Sessions

Date: 2026-10-21

Status: Accepted in part. ADR 0043 supersedes the in-process runtime
factories, attach frames, and runtime directory; the shared user Bridge at
`bridge.sock` remains.

## Context

DeskHelm is the local control surface for agents this machine started. The
operator wants those agents in one place, the way tmux shows every session:
one web page, and later one remote app and one IM channel, should attach once
and see every session.

ADR 0005 already chose one Unix socket. The per-directory Alpha bridge was a
workaround. One Bridge process could host one Codex app-server adapter, because
control handlers and the catalog provider are single-owner. Two directories
therefore started two Bridges. `deskhelm web` then refused to guess which
socket to open. A multiplexer inside the web client would have to be copied
into the dashboard, the future app, and every IM channel.

Codex app-server and the managed TUI proxy are session transports. They are
not the control plane. The user bridge must not accept a working directory
from an attach request. Explicit `alpha --socket` still needs a private bridge
so tests and isolated runs stay separate.

## Decision

Default Alpha joins one long-lived user bridge at `default_socket_path()`
(`bridge.sock`). Each Alpha attaches one session to it. Web, the dashboard,
and later clients use that same socket. This slice does not build the remote
app or the IM channel.

- The bridge hosts an `AgentRuntimeDirectory` of at most 32 attached runtimes.
  The directory registers one dispatcher per control kind and one catalog
  provider. Dispatch and catalog lookup use `agent_id + session_id +
  project_id`. The facade given to a runtime forwards `resolve_approval` to
  the real control router. A second runtime does not replace the first
  handler.
- A controller that negotiates `agent_runtime_attach_v1` may send
  `agent_runtime_attach`, `agent_runtime_detach`, and
  `agent_runtime_directory_query`. The bridge accepts that capability only
  while the directory is enabled. Results use fixed status codes and carry no
  paths, working directories, or exception text.
- Attach names two absolute sockets. Both paths must stay inside the DeskHelm
  runtime directory, must differ, and must not be the user bridge socket.
  Confinement resolves the parent directory and leaves the final component
  unfollowed, because Codex app-server publishes a symlink to its daemon
  socket. The app-server path must already be a socket. The TUI proxy socket
  must not exist yet. The bridge builds the Codex adapter in-process through a
  factory supplied by the CLI. The server does not import the adapter.
- The directory cannot be combined with a single `agent_provider` or
  `agent_runtime`. The shared bridge starts with 32 slots and 64 connections.
  Those are not the defaults for a plain `deskhelm bridge`.
- Default Alpha omits `preferred_slot` and uses session id `local-{digest12}`
  so two folders with the same name do not collide. `project_id` stays the
  folder name. Codex and TUI proxy sockets stay directory-scoped. They are
  never derived from `bridge.sock`.
- Alpha does not stop or unlink the user bridge after a successful attach.
  It detaches its runtime and removes only this directory's Codex sockets.
  A stale `bridge.sock` that refuses connections may be replaced. Two racing
  starters take a lock; the loser connects to the winner.
- The first `alpha --voice` starts the user bridge under the voice runtime,
  with no baked-in session. A later voice Alpha uses that bridge. If the
  bridge is already up without voice, Alpha fails and does not start a second
  voice stack. PTT still targets the focused session on that socket.
- Explicit `alpha --socket` keeps today's private bridge, slot 0, and
  `local-alpha` session id. The user launcher must not inject `--socket` on
  the default path, and it must not signal the user bridge's process group.
  Leftover Codex cleanup may signal only this directory's app-server.

## Consequences

An Alpha that is already running on a private socket stays there until that
process is restarted. Web must be restarted to read `bridge.sock`. Sessions
from agents DeskHelm did not start stay off the page.

Voice cannot be added to a bridge that was started without it. Stop that
bridge when no session still needs it, then start one Alpha with `--voice`.

An empty user bridge may keep running after the Alpha that started it exits.
That is what lets the next session, the page, and a later channel attach.

## Alternatives considered

Choosing a socket inside the web client was rejected. It leaves the dashboard
and every future channel with the same problem.

Proxying the old per-directory sockets into the user bridge was rejected for
this slice. Restart is the migration.

Reading arbitrary Codex transcripts was rejected. The control plane lists
sessions DeskHelm attached, not every thread on the machine.

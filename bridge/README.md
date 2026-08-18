# Bridge

Local-first service that normalizes coding-agent events for DeskHelm clients and
future devices.

## Internal Boundaries

- `StateStore` owns the current state projection and publishes snapshots.
- `SessionRegistry` maps agent, session, and project identity to display slots,
  tracks active and disconnected lifecycle state, and owns explicit focus.
- `SlotPanel` is a terminal subscriber and does not own Bridge state.

The current wire protocol still accepts the explicit `slot` required by
`AgentEvent v1`. Dynamic session allocation is an internal capability until a
future state protocol replaces that presentation mapping.

The server accepts legacy first-frame `AgentEvent v1` publishers and negotiated
`publisher` connections using `client_hello`, `server_hello`, and
self-describing lifecycle, state, or interaction frames. Publishers using
`adapter_session_v1` explicitly register complete sessions and declare runtime
capabilities before publishing owned events. It limits frames to 1 MiB and
handles 16 connections concurrently by default. Negotiated state and
interaction subscribers and `control_command_v1` controllers are enabled. See
[`protocol/local-transport-v1.md`](../protocol/local-transport-v1.md).

## Phase 0 Quickstart

The prototype requires Python 3.11 or newer and has no runtime dependencies.

Start the four-slot bridge:

```bash
PYTHONPATH=bridge python3 -m deskhelm_bridge bridge
```

Use `--max-connections` to lower or raise the bounded connection limit.
The default subscriber limit is shared by state and interaction subscribers and
is half the connection limit. Each subscriber has an 8-frame output queue.
Override these with `--max-subscribers` and `--subscriber-queue-frames`.

Control routing retains at most 1024 idempotency entries and 1024 approval
records by default. Override these with `--control-idempotency-entries`,
`--control-idempotency-retention-ms`, and `--control-approval-records`.
`focus` is handled internally; other command kinds reject with
`handler_unavailable` until an Agent or Voice Gateway registers a bounded
non-blocking handler.

Python integrations can use `deskhelm_bridge.send_negotiated_event` for a
single negotiated state event while the CLI remains a legacy compatibility
client.

In another terminal, run the demo sequence:

```bash
PYTHONPATH=bridge python3 -m deskhelm_bridge simulate
```

Send one event manually:

```bash
PYTHONPATH=bridge python3 -m deskhelm_bridge emit \
  --agent-id project-a:codex:1 \
  --slot 0 \
  --state waiting_approval \
  --label backend
```

For an editable CLI installation:

```bash
python3 -m pip install -e .
deskhelm bridge
```

The default socket is `$XDG_RUNTIME_DIR/deskhelm/bridge.sock`, or
`/tmp/deskhelm-<uid>/bridge.sock` when `XDG_RUNTIME_DIR` is unavailable.

The pre-release `agent-io` CLI and `python -m agent_io_bridge` remain temporary
compatibility aliases. They use the DeskHelm socket path.

## Codex Hook

Install the editable CLI, start `deskhelm bridge`, then adapt
[`adapters/codex/hooks.example.json`](../adapters/codex/hooks.example.json) into
`~/.codex/hooks.json`. Each command receives a Codex hook payload on standard
input and forwards its lifecycle state to the configured slot.

## Tests

```bash
PYTHONPATH=bridge python3 -m unittest discover -s tests -v
```

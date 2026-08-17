# Bridge

Local-first service that normalizes coding-agent events for DeskHelm clients and
future devices.

## Internal Boundaries

- `StateStore` owns the current state projection and publishes snapshots.
- `SessionRegistry` maps agent, session, and project identity to display slots.
- `SlotPanel` is a terminal subscriber and does not own Bridge state.

The current wire protocol still accepts the explicit `slot` required by
`AgentEvent v1`. Dynamic session allocation is an internal capability until a
new interaction protocol is accepted.

## Phase 0 Quickstart

The prototype requires Python 3.11 or newer and has no runtime dependencies.

Start the four-slot bridge:

```bash
PYTHONPATH=bridge python3 -m deskhelm_bridge bridge
```

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

# ADR 0001: Phase 0 Uses Python and a Unix Socket

- Status: Accepted
- Date: 2026-07-16

Naming note: ADR 0003 replaces the `agent-io` CLI and runtime-path names with
DeskHelm equivalents while preserving temporary compatibility commands.

## Context

Phase 0 must validate the core `agent-io` experience before hardware exists: normalize agent events, accept events from a Codex hook, and display four live status slots. The prototype should run locally with minimal setup and expose protocol assumptions early.

## Decision

Implement the Phase 0 bridge and CLI in Python 3 using only the standard library.

- Transport: newline-delimited JSON over a local Unix domain stream socket.
- Default endpoint: `$XDG_RUNTIME_DIR/agent-io/bridge.sock`, falling back to `/tmp/agent-io-<uid>/bridge.sock`.
- Interface: one `agent-io` CLI with `bridge`, `emit`, and `simulate` commands.
- State model: four slots using the states defined in the research document.
- Codex integration: a hook adapter reads hook JSON from standard input and emits a normalized event.

## Consequences

The prototype has no runtime dependencies and is easy to test in CI. Unix sockets intentionally limit Phase 0 to Linux and macOS; Windows and device transports remain Phase 1 decisions. The wire event is versioned so the transport can later move to serial, Vendor HID, or a cross-platform local IPC mechanism without redefining agent state semantics.

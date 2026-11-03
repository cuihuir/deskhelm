# ADR 0043: Architecture v2: Control-Plane Bridge, Session Hosts, and Surfaces

Date: 2026-11-03

Status: Accepted. Implemented in phases; see Migration.

## Context

The Bridge process is both the control plane and the host of the data plane.
Adapters, the Codex TUI proxy, voice, and the terminal panel run inside it.
`docs/architecture/target-architecture.md` §2 records the evidence. Three
consequences matter most:

- Control routing calls handlers under the router lock, and handlers publish
  under `_event_lock`. The publish path takes the router lock while holding
  `_event_lock`. An approval request that arrives during a prompt submission
  can deadlock every session.
- Restarting or upgrading the Bridge stops every session. The stdio relay
  stops the vendor process on EOF, and the Codex launcher stops its TUI when
  the user bridge socket disappears.
- Session facts have no single owner. Pending approvals cannot be queried,
  the state plane is keyed by slot without a session identity, and each
  client rebuilds state with its own rules.

The project is pre-release, so the structure can still change at low cost.

## Decision

1. The Bridge is a pure control plane. It owns session facts, focus,
   routing, idempotency receipts, approval tickets, the decision policy, and
   projections. Its core is a single-threaded actor without locks or I/O. It
   imports no adapter, voice, or rendering code and stays standard-library
   only.
2. Each session runs in a session host process (`deskhelm codex`,
   `deskhelm agent <kind>`). The host owns the vendor process and the driver,
   and for Codex also the TUI proxy and the PTY. The host connection is the
   session lease. After a Bridge restart, hosts keep their vendor processes,
   reconnect, reopen their sessions, and report open requests again.
3. Web, the dashboard, the voice host, and a later device host are surfaces.
   They share one client runtime (`deskhelm_client`) and one console
   projection: a snapshot followed by cursor-ordered deltas that carry
   sessions, focus, pending approvals, and surface status. Voice runs in its
   own host process.
4. Drivers implement one contract and declare a capability matrix when the
   session opens. The Bridge enforces it and returns typed unsupported
   results. ACP agents share one generic driver with small flavors; Grok is
   the first flavor.
5. Hosts report facts that separate content-free `meta` from operator-only
   `content`. State derives only from `meta`. Privacy is filtered per tier on
   output instead of by splitting input planes.
6. The Bridge generates approval and turn ids. Vendor ids are session-scoped
   references used only by hosts. Approval commands name the approval id.
7. An approval is consumed when its dispatch was accepted or may have been
   delivered. A dispatch that definitely did not reach a runtime leaves the
   request pending and returns a fixed code.
8. Rich content (D1). The Bridge keeps a bounded, in-memory, per-session
   window of recent interaction events. The window is never written to disk,
   is not canonical, and is dropped when the session ends. The operator tier
   may show bounded command lines, command output, and diffs. The device and
   voice tiers and logs never receive them.
9. Voice approval (D3). Exact phrases remain, but only when the target
   session has exactly one answerable approval. An approval phrase never
   becomes a prompt. Spoken `取消` discards the utterance and never rejects.
   Once hardware exists, the default becomes voice nomination plus a
   hardware hold.
10. Legacy removal (D4). `AgentEvent v1` and the slot state plane,
    `codex_hook`, `emit`, the codex exec gateway with the project registry,
    and the `agent-io` aliases with `agent_io_bridge` are removed after their
    replacements land. `simulate` becomes a fake session host.
11. Every web request is authenticated. A startup token becomes an HttpOnly,
    SameSite=Strict cookie. Every request must name an allowed Host. POST
    requests need a JSON body and, when the browser sends one, an allowed
    Origin.

## Migration

| Phase | Scope |
|---|---|
| 0 | Safety fixes on the current code: lock order, undelivered approvals, Grok approval options, titles, and cancel, dashboard summaries, web authentication, voice phrases, runtime-neutral notifications, and a replay test harness. |
| 1 | Protocol v2 and the single-threaded core beside the current protocol. |
| 2 | Session hosts, the adapter kit, generic ACP, Claude, and the Codex host. |
| 3 | `deskhelm_client`, the migrated surfaces, and the voice host. |
| 4 | Legacy removal, consolidated documentation, and protocol v2 docs. |
| 5 | Hardware and remote-access ADRs. |

Phases 1 to 3 may run the old and new protocols side by side. Phase 4
removes the old protocol.

## Superseded and Amended Decisions

- Superseded: slot assignment at registration (ADR 0002, 0004); the lease
  plus attach pair (ADR 0007); in-process runtime factories, attach frames,
  and the runtime directory (ADR 0038); relay sockets and relay runtime kinds
  (ADR 0040); the codex exec gateway and project registry (ADR 0008, 0031).
- Amended: two connection roles, a console snapshot with a cursor, and a
  bounded interaction window (ADR 0005); approval ids and delivery-aware
  consumption (ADR 0006); a voice host process with public content-free voice
  status (ADR 0009); PTT cancel (ADR 0017); the TUI proxy inside the Codex
  host (ADR 0033); a separate web process with the window in the Bridge and
  authentication (ADR 0036); echo correlation by command id (ADR 0042).
- Superseded ADRs keep their text. Their status lines change when the
  replacing phase lands. Each phase updates AGENTS.md for the rules it
  changes, and phase 4 condenses the AGENTS.md architecture rules into
  invariants.

## Consequences

- A Bridge restart no longer stops agent processes or the Codex TUI.
- An adapter change takes effect when its session restarts.
- The Bridge loses its voice and Codex flags, and voice can attach to a
  running Bridge.
- Clients see pending approvals after reconnecting.
- Two protocols coexist during phases 1 to 3. That cost ends in phase 4.

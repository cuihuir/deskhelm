# ADR 0035: Bridge-Native TUI Dashboard

Date: 2026-09-21

Status: Accepted for staged implementation

## Context

DeskHelm already exposes the information and controls needed by a multi-session
operator interface, but its terminal presentation is still the Phase 0
`SlotPanel`. That panel is rendered inside the Bridge process, shows only the
small state projection, and is not an interactive client.

A useful Agent Console needs a local overview that answers three questions
without replacing an Agent's own terminal UI:

- Which sessions are active, idle, complete, failed, or waiting for a person?
- Which complete session is explicitly focused for PTT and physical controls?
- Which safe action can be applied to the selected session now?

Terminal dashboards such as captain-miao demonstrate the value of a managed
multi-session overview, attach/detach workflows, and durable PTY ownership.
DeskHelm must preserve different boundaries: Bridge is the authority for
normalized session state and targeted controls; rich interaction content is
live-only; Agent runtimes own their canonical conversation history; and the
Dashboard must not scrape terminal output or treat transcript files as state.

## Decision

DeskHelm will add a TUI Dashboard as an optional, replaceable Bridge client.
It is not embedded into Bridge state management and does not become a terminal
emulator, Agent runtime, transcript store, or session supervisor.

```text
Agent runtimes
  -> adapters
  -> Bridge
       |- state subscription ---------> Dashboard session list
       |- interaction subscription ---> Dashboard live detail/attention
       `- control results <-----------> Dashboard controller

Managed Agent TUI <---- separate launcher/proxy/supervisor boundary
```

The Dashboard uses three separate negotiated connections because the existing
transport gives each connection one fixed role and each subscriber selects one
event plane:

- `state_subscription_v1` supplies the sequence-zero snapshot and ordered live
  operational state.
- `interaction_subscription_v1` supplies bounded live-only rich events after
  the Dashboard connects.
- `control_command_v1` supplies explicit targeted controls and correlated
  results plus the read-only `session_query` metadata request.

The Dashboard must consume the public local protocol exactly as another client
would. It must not import or mutate `StateStore`, `SessionRegistry`, or
`ControlRouter` internals.

### Interaction Model

The Dashboard keeps these concepts distinct:

- **cursor**: the row currently highlighted for inspection;
- **selected session**: the complete session identity represented by that row;
- **focused session**: the Bridge-authoritative target for PTT and physical
  controls;
- **attention**: a notification that a session may need a person, never an
  implicit focus change.

Moving the cursor or opening a detail pane has no control side effect. Focus
changes only after an explicit operator action that sends a targeted `focus`
command. The Dashboard renders the focused marker from Bridge state/query
rather than assuming that a locally highlighted row is focused.

The initial layout is responsive rather than tied to a fixed slot count:

```text
+ Sessions ----------------+ Selected session -------------------------+
| > backend   thinking     | project   deskhelm                        |
| * docs      needs input  | runtime   codex                           |
|   tests     complete     | state     waiting_user                    |
|   api       disconnected | recent    live events since connection    |
+--------------------------+--------------------------------------------+
| focus  interrupt  approve/reject when correlated  help  quit        |
+-----------------------------------------------------------------------+
```

`>` denotes the cursor and `*` denotes Bridge focus; they may appear on
different rows. Narrow terminals show the session list first and open the
selected detail as a separate view. State changes update rows in place and do
not automatically reorder the list under the cursor. An explicit attention
filter may group sessions that need action.

### Initial Scope

The first implementation will provide:

- a multi-session list built from the atomic state snapshot and live updates;
- complete session identity, adapter/runtime metadata when available, active
  or disconnected lifecycle, state, label, progress, and explicit focus;
- an in-memory, bounded detail view of interaction events observed after the
  Dashboard connected;
- explicit focus and interrupt controls;
- approval and rejection of one visible, correlated, non-expired pending
  request; the Dashboard copies the exact request metadata and delegates race,
  idempotency, and downstream delivery behavior to `ControlRouter`;
- sequence-gap, Bridge restart, disconnect, slow-subscriber, and stale-control
  feedback without reconstructing missing history;
- keyboard help and terminal resize handling.

Browsing, filtering, and viewing details are always read-only. Consequential
controls require an explicit key action and a stable complete target. The
Dashboard never targets by display slot and never turns status-derived
`attention` into an approval request.

### Session Opening and Attachment

The Dashboard does not initially implement generic `attach`, `detach`,
`resume`, restart, or termination. The current managed Codex TUI remains a
separate process connected through the existing approval-aware proxy.

A later session supervisor may expose bounded launcher and PTY operations.
When that exists, the Dashboard may request an attach action through a
versioned capability. It must not inject terminal keystrokes, adopt an
unrelated process, infer a working directory from `project_id`, or make the
Dashboard process the owner of the Agent session.

Closing the Dashboard disconnects only its client connections. It never
releases or terminates a session.

### Privacy, Persistence, and Bounds

- The Dashboard does not read Agent transcript files or terminal scrollback.
- It does not persist prompts, responses, tool arguments, approval content, or
  interaction events by default.
- Rich events are retained only in a bounded in-memory per-session view and
  are discarded on exit.
- A new interaction subscription starts with an explicit "live from now"
  marker; the UI must not imply that earlier conversation history is loaded.
- State recovery uses a fresh snapshot. Missing rich events remain missing.
- All rendered strings, rows, events, retained bytes, and control records are
  bounded. Oversized or malformed content fails visibly without crashing the
  Dashboard or blocking Bridge publishers.

### Packaging Boundary

The dependency-minimal Bridge remains usable without the Dashboard. The TUI
may use a dedicated client dependency later, but that dependency must stay out
of the Bridge runtime and requires an implementation decision after a small
prototype compares terminal behavior, accessibility, packaging, and resize
support. The existing `SlotPanel` remains a diagnostic/compatibility output
until the standalone Dashboard covers its operational use cases.

Remote transport, durable session history, PTY pooling, and cross-host
credential forwarding are not part of this decision. They require separate
ADRs and must not weaken the local protocol, project registry, or secret
boundaries.

## Consequences

- DeskHelm gains a product-level multi-session interface without coupling the
  Bridge to one UI framework.
- Existing state, interaction, control, ownership, privacy, and focus rules are
  reused rather than replaced with filesystem polling or transcript scraping.
- Merely looking at an attention event cannot redirect PTT or hardware input.
- The first Dashboard can observe and safely control already registered
  sessions, but it cannot yet keep Agent PTYs alive or attach to arbitrary
  terminals.
- A shared multi-project Bridge becomes the natural backend for one Dashboard;
  separate per-directory Alpha Bridges remain separate until an explicit
  broker or supervisor is designed. ADR 0038 is that broker for default
  Alpha: one user bridge, with explicit `--socket` still private.
- Session supervision, attach/detach, remote operation, and durable history
  remain explicit later phases instead of hidden responsibilities of the UI.

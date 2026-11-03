# ADR 0034: Focused PTT Routing

Date: 2026-09-04

Status: Accepted

## Context

The right-Alt controller previously bound one complete session target when it
started. With more than one open session, separate listeners could dispatch the
same physical key edge to different targets. Device exclusivity prevents that
broadcast, but it does not express which session the operator is currently
using.

DeskHelm already has an explicit `focused_session` pointer. Human-attention
events must not silently move that pointer: an approval or input request may
notify the operator while another session remains selected.

## Decision

- PTT authorization is determined by the Bridge's current
  `focused_session` at press time. A press with no focused session is rejected
  without starting capture or submitting a prompt.
- The press command must still contain the complete resolved target identity.
  The Bridge rejects a `press_ptt` command whose target is not currently
  focused.
- Once accepted, a PTT activation retains its resolved target through release.
  Changing focus during capture does not retarget or invalidate that release.
- Approval, `attention`, and `user_input_required` events notify only. They do
  not change focus or authorize PTT for their target.
- A UI changes focus by issuing the existing targeted `focus` control. The
  `deskhelm focus` command is the reference terminal client for that action.
- Superseded in part by ADR 0039: one listener still owns PTT, through a
  flock rather than an evdev grab. The focused target is resolved through the
  owning Bridge rather than by broadcasting to every open session.

## Consequences

- Multiple sessions can coexist safely: only the explicitly focused active
  session receives a PTT prompt.
- Operators must focus a session before using PTT; no-focus and stale-focus
  cases fail closed.
- A PTT client needs a bounded read-only focus query before constructing a
  targeted press command. Existing targeted control framing remains intact.
- The current per-directory Alpha topology still gives each Alpha its own
  Bridge-local focus. Cross-Alpha focus switching requires a shared Bridge or a
  future user-level focus broker and is not inferred from terminal visibility.
  ADR 0038 makes the default one user bridge, so focus is shared by every
  attached session. Explicit `--socket` keeps the private bridge and its
  local focus.

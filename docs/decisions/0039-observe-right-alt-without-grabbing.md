# ADR 0039: Observe Right Alt Without Grabbing the Keyboard

Date: 2026-10-21

Status: Accepted

## Context

ADR 0027 armed push-to-talk by taking an exclusive evdev grab on
`/dev/input/deskhelm-ptt-kbd`. The udev rule points that path at keyd's
virtual keyboard. On this machine that node is the keyboard the desktop
actually reads. `EVIOCGRAB` is all-or-nothing: the listener then drops every
event that is not right Alt, so those keys never reach the compositor.

Starting `deskhelm alpha --tui --yolo --voice` therefore made the whole
keyboard unusable. Recovery required the on-screen keyboard. The product
rule is narrower: DeskHelm may watch right Alt for voice, and the rest of
the keyboard must keep working.

ADR 0034 still needs one listener. Two voice Alphas must not dispatch the
same key edge to two sessions. That exclusion does not require consuming
the device.

## Decision

- The right-Alt listener opens the configured evdev device and reads it. It
  does not call `EVIOCGRAB` or `device.grab()`.
- Only `EV_KEY` / `KEY_RIGHTALT` press (`1`) and release (`0`) are forwarded
  to the existing controller. Other events are ignored by DeskHelm and remain
  available to the desktop.
- One listener is enforced with a non-blocking exclusive flock on
  `deskhelm_runtime_directory() / "ptt.lock"`. A second listener fails closed
  with the existing fixed error and closes the device.
- Right Alt still reaches the compositor. Its possible AltGr effect is
  accepted.
- DeskHelm still does not configure keyd, does not open the physical
  keyboard beside keyd, and does not inject or rewrite keys.

This supersedes the exclusive-grab bullets in ADR 0027 and ADR 0034. Press,
release, focus routing, and the one-owner failure stay as those ADRs define
them.

## Consequences

- A voice Alpha can run while the operator keeps typing.
- A second voice Alpha on the same machine still cannot arm another listener.
- Tests must not require `grab` or `ungrab`, and they must not open a real
  input device.
- The listener no longer hides keys, so it also no longer prevents another
  process from seeing right Alt. The flock is the only single-owner control.

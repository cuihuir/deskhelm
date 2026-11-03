# ADR 0027: Right-Alt PTT and IME Scope

Date: 2026-08-21

Status: Accepted. The exclusive evdev grab is superseded by ADR 0039. Since
ADR 0043 the voice host runs the right-Alt listener.

## Context

DeskHelm needs a physical push-to-talk gesture for local voice interaction. The
user selected the right `Alt` key and does not want a GUI in the initial path.
On GNOME/Wayland, a raw global key listener cannot reliably determine whether
another application's text field is active; right `Alt` can also act as AltGr.

## Decision

- The initial controller is an explicit foreground terminal PTT mode. It binds
  one complete active session target at startup, then observes right `Alt` only
  while that mode is armed. It has no GUI or text-field detection.
- Superseded by ADR 0039: the controller does not take an exclusive evdev
  grab. One listener holds a flock, and a second listener fails closed. The
  desktop keeps receiving every key, including right Alt.
- The controller never changes focus. It fails closed when its configured
  complete target is not active.
- Press and release retain one generated activation ID and use the existing
  complete-target PTT semantics. The controller observes key events only; it
  does not remap, consume, or inject keyboard input.
- DeskHelm does not configure `keyd`. When `keyd` is present, it may retain its
  existing left-`Alt` application mappings and leave right `Alt` unmodified.
  DeskHelm then observes the right-`Alt` events forwarded by `keyd`'s virtual
  keyboard through a stable, permission-controlled device path. It does not
  open the physical keyboard concurrently with `keyd`.
- A future DeskHelm microcontroller button uses this same press/release
  controller contract. Its LEDs receive Voice lifecycle state as output only;
  LED state never authorizes capture or changes the selected target.
- General voice dictation into arbitrary Wayland application text fields is a
  later IBus/Fcitx input-method integration. It must deliver text through the
  input-method context, not a global raw-key listener or clipboard fallback.

## Consequences

- The first physical PTT path avoids persistent global AltGr collisions and
  accidental microphone activation while typing elsewhere.
- Only one DeskHelm PTT listener runs at a time. Ownership is a flock, not a
  device grab. Opening another voice-enabled Alpha on that device must be
  rejected rather than broadcast input across sessions. The keyboard remains
  usable because the listener does not consume events.
- Existing `keyd` mappings remain outside DeskHelm's scope. A missing or
  unavailable virtual keyboard device fails explicitly rather than falling
  back to another keyboard or modifying the user's mapping.
- The terminal controller requires explicit target configuration. A later
  hardware controller replaces its key source, not its target, control, or
  safety semantics.
- IME support becomes a separate desktop integration with its own permission,
  privacy, and text-insertion design.

# ADR 0017: Correlate External PTT Press and Release

Date: 2026-08-19

Status: Accepted

## Context

The Voice Gateway already supports in-process push-to-talk, but controllers on
the DeskHelm socket cannot start or release capture. A release identified only
by session is unsafe: a delayed release from an earlier PTT cycle could stop a
new capture for the same session, while a release without a target could stop
another session's capture.

PTT controls also cross a transport boundary where duplicate delivery and
ambiguous disconnects are possible. The existing control router provides
bounded idempotency, but the Voice Gateway must still verify ownership of the
active PTT cycle.

## Decision

Extend `ControlCommand v1` with `press_ptt` and `release_ptt`.

- Both commands retain the complete `agent_id + session_id + project_id`
  target, issuer, expiry, and idempotency identity required by every control.
- `press_ptt` has an empty payload. Its `command_id` becomes the activation ID
  for the capture it starts.
- `release_ptt` requires `payload.press_command_id`, copied exactly from the
  matching press command.
- A release succeeds only when both its complete target and
  `press_command_id` own the active capture.
- An idle, cross-session, or stale release fails dispatch and does not alter
  the current PTT state.
- Exact command retries use the existing router deduplication result. Clients
  must not automatically replay a new press or release after an ambiguous
  outcome.
- Keep `cancel_ptt` internal until cancellation semantics and authorization are
  defined separately.

## Consequences

- Desktop and future physical controllers can drive the existing PTT path over
  the negotiated control plane without routing by display slot.
- A late release cannot terminate a newer PTT cycle, including one for the same
  session.
- Controllers must retain the press command ID until they send its release.
- PTT dispatch still requires an explicitly composed Voice Gateway; otherwise
  the router returns `handler_unavailable`.
- This decision does not activate PipeWire, VAD, ASR, or TTS providers in the
  Bridge CLI.

## Alternatives

- Use an empty release payload: rejected because session identity alone cannot
  distinguish consecutive PTT cycles for the same session.
- Release whichever capture is active: rejected because it permits
  cross-session interruption.
- Model PTT as a single toggle command: rejected because lost or duplicate
  delivery can invert controller and gateway state.
- Expose cancellation with release: rejected because normal completion and
  discarded capture have different user-visible semantics.

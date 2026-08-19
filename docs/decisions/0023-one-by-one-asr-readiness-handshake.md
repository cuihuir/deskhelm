# ADR 0023: Add One-by-One ASR Phrase Readiness Handshakes

- Status: Accepted
- Date: 2026-08-19

## Context

The bounded multi-phrase diagnostic correctly opened a fresh capture for every
phrase, but it printed the next prompt as soon as the previous provider call
completed. In chat-driven operation, a person can miss that transition even
when the capture window itself is long enough. The resulting signal and ASR
metrics are useful diagnostics but are difficult to interpret as speech-timing
evidence.

## Decision

- Preserve the existing immediate-capture mode for scripts and direct terminal
  use.
- Add an opt-in `--await-phrase-ready` mode. Before each capture, print the
  public phrase and wait for an exact `ready` line on stdin.
- Bound each readiness wait to 1-120 seconds, use a fixed
  `voice_phrase_not_ready` error on timeout or EOF, and never capture audio
  without the handshake in this mode.
- Report `phrase_ready_mode` as `immediate` or `chat_handshake` in the
  privacy-safe result. Do not print or retain stdin contents beyond the fixed
  readiness token.
- Keep readiness separate from VAD and ASR. It only establishes capture start;
  PTT/VAD boundaries and provider behavior remain unchanged.

## Consequences

- A chat operator can acknowledge each phrase after reading the prompt, so
  natural speech does not race automatic transitions.
- The handshake mode is interactive and must not be used by unattended jobs
  without supplying bounded `ready` lines.
- Results from handshake and immediate modes remain distinct observations and
  are not paired recordings.

## Implementation Status

The CLI flag, bounded stdin wait, fixed failure record, parent/per-phrase mode
metadata, and focused privacy tests are implemented. A live one-by-one
confirmation run remains pending.

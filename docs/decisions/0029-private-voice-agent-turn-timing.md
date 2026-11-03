# ADR 0029: Private Voice-Agent Turn Timing

Date: 2026-08-24

Status: Accepted. DeskHelm 0.1.0 removed this timing record with the v1 Bridge
(ADR 0043); protocol v2 has no equivalent yet.

## Context

The local voice alpha has measured ASR, TTS, and PipeWire handoff stages. The
user has explicitly deferred acoustic loopback measurement. The remaining
useful latency question is the bounded local Agent turn between a Voice
transcript dispatch and the resulting speech handoff.

`InteractionEvent v1` can contain private message content and provider-specific
correlation identifiers. Publishing a new cross-event identifier or deriving
timing from interaction subscriber traffic would broaden that private-content
plane and make unrelated replies easy to associate.

## Decision

- Do not open a microphone, play a probe, capture loopback audio, or report
  `speaker_first_audio` for this phase.
- Add an explicit opt-in, process-local turn timer at the Bridge composition
  boundary. It receives only one target, a generated turn identifier, monotonic
  timestamps, and terminal status; it never receives transcript, prompt,
  assistant-message, tool, or provider output text.
- Correlate a Voice-originated `submit_prompt` command with the Agent run using
  its command identifier internally. The timer associates the fixed completion
  notification with that turn after the matching Agent terminal event.
- Bound retained records and emit one numeric-only result per terminal turn.
  Include release and post-dispatch timing for notification readiness,
  speech/TTS start, complete TTS audio, PipeWire first write, and Agent terminal.
  Also report the adjacent notification-to-TTS-start, TTS-generation, and
  audio-ready-to-first-write stages. Report missing stages explicitly rather
  than inferring them.
- Keep timing output local to the explicit diagnostic/controller. Do not extend
  `InteractionEvent v1`, state projection, hardware state, ordinary logs, or
  durable history. A future negotiated metrics protocol needs its own ADR.

## Consequences

- Live measurements can separate Agent turnaround, speech queue handoff, TTS
  generation, and PipeWire handoff without storing or disclosing user content.
- It measures a bounded software path, not model reasoning quality or acoustic
  speaker latency.
- Non-Voice prompts, multiple unrelated speech items, provider failures, and
  cancellation remain distinguishable terminal outcomes rather than being
  folded into a successful latency value.

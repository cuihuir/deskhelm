# ADR 0030: Notification-Gated Agent Speech

Date: 2026-08-24

Status: Accepted

## Context

The local voice alpha initially spoke every complete assistant message as soon
as the Agent adapter published it. A Codex turn may publish more than one such
message before its terminal event. Pressing PTT correctly cancels only current
playback and does not cancel the Agent, so a later message from the same turn
could begin speaking again. A follow-up voice prompt also cannot start while
the bounded Agent gateway still owns an active run for that session.

Voice is intended to bring a person back into the loop, not narrate internal or
intermediate Agent progress.

## Decision

- Supersede ADR 0009's automatic assistant-message speech routing for the local
  notification path; explicit targeted `speak` controls remain available.
- Do not speak assistant messages, tool events, or other progress while an
  Agent turn is active.
- Never retain or speak assistant response content. After `task_completed`,
  speak only a fixed, content-free completion notification.
- Speak fixed, content-free notifications for `task_failed`,
  `approval_request`, active `attention`, and `user_input_required`, because
  each requires human attention. Do not speak approval summaries, input
  prompts, tool output, or failure details.
- Treat status-derived approval attention as notification-only. It must not
  create a pending approval. Deduplicate it with an actionable
  `approval_request` for the same target until the attention state clears.
- Keep PTT interruption scoped to current interruptible playback. It does not
  cancel Agent work. Because ordinary completion speech begins only after the
  Agent terminal, a new PTT turn can start a new Agent run after interruption.
- Keep explicit `speak` controls unchanged; callers remain responsible for
  their declared priority and interruptibility.

## Consequences

- Codex messages remain available to rich interaction clients but never cause
  speech, including after the turn reaches a terminal state.
- The completion notification begins only after the Agent terminal, avoiding a
  busy-session barge-in dead end without exposing response content to TTS.
- Human-attention notifications reveal only their fixed category, not private
  interaction content.
- No assistant response text enters the notification speech queue or its
  process-local state.

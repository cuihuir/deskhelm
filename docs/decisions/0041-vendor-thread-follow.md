# ADR 0041: Start a Turn When Steer Has No Active Turn

Date: 2026-10-22

Status: Accepted

## Context

DeskHelm sends the next prompt to an attached Codex thread with `turn/steer`
while it still remembers a turn id. After that turn has finished, Codex
rejects the steer because no turn is active. The prompt never reaches the
thread, and the page shows `codex_control_failed`.

The page already renders live interaction events for Codex, Claude, and Grok.
That pipe is the output path. A second transcript protocol, a polled vendor
history, or a burst of old items would sit beside it and can overflow the
8-frame subscriber queue.

## Decision

When `turn/steer` fails and the error message contains `no active turn`, the
Codex adapter clears the remembered turn and queues one `turn/start` with the
same prompt. A stored model or effort override is included, because this call
is a new turn. The adapter does not loop.

A review steer, a compact steer, and any other steer error still publish
`codex_control_failed` and do not start a turn.

No new controller frame, capability, or page channel is added. Rollout files
are not read. The interaction subscription stays live-only.

## Consequences

The running Codex adapter keeps the old steer behavior until that DeskHelm
session is restarted. This ADR does not restart it.

Codex remains the canonical history. Output continues to arrive as the
existing interaction events for whichever agent DeskHelm started.

## Alternatives considered

Loading a vendor thread window, on a new query or by publishing it into the
interaction stream, was rejected for this change. It duplicates the output
pipe and is heavier than getting the next prompt onto the attached thread.
Cloning DeepSeek Harness was rejected for the same reason.

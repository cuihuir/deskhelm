# ADR 0042: One User Message per Submitted Prompt

Date: 2026-10-22

Status: Accepted

## Context

The page builds each visible message from interaction events that share one
`correlation_id`. Claude and Grok published every DeskHelm prompt with the
fixed id `user-prompt`, so a later prompt replaced the earlier one. Codex
published no user message when it accepted a prompt. The page kept an
optimistic copy, and that copy stayed when Codex never echoed the text.

The interaction plane is still the only output path. This does not add a
transcript query, a poll, or a vendor-history reader.

## Decision

- Codex, Claude, and Grok publish a prompt once, as a complete user message,
  when DeskHelm accepts it. The correlation id is that control's command id.
- A later vendor item with the same text is not published again. Codex
  remembers the last eight accepted texts and consumes one match. Claude and
  Grok remember the latest accepted text and consume one match.
- A vendor user item with different text keeps its own id and is published.
- An idle-steer retry uses the message already published for that command.
  It does not publish a second copy.

## Consequences

A running adapter keeps the old behavior until that DeskHelm session is
restarted. The page shows each accepted prompt on the existing interaction
stream. Text from before the adapter published it is still absent.

## Alternatives considered

Loading the vendor thread window was left out. It would add a second read
beside the interaction stream. Keeping the fixed `user-prompt` id was
rejected because the page can then show only the latest prompt.

# ADR 0008: Use a Bounded In-Process Text Agent Gateway

- Status: Accepted
- Date: 2026-08-18

## Context

DeskHelm needs to turn targeted `submit_prompt` and `interrupt` controls into
Agent runtime work before voice or hardware exists. The Bridge must remain
Agent-agnostic and dependency-minimal, while vendor JSON, process behavior, and
authentication remain adapter concerns.

The first provider is `codex exec --json`. Official OpenAI documentation states
that this mode emits JSONL events including `thread.started`, `turn.*`,
`item.*`, and `error`, and that a prior session can be continued with
`codex exec resume <SESSION_ID>`.

## Decision

Add a generic in-process `AgentGateway` to the Bridge composition boundary. It
owns:

- explicit `submit_prompt` and `interrupt` control handlers;
- at most one active run per complete DeskHelm session;
- a configured maximum number of simultaneous runs;
- a bounded set of process-local provider session IDs and event sequences;
- normalization of provider events into `InteractionEvent v1`;
- exactly one task-completed or task-failed terminal event per accepted run.

The gateway has no unbounded pending queue. A command is reported as
`dispatched` only after the gateway reserves bounded capacity and submits the
run to its fixed worker pool. Capacity exhaustion, duplicate active work for a
session, or interruption without an active run fails dispatch safely.

Provider adapters implement a synchronous, cancellable run contract. The
Codex adapter owns all Codex-specific command construction and JSONL parsing.
It:

- passes prompts through stdin so private text does not appear in process
  command-line arguments;
- captures JSONL from stdout while discarding stderr rather than logging it;
- limits each JSONL record to 1 MiB;
- maps supported assistant-message and command-execution items;
- ignores unknown well-formed event types for forward compatibility;
- treats malformed output, missing terminal events, unsuccessful process exit,
  and startup failure as terminal failures;
- terminates the owned process group on cancellation or timeout;
- resumes with the bounded provider session ID learned from `thread.started`.

The provider is disabled by default. It is enabled explicitly with
`--agent-provider codex`, a configured working directory, timeout, concurrency
bound, and Codex sandbox. The default Codex sandbox is `read-only`.

Version 1 supports prompt submission and interruption only. Approval decisions
are not inferred or automatically handled through `codex exec`.

## Consequences

- A local controller can target a registered session and stream normalized
  text results to interaction subscribers.
- Tests use a deterministic fake provider and fake subprocess; they require no
  network, login, API key, or live model invocation.
- `project_id` remains an identity, not a filesystem path. The first gateway
  uses one explicit working directory configured for the Bridge process.
- Provider session IDs and sequences are process-local and bounded; they are
  not durable history.
- A `dispatched` control result means bounded work was accepted, not that the
  Agent completed successfully.

## Implementation Status

The generic gateway, fake provider, Codex JSONL mapper and subprocess provider,
CLI composition, prompt/interrupt handlers, cancellation, timeout, resume, and
socket-level deterministic test are implemented.

Official source: <https://learn.chatgpt.com/docs/non-interactive-mode>

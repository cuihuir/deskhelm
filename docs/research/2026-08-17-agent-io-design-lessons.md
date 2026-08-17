# Agent I/O Projects: Design Lessons for DeskHelm

Date: 2026-08-17

Status: Research input, not a protocol decision

## Scope

This review compares two public projects that use the `agent-io` name:

- `carlrannaberg/agent-io`: a TypeScript toolkit that normalizes multiple Agent
  CLI output formats into a streaming event model.
- `lispking/agent-io`: a Rust SDK that provides a multi-provider Agent runtime,
  tool execution, streaming events, usage tracking, memory, and context
  compaction.

DeskHelm has a different responsibility: it coordinates existing coding agents
and exposes their state and controls to voice, TUI, desktop, and physical
surfaces. It should borrow interface and reliability ideas without becoming a
new LLM or Agent runtime.

## Lessons to Adopt

### 1. Normalize at the Adapter Boundary

Vendor JSONL, hooks, and app-server messages should be parsed inside adapters.
Bridge consumers should receive versioned DeskHelm events rather than
vendor-specific objects.

Each adapter should declare capabilities instead of making the Bridge infer
them:

- streaming output
- tool lifecycle events
- approval requests
- interruption and cancellation
- session resume
- usage reporting
- structured errors

Automatic format detection may be a fallback for imported logs, but live
sessions should identify their adapter explicitly. Detecting a vendor from the
first line is fragile when streams begin with banners, warnings, or partial
records.

### 2. Treat Streams as Long-Lived and Bounded

The TypeScript project correctly treats parsing as an incremental pipeline and
handles partial lines, parser flushing, malformed input, and maximum line
length. DeskHelm needs the same properties for Agent and voice streams:

- bounded record and queue sizes
- ordered events per session
- explicit backpressure or drop policy
- cancellation propagation
- end-of-stream flushing
- recovery after malformed records
- an error budget that prevents infinite failure loops

Collecting an entire stream into memory should remain a testing convenience,
not a production code path.

### 3. Use Correlated Lifecycle Events

The Rust SDK distinguishes text deltas, final responses, tool calls, tool
results, message boundaries, step boundaries, errors, and usage. DeskHelm's
future `InteractionEvent` should preserve equivalent lifecycle information when
the source provides it.

Before `InteractionEvent v1` is accepted, evaluate these envelope fields:

- `event_id`
- `agent_id`, `session_id`, and `project_id`
- `source` and `source_version`
- `sequence`
- `occurred_at`
- `correlation_id`
- `request_id` for approvals and controls

Tool calls and results must share a correlation identifier. Text deltas must be
distinguishable from complete text. A final response must not be inferred only
from process exit.

### 4. Keep Projection, Rich Interaction, and Control Separate

A single event union is convenient for a formatter or an Agent SDK, but it is
too broad for DeskHelm's consumers. The existing three-plane direction remains
correct:

- `StateEvent`: small state projection for panels and devices
- `InteractionEvent`: rich session content for voice, TUI, and desktop clients
- `ControlCommand`: targeted consequential actions

This separation prevents text, tool arguments, reasoning, and debug payloads
from reaching hardware by accident.

### 5. Build Compatibility from Real Fixtures

The strongest idea in the TypeScript project is fixture-based schema discovery.
DeskHelm should capture representative output from each supported Agent CLI and
record the producing CLI version with every fixture set.

Required fixture categories include:

- basic message and final response
- tool start, output, failure, and completion
- approval request and response
- cancellation and process exit
- malformed and unknown events
- Unicode, large records, and partial JSONL chunks
- session resume and version changes

Synthetic fixtures are still useful for boundary cases, but they cannot replace
captured output. The reviewed project currently has incomplete non-Claude
fixtures, illustrating how easy it is for a claimed compatibility matrix to
outpace actual evidence.

### 6. Make Heavy Features Optional

The Rust SDK uses provider and persistence feature flags. DeskHelm should apply
the same principle at process and package boundaries:

- Bridge remains lightweight and dependency-minimal.
- Agent adapters are independently selectable.
- Voice providers and model runtimes live outside Bridge.
- Persistence, metrics exporters, and future memory features are optional.

DeskHelm should not own the canonical conversation memory of an external Agent.
It may retain event history or recovery metadata, but the adapter must remain
the authority for the Agent session.

### 7. Design Observability with Privacy Boundaries

Structured spans for adapter invocation, Agent steps, tools, ASR, and TTS will
make recovery and latency analysis much easier. Every span should carry stable
session and correlation identifiers.

Raw events, tool arguments, prompts, paths, and model output may contain source
code or secrets. They must not enter ordinary logs by default. Debug capture
needs explicit enablement, local access controls, size limits, and redaction.

### 8. Separate Retry Policy from Control Semantics

Exponential backoff is useful for transient provider failures. It is unsafe as a
blanket policy for DeskHelm controls.

- Read-only subscription and idempotent discovery may retry automatically.
- Prompt submission requires an idempotency key before retry.
- Interrupt may be safely repeated when adapters define it as idempotent.
- Approval and rejection must never be replayed blindly.

## Risks to Avoid

- Building a universal Agent runtime instead of integrating existing agents.
- Claiming adapter support before real fixtures and compatibility tests exist.
- Allowing auto-detection to silently choose the wrong adapter.
- Sending unbounded JSONL records or audio queues through the Bridge.
- Treating process exit as the only completion signal.
- Mixing state projection, rich content, and control authorization.
- Logging raw Agent events by default.
- Adding persistence or model dependencies to the core before their ownership
  and failure behavior are defined.

## Proposed Design Gates

The following must be completed before the Voice Gateway depends on the rich
interaction protocol:

1. Define the adapter capability contract.
2. Define ordering, correlation, cancellation, and terminal-event semantics.
3. Add captured Codex fixtures with CLI version metadata.
4. Specify queue bounds, maximum record size, and slow-subscriber behavior.
5. Specify raw-event retention and logging redaction rules.
6. Add idempotency requirements to `ControlCommand`.
7. Add structured latency and recovery metrics without recording private
   content.

## Sources

- <https://github.com/carlrannaberg/agent-io>
- <https://github.com/carlrannaberg/agent-io/blob/main/packages/stream/src/stream.ts>
- <https://github.com/carlrannaberg/agent-io/blob/main/specs/testing-strategy.md>
- <https://github.com/carlrannaberg/agent-io/blob/main/fixtures/SCHEMA_ANALYSIS.md>
- <https://github.com/lispking/agent-io>
- <https://github.com/lispking/agent-io/blob/main/agent-io/src/agent/events.rs>
- <https://github.com/lispking/agent-io/blob/main/agent-io/src/llm/base.rs>
- <https://github.com/lispking/agent-io/blob/main/agent-io/src/agent/compaction.rs>
- <https://github.com/lispking/agent-io/blob/main/agent-io/src/observability.rs>

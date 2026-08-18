# Project Constitution

These principles govern all AI-assisted and agent-driven work in this
repository. When another instruction conflicts with this constitution, follow
the higher-priority user or system instruction and document the conflict when
it affects the result.

## 1. Use Visualizations Purposefully

When explaining complex workflows, architecture, dependencies, state changes,
or repeated comparisons, use the smallest effective visualization such as a
table, flowchart, timeline, or tree. Do not add diagrams to simple facts or
one-step operations merely for presentation.

## 2. Be Concise and Evidence-Based

Lead with conclusions and the evidence that materially supports them. Clearly
distinguish verified facts, reasonable inferences, and unknowns. Prefer project
source material, official documentation, and reliable primary sources. Never
present an unverified assumption as fact.

## 3. Stay Aligned With the User's Goal

Every analysis, recommendation, and change must serve the user's current
objective and respect the stated scope, constraints, and priorities. Do not
expand the task, alter the business goal, or add unrequested functionality for
technical completeness. When the goal conflicts with the current
implementation, explain the conflict and propose the smallest viable
resolution.

## 4. Ask Only for Critical Decisions

Do not interrupt the user when repository evidence or a reasonable low-risk
assumption allows progress. Ask for confirmation only when missing information
would materially change the result, expand scope, cause irreversible impact, or
require a key business decision. Briefly disclose consequential assumptions in
the result.

## 5. Use Subagents With Restraint

Use subagents only when work can be cleanly separated, parallel execution
materially improves efficiency, or an independent review or specialist
capability is valuable. Do not split simple work for appearance or assign
duplicate work to multiple agents. The primary agent owns integration, conflict
resolution, validation, and final delivery.

## 6. Keep Changes Focused

Limit code and documentation changes to the smallest scope required to complete
the current objective. Do not include unrelated refactors, formatting,
dependency upgrades, or directory changes. If broader work is necessary,
explain why and preserve existing interfaces, behavior, and user-owned work.

## 7. Validate Real Outcomes

Do not declare completion because code is present, reads correctly, or appears
finished. Run risk-appropriate builds, tests, execution checks, or output
inspections that verify the result the user actually needs. If validation
cannot be completed, state what remains unverified, why, and the residual risk.

## 8. Protect Existing Code and Data

Preserve existing code, uncommitted changes, configuration, and data. Never
overwrite, delete, revert, or reset user work without clear authorization.
Resolve destructive targets and impact before acting; ask when scope is
unclear. If unexpected changes appear during work, stop and notify the user.

## 9. Report Only Meaningful Progress

Report progress that affects user decisions: important findings, scope changes,
risks, validation results, and blockers. Do not stream mechanical operations,
repeat plans, or send status messages without new information. Final delivery
should emphasize outcomes, changed locations, validation, and necessary next
steps.

## 10. Keep AGENTS.md Current

Update `AGENTS.md` whenever project structure, critical commands, test
procedures, engineering constraints, or durable collaboration rules change.
Record only stable, reusable information that affects future work; do not add
temporary task history or one-off decisions. Check updates against the
repository and related documentation.

## 11. Maintain Handoff.md

After each identifiable phase of work, update the root `Handoff.md` so work can
stop and resume at any time. Record the current objective and status, completed
work, key decisions, changed files, validation results, remaining work, next
steps, risks, blockers, and essential context. Keep it as a concise statement
of current state rather than an append-only activity log. Never include
secrets, tokens, or sensitive credentials. Leave a short final state when a
task is complete.

# DeskHelm Repository Guidelines

## Project Identity and Scope

DeskHelm is a local-first control surface for coding agents. The GitHub
repository and product name are `DeskHelm` / `deskhelm`. The pre-release
`agent-io` commands and `agent_io_bridge` module execution entry point are
temporary compatibility aliases under ADR 0003; do not introduce new public
interfaces under the legacy names.

The project is a monorepo with these responsibility boundaries:

```text
Agent runtimes
  -> adapters
  -> Bridge: sessions, state, targeting, and control routing
     -> Voice Gateway: PTT, ASR, TTS, playback, and voice notifications
     -> TUI and desktop clients
     -> Physical Surface: HID, controls, RGB, displays, and device transport
```

DeskHelm integrates existing Agent runtimes. It must not become a new general
LLM or Agent framework, and it must not own an external Agent's canonical
conversation history.

## Project Structure

- `bridge/`: dependency-minimal local core service.
- `adapters/`: runtime-specific Codex, Claude Code, Gemini CLI, and other
  integrations.
- `protocol/`: versioned state, interaction, control, and transport contracts.
- `configurator/`: device and client setup application.
- `hardware/`: electronics, PCB, mechanical, production, and test assets.
- `firmware/`: device firmware and bootloader integration.
- `tests/`: cross-component, compatibility, and hardware-in-the-loop tests.
- `docs/`: research, architecture, product, software, hardware, and ADRs.
- `tools/`: development and manufacturing utilities.

Keep third-party reference files outside version control under
`references/vendor/`. Keep model weights and generated binaries outside Git.

## Architecture and Protocol Rules

- Keep integrations Agent-agnostic and local-first.
- Preserve the separation between small `StateEvent` projections, rich
  `InteractionEvent` content, and targeted `ControlCommand` actions.
- Keep `AgentEvent v1` compatible until a documented migration exists.
- Use the single DeskHelm Unix socket for negotiated publishers, subscribers,
  and controllers. A connection has one fixed role after
  `client_hello` / `server_hello` negotiation.
- Negotiated state publishers use the `agent_event_v1` capability and add
  `message_type: agent_event`; the remaining payload stays compatible with
  `AgentEvent v1`.
- Negotiated state subscribers use `state_subscription_v1`, receive an atomic
  sequence-zero snapshot, and then subscription-local ordered live updates.
- Limit legacy compatibility to connections whose first frame is an
  `AgentEvent v1`; legacy connections remain state publishers only.
- Limit UTF-8 NDJSON frames to 1 MiB and use bounded per-connection queues.
  A full subscriber queue is terminal; disconnect and require a fresh snapshot.
- Keep the subscriber limit below the total connection limit so long-lived
  readers cannot consume every publisher worker.
- Bound first-frame negotiation time so incomplete connections cannot retain
  workers indefinitely.
- Do not promise durable event history or replay. On reconnect or a sequence
  gap, clients request a fresh snapshot before consuming live events.
- Identify sessions with `agent_id + session_id + project_id`; treat `slot` as a
  presentation mapping only.
- Do not focus sessions implicitly. Only active sessions may be focused;
  disconnecting, replacing, releasing, or expiring the focused session clears
  focus, and restore does not re-focus automatically.
- Parse vendor formats at the adapter boundary and expose declared adapter
  capabilities.
- Bound streams, records, queues, retries, and slow-subscriber behavior.
- Correlate tool calls, results, approvals, controls, and terminal events with
  stable identifiers.
- Every control must name `agent_id + session_id + project_id`, `issued_by`, an
  issue time, an expiry, and an idempotency key. Never target controls by slot.
- Scope control idempotency by `issued_by + idempotency_key`; a retry preserves
  the complete command identity and content.
- Never replay approval or rejection blindly. Consequential controls must name
  their target session and copy the pending request ID, summary, and expiry
  exactly. Approval and rejection are never automatically retried.
- Do not log prompts, source code, tool arguments, raw Agent events, audio, or
  credentials by default.
- Keep PyTorch, CUDA, model weights, and provider-specific voice dependencies
  outside the core Bridge.

Record significant protocol, transport, concurrency, framework, MCU, or
licensing decisions in an ADR before implementation spreads across components.

## Build, Test, and Development Commands

The Phase 0 Bridge requires Python 3.11 or newer and has no runtime
dependencies. A repository-wide build system has not been selected; do not add
a placeholder task runner without an ADR.

Current commands:

- `PYTHONPATH=bridge python3 -m deskhelm_bridge bridge --plain`: run the Bridge.
- `PYTHONPATH=bridge python3 -m deskhelm_bridge bridge --max-connections 16`:
  run with an explicit bounded connection limit.
- `PYTHONPATH=bridge python3 -m deskhelm_bridge bridge --max-subscribers 8
  --subscriber-queue-frames 8`: run with explicit subscription bounds.
- `PYTHONPATH=bridge python3 -m deskhelm_bridge simulate`: emit demo events.
- `PYTHONPATH=bridge python3 -m unittest discover -s tests -v`: run tests.
- `git diff --check`: detect whitespace errors.
- `find docs -name '*.md' -type f`: list documentation for review.
- `rg '<term>' .`: search code and documentation.

Document component-specific commands in that component's `README.md`. Add a
repository-level wrapper only when multiple implemented stacks require it.

## Coding and Naming Conventions

Follow `.editorconfig`: UTF-8, LF line endings, a final newline, two-space
indentation, and no trailing whitespace. Makefiles use tabs. Prefer descriptive
names over abbreviations.

- Directories and files: `kebab-case`.
- ADRs: `docs/decisions/NNNN-short-title.md`.
- Dated research: `docs/research/YYYY-MM-DD-topic.md`.
- Protocol fields and configuration keys: `snake_case` unless an adopted
  ecosystem requires otherwise.
- Tests: name after observable behavior.

## Testing and Compatibility

Add tests beside their component or under `tests/` for cross-component
behavior. Protocol and adapter changes require captured or synthetic fixtures,
including producing runtime versions where applicable. Test malformed input,
unknown events, cancellation, queue limits, and terminal states, not only happy
paths.

Firmware changes should include hardware-in-the-loop notes when necessary.
Record every untested hardware or model assumption explicitly.

## Git and Review

Use focused commits with short imperative subjects, optionally with a
conventional prefix, such as `protocol: define interaction envelope`. Do not
mix unrelated formatting, refactors, generated artifacts, or dependency updates
into a feature commit.

Before committing, run the relevant tests and `git diff --check`. Pull requests
should explain the problem, approach, validation, affected components, risks,
and linked ADRs. Include screenshots, recordings, schematics, or renders for
visible changes.

Do not commit secrets, credentials, generated binaries, model weights, vendor
archives, or files with unclear commercial licensing.

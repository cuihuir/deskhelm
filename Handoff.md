# Handoff

Date: 2026-08-17

Project: DeskHelm

Repository: <https://github.com/cuihuir/deskhelm>

## Current Objective and Status

Build the no-hardware DeskHelm software path before committing to physical
devices:

```text
Bridge state and sessions
  -> interaction and control protocols
  -> text-only Agent gateway
  -> PTT, ASR, and interruptible TTS
  -> physical controls
```

Phase 0 is working and pushed to `main`. The Bridge has a Unix socket transport,
normalized `AgentEvent v1`, CLI simulator, Codex hook adapter, `StateStore`,
in-process subscriptions, `SessionRegistry`, and terminal projection. Voice
Gateway implementation has not started.

## Completed Work

- Created and pushed the initial project baseline.
- Selected `DeskHelm` as the product and repository name.
- Added repository Git attributes, ignore rules, local commit identity, and an
  HTTPS `origin`.
- Separated Bridge state storage, session projection, and terminal rendering.
- Added unit and end-to-end coverage for events, display, Codex hooks,
  `StateStore`, and `SessionRegistry`.
- Recorded Phase 0 and Bridge-boundary ADRs.
- Added local voice-stack research and a no-hardware software roadmap.
- Reviewed two public Agent I/O projects and extracted streaming, adapter,
  fixture, observability, privacy, and idempotency lessons.
- Added the project constitution and durable DeskHelm-specific collaboration
  rules to `AGENTS.md`.

## Key Decisions

- DeskHelm integrates existing coding agents; it is not a new general Agent or
  LLM runtime.
- Bridge, Voice Gateway, and Physical Surface remain separate components.
- State projection, rich interaction, and control commands remain separate
  protocol planes.
- `AgentEvent v1` remains compatible while richer protocols are designed.
- Session identity is `agent_id + session_id + project_id`; `slot` is a display
  mapping.
- Vendor parsing belongs inside adapters, which must declare capabilities.
- Streams and queues require explicit bounds, ordering, cancellation,
  correlation, and slow-consumer behavior.
- Approval and rejection require precise targets and must not be blindly
  retried.
- Bridge remains dependency-minimal; voice models and GPU runtimes stay outside
  it.

## Important Files

- `AGENTS.md`: constitution and repository rules.
- `README.md`: project overview and current quickstart.
- `docs/software/no-hardware-roadmap.md`: active milestone plan.
- `docs/architecture/multimodal-agent-console.md`: component architecture.
- `docs/research/2026-08-14-local-voice-stack.md`: ASR and TTS research.
- `docs/research/2026-08-17-agent-io-design-lessons.md`: external design review.
- `docs/decisions/0001-phase-0-python-unix-socket.md`: Phase 0 transport.
- `docs/decisions/0002-separate-bridge-state-and-session-projection.md`:
  Bridge state and session boundary.
- `bridge/agent_io_bridge/state_store.py`: state snapshots and subscriptions.
- `bridge/agent_io_bridge/session_registry.py`: session-to-slot projection.

## Validation

Last verified on 2026-08-17:

```bash
PYTHONPATH=bridge python3 -m unittest discover -s tests -v
```

Result: 17 tests passed.

Repository checks also passed:

```bash
git diff --check
find docs -name '*.md' -type f
```

## Remaining Work

1. Migrate legacy `agent-io`, `agent_io`, and `next_keyboard` public identifiers
   to DeskHelm with an explicit compatibility plan.
2. Choose and add the repository license; the GitHub repository is currently
   public but has no root license file.
3. Define session lifecycle and focus semantics.
4. Decide bounded Bridge concurrency and external subscription transport in an
   ADR.
5. Define the adapter capability contract and add versioned Codex fixtures.
6. Define `InteractionEvent v1` ordering, correlation, cancellation, terminal
   events, and privacy boundaries.
7. Define `ControlCommand v1`, including targeting, expiry, idempotency, and
   approval safety.
8. Build the text-only Codex gateway with a deterministic fake provider.
9. Build the Voice Gateway skeleton with fake audio, ASR, TTS, and playback
   providers before installing models.

## Risks and Blockers

- The Bridge server still handles connections sequentially.
- `InteractionEvent` and `ControlCommand` are architecture drafts, not accepted
  protocols.
- Existing package, CLI, socket, and documentation identifiers still use
  `agent-io`; renaming them without a compatibility plan would break users.
- The public repository has no selected open-source license.
- Codex, Claude, and Gemini compatibility is not yet backed by captured,
  versioned fixture sets.
- ASR and TTS quality, latency, recovery, resource use, and model licensing have
  not been benchmarked on the target machine.

## Next Step

Complete the DeskHelm naming and license decisions, then write the protocol
envelope and adapter-capability ADR before implementing the text-only Agent
gateway.

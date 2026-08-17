# Phase 0 Software Validation

## Goal

Prove that multiple coding-agent states can be normalized, transported locally, and understood at a glance before committing to hardware.

## Deliverables

- Four-slot terminal status panel.
- Versioned normalized event schema.
- Local Unix socket bridge.
- CLI event emitter and four-agent simulator.
- Codex lifecycle hook adapter.
- Automated protocol and end-to-end tests.

## Non-goals

- USB, RGB, firmware, or physical controls.
- Persistent history or remote access.
- Automatic approval or command execution.
- Production packaging for every operating system.

## Success Criteria

1. Starting the bridge displays four offline slots.
2. A CLI event updates the selected slot.
3. The simulator cycles all four slots through representative states.
4. A Codex hook payload is translated and delivered to the bridge.
5. Invalid states, slots, and protocol versions are rejected.

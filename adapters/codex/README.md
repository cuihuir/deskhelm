# Codex Adapter

The current hook adapter maps Codex hook notifications to legacy-compatible
DeskHelm state events. The next adapter stage will wrap `codex exec --json` and
normalize its JSONL stream into complete session and interaction events.

Versioned JSONL evidence lives under
`tests/fixtures/adapters/codex-exec-json/`. The official example is copied from
Codex non-interactive mode documentation and is not claimed as locally
produced. Synthetic files cover failure, unknown-event, and malformed-input
boundaries without requiring network access or a logged-in runtime.

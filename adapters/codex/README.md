# Codex Adapter

The hook adapter maps Codex hook notifications to legacy-compatible DeskHelm
state events. The text provider wraps `codex exec --json` and normalizes its
JSONL stream into session-scoped interaction events.

That text provider is now implemented in `deskhelm_codex_adapter`. It accepts
prompts over stdin, maps supported assistant-message and command-execution
events, retains bounded process-local thread IDs for resume, and stops its owned
process group on interruption or timeout. Unknown well-formed events are
ignored; malformed JSONL and missing terminal events fail the run.

Versioned JSONL evidence lives under
`tests/fixtures/adapters/codex-exec-json/`. The official example is copied from
Codex non-interactive mode documentation and is not claimed as locally
produced. Synthetic files cover failure, unknown-event, and malformed-input
boundaries without requiring network access or a logged-in runtime.

The implementation follows the official Codex non-interactive mode contract:
<https://learn.chatgpt.com/docs/non-interactive-mode>.

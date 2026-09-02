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

## Manual TUI Sessions

`CodexAppServerAdapter` connects to a DeskHelm-managed `codex app-server` over
its local Unix WebSocket transport. The managed TUI connects through a separate
bounded local WebSocket proxy, which forwards normal traffic to the app-server
and exposes the real server-initiated command/file/permission approval request to the
adapter without copying its private content. The adapter performs the JSON-RPC initialization
handshake and attaches directly to one loaded thread. Direct attachment is
required because a newly opened remote TUI has no rollout for `thread/resume`
until its first turn. Attachment makes the thread immediately targetable.
Codex's `/resume` picker uses one additional short-lived proxy connection; it
forwards normal app-server traffic but never owns voice approvals.
The adapter separately retries bounded `thread/resume` calls until its
connection is subscribed to manually started turns. A successful DeskHelm
`turn/start` also establishes the subscription. Explicit thread resume errors
remain fail-closed, and bounded metadata reads exclude one positively
identified ephemeral internal thread without selecting among multiple user
threads. The adapter maps turn completion, command/file/permission approvals,
and structured user-input requests into DeskHelm interactions. Prompt,
interrupt, approve, and reject controls return to the same thread through the
app-server protocol. Permission approval grants only the requested subset for
the current turn; rejection grants none.

Codex `thread/status/changed` and resumed thread status also drive a bounded
notification-only approval-attention state. Entering `waitingOnApproval`
speaks one fixed confirmation notice. That status has no request ID and does
not register a pending approval by itself. The managed TUI proxy separately
captures the correlated request, making exact one-shot voice approval possible;
the voice layer suppresses the duplicate notice.

This mode does not inject terminal keystrokes and cannot attach to an unrelated
plain `codex` process. Start the DeskHelm Alpha first, then manually run the
`codex --remote unix://...` command printed by Alpha. Multiple non-ephemeral
loaded threads, secret input, and multi-question input remain terminal-only in
the initial implementation.

For a single-terminal workflow, `deskhelm alpha --tui` starts the same remote
command as a foreground child after the managed app-server and Bridge are
ready. Exiting that TUI stops the complete Alpha stack. This is a convenience
launcher only; the adapter still uses the same local app-server protocol.

The adapter uses fixed privacy-safe approval and input summaries. Command text,
file changes, working directories, reasons, and question text are not copied to
notifications. Voice approval accepts only the bounded exact approval phrases
`批准`, `确认`, `同意`, `允许`, and `可以`, or rejection phrases `拒绝`, `不同意`,
`不允许`, and `取消`, when one non-expired target approval is pending; it never
uses session-wide approval. Voice and keyboard decisions race safely: the first
response wins, keyboard-first clears DeskHelm state, and voice-first suppresses
the later TUI response for that same request.

The protocol follows the official Codex app-server contract:
<https://learn.chatgpt.com/docs/app-server>. The app-server WebSocket transport
is experimental and this integration remains Alpha-only.

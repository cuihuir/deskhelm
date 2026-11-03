# Adapters

Runtime-specific drivers translate a vendor's process and wire format into
protocol v2 facts and effects. Each driver runs inside one session host
(`bridge/deskhelm_host`) and declares a capability matrix when its session
opens; the Bridge enforces that matrix and imports no adapter code.

| Directory | Runtime | Transport |
|---|---|---|
| [`codex/`](codex/) | Codex CLI | `codex app-server` over a local Unix WebSocket, with the managed TUI behind a proxy |
| [`claude/`](claude/) | Claude Code | `claude -p` stream-json on stdio, with `--permission-prompt-tool stdio` |
| [`acp/`](acp/) | ACP agents | ACP JSON-RPC on stdio; Grok is the first flavor |

Vendor formats stay inside the adapter. Tool labels are fixed kinds, and
command lines, paths, tool arguments, thoughts, working directories, and
vendor error text stay out of content-free facts. Prompts reach the vendor
on stdin, never in process arguments.

A driver completes a streamed reply with its whole text and sends only new
tool output, or the whole output marked `replace` when the vendor replaced it
(Grok sends the whole content on each update). Claude needs
`--permission-prompt-tool stdio`; without it, Claude Code 2.1.312 denies a
tool that needs permission without asking. Its driver declines
`AskUserQuestion` at once because the session takes no input. Grok approvals
use only the `allow_once` or `reject_once` option; without one, the decision
is undelivered and the request stays pending.

Compatibility evidence lives under `tests/fixtures/adapters/<adapter>/` with a
manifest that records source type, runtime version, and provenance. Replay
transcripts under `tests/fixtures/replay/<runtime>/` drive the real drivers;
every current transcript is synthetic.

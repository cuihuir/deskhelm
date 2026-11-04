# Handoff

Date: 2026-11-04

Project: DeskHelm

Repository: <https://github.com/cuihuir/deskhelm>

## Current State

DeskHelm 0.2.0 is released from `main` as tag `v0.2.0`. It keeps the protocol
v2 architecture of 0.1.0 (ADR 0043, phases 0 to 4) and changes the protocol
without compatibility with 0.1.0, which the user accepted on 2026-11-04
because DeskHelm has no users yet (ADR 0045). Phase 5, the hardware and
remote-access ADRs, has not started.

| Component | Where | State |
|---|---|---|
| Bridge | `bridge/deskhelm_core` | Single-threaded actor at `bridge.sock`; hosts start it on demand |
| Codex host | `adapters/codex`, `deskhelm codex` | Managed TUI, approval proxy, PTY relay; live-checked with Codex CLI 0.166.0 |
| Claude and Grok hosts | `adapters/claude`, `adapters/acp`, `deskhelm agent` | Live-checked with Claude Code 2.1.312 and Grok 1.0.60 |
| Surfaces | `deskhelm_client`, `deskhelm_web`, `deskhelm_dashboard` | Rebuilt web page on port 8766 and the dashboard |
| Voice host | `bridge/deskhelm_voice_host`, `deskhelm voice` | Paraformer ASR, Piper, Kokoro, or IndexTTS notices, right-Alt PTT; user-verified in 0.1.0 |
| Demo | `deskhelm simulate` | Fake session host with a scripted turn and approval |

What 0.2.0 changed since 0.1.0:

- **The web page is rebuilt (ADR 0044):** the server folds interaction
  events with `deskhelm_client.transcript` and sends a snapshot, then
  revisioned patches. The page is plain ES modules: Markdown replies, tool
  rows with their command or path and outcome, approvals that show what they
  would allow, a stop button, a details panel, a Codex model menu, light and
  dark themes, and an approval count in the tab title.
- **Protocol changes:** the Bridge publishes how each approval ended; a
  completed message carries its whole text; tool output may replace itself;
  the session view counts catalog pushes; a console delta carries only
  changed sessions (ADR 0044). A tool's completion states `completed`,
  `failed`, or `declined`, and a request carries its operator-only detail,
  also when a host reopens it (ADR 0045).
- **Drivers:** Claude is ready before its first prompt; Claude and Grok tools
  show their command or path; no driver invents exit codes; a tool a driver
  refused reads as declined; Grok replies split at tool calls and its tool
  output is not repeated; Codex shell wrappers are unwrapped.
- **Command line:** `deskhelm agent` and `deskhelm codex` have parsers of
  their own, and a stopped host ends by its signal.
- **README:** a screenshot of the page, a Mermaid flowchart of the
  architecture, and a three-step quick start. The screenshots come from
  synthetic sessions, not a real project.

Since 0.2.0, branch `next` (not pushed, not released) adds answering agent
questions on the page (ADR 0046), after the user saw a Codex question as a
plain list and its TUI answer as raw `<send_user_message_question_reply>`
JSON:

- An input request carries its questions and options as operator-only
  content; a non-blocking request outlives its turn; an operator-only
  `answer` command answers by question id; `input_resolved` says how a
  question ended and with which answers.
- Codex 0.166.0's `request_user_input_async` questions (an `agentMessage`
  with `questions`, which Codex does not wait on) and `requestUserInput`
  requests become answerable cards. DeskHelm answers an async question with
  the TUI's own reply message, recognizes the TUI's answers, and never shows
  the wrapper.
- The page's question card offers the options (the first chosen, as in the
  TUI), a free-text answer, and one submit, and keeps a half-typed answer
  while the agent works. The dashboard shows questions and their answers.

`voice/runtime` installs the checkout as an editable path dependency, so no
command, test, or tool needs `PYTHONPATH`. The two uv projects stay separate
so model dependencies never enter the root package.

## Key Decisions

- ADR 0043 with D1 to D4 accepted as recommended: a bounded in-memory
  interaction window, exact voice phrases only for a single answerable
  approval, and removal of every v1 path.
- ADR 0045: the Bridge, hosts, and surfaces of one release run together, and
  no component checks the release of its peer. Breaking changes are preferred
  over compatibility layers while DeskHelm has no users.
- A request's detail and a tool's command line are content: only the
  operator's interaction stream carries them, and console tickets stay
  content-free for the voice and device tiers.
- The page has no build step, framework, or dependency; transcript logic and
  wording live in `deskhelm_client`, so the page and the dashboard agree.
- Superseded ADRs keep their text; their status lines name what changed.
- uv uses the official PyPI index; the Tsinghua mirror was removed from the
  user's shell configuration at their request.

## Validation

- On `next`: 351 tests and all 51 browser checks pass, including a replay
  transcript of both async-question paths. Live with Codex CLI 0.166.0 on a
  private Bridge: a question answered on the page reached Codex, which
  replied with the answer, and the TUI took it off its queue; a question
  answered in the TUI (`shift+←`, Enter) showed on the page with its answer;
  no raw reply appeared in either case.

- Full suite in the voice runtime without `PYTHONPATH`: 340 tests pass (289 at
  0.1.0). The root environment passes every test except the ASR provider
  tests, which need NumPy.
- `tools/check-web-page.py`: all 45 browser checks pass in Chrome with the
  page's CSP enforced, including approval details, a declined tool, and an
  approval rebuilt after a Bridge restart that keeps its detail.
- 2026-11-04, live on the page against private Bridges with the 0.2.0 code:
  Claude Code 2.1.312 showed each approval's path or command, approved and
  rejected from the page, read the refused tool as declined, and stopped
  mid-table; Grok 1.0.60 showed `seq 1 5` once between two replies and its
  tool as completed; Codex CLI 0.166.0 showed the command of its approval,
  finished the turn, and `/quit` exited 0 with nothing left behind.
- The first Grok run after the machine's 09:12 reboot, made alongside the
  Claude check, never became ready and its host exited 1. A direct ACP probe,
  a hand-started host, and a rerun all worked; the cause is unknown.
- Earlier on 2026-11-04 with Codex CLI 0.166.0: with an approval pending, the
  Bridge was replaced and the host reopened its session within 3.5 s.

## Remaining Work

- Choose a repository license. The repository is public and has no license
  file.
- Phase 5: hardware and remote-access ADRs.
- Push `next` and release it (a protocol change, so 0.3.0) once the user has
  tried answering questions on the page; the running stack needs a restart.
- Codex `requestUserInput` questions (used in Plan mode) and secret
  questions were not seen live; only fixtures and unit tests cover them.
  Claude Code's `AskUserQuestion` is still declined, so Claude never asks.
- Not verified live: Grok approvals (the user's Grok config is
  `always-approve`, and a `GROK_CONFIG` overlay did not change it), so
  whether Grok reports a rejected tool as `failed` is unconfirmed; the Codex
  `/resume` picker under v2; a Codex file-change approval's paths (the live
  turn wrote its file through a shell command).
- Codex approval summaries are English while Claude and Grok summaries are
  Chinese.
- Left from the review: a streamed reply still redraws its whole message,
  throttled by its length; the 5 s SSE send timeout may cut a large snapshot
  to a slow remote browser; `tests.test_host_link` leaves a ResourceWarning;
  test Bridges write their log to the user's `$XDG_RUNTIME_DIR/deskhelm/`.
- The v1 voice turn timing record (ADR 0029) has no v2 equivalent, and the
  voice host does not enable advisory VAD.
- Every replay transcript is synthetic; recording a live one needs an
  operator decision and review.
- Voice selection: production VAD is unselected; Paraformer is the alpha
  ASR, not a final choice; SenseVoice's FunASR Model License 1.1 and Piper's
  GPL runtime need review before packaging.

## Risks

- The Codex app-server WebSocket transport is experimental, so the Codex host
  is Alpha-only. It cannot attach to an unrelated plain `codex` process, and
  secret or multi-question input stays in the TUI.
- DeskHelm runs from a source checkout: the Codex relay is
  `tools/run-with-pty.py`, and the voice host locates the checkout. A wheel
  install is not supported.
- A process from another release is not refused at the handshake: a 0.1.0
  host is disconnected by a 0.2.0 Bridge at its first tool completion and
  then reconnects again.
- Approval tracking is bounded at 16 per session; the interaction window is
  bounded and is not history.

## Operator Notes

- After pulling, resynchronize both environments: `uv sync --extra ptt
  --locked` at the root and `uv sync --locked --python 3.12` in
  `voice/runtime`.
- When upgrading, stop every DeskHelm process (agent sessions, `deskhelm
  web`, `deskhelm dashboard`, voice) and the background Bridge (`pkill -f
  deskhelm_core`), then start them again; the first session starts a new
  Bridge.
- For live checks, keep tests off the user's Bridge: Codex accepts a private
  `XDG_RUNTIME_DIR`; isolate Grok and Claude with a hand-started Bridge and
  `--socket`. Give prompts absolute paths, because a nested Claude in the
  agent sandbox believes it works in the sandbox's directory.
- `deskhelm codex --voice` needs the configured USB microphone; without it,
  add `--default-source`. The voice host logs to `codex-<digest>-voice.log`
  and the Bridge to `bridge.log`, both in `$XDG_RUNTIME_DIR/deskhelm/`.
- `t.sh` is a local script and stays untracked.

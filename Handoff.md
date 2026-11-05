# Handoff

Date: 2026-11-06

Project: DeskHelm

Repository: <https://github.com/cuihuir/deskhelm>

## Current State

DeskHelm 0.3.0 is released from `main` as tag `v0.3.0`. It keeps the protocol
v2 architecture of 0.1.0 (ADR 0043, phases 0 to 4) and changes the protocol
without compatibility with earlier releases, which the user accepted on
2026-11-04 because DeskHelm has no users yet (ADR 0045). Phase 5, the hardware
and remote-access ADRs, has not started.

| Component | Where | State |
|---|---|---|
| Bridge | `bridge/deskhelm_core` | Single-threaded actor at `bridge.sock`; hosts start it on demand |
| Codex host | `adapters/codex`, `deskhelm codex` | Managed TUI, approval proxy, PTY relay, answerable questions; live-checked with Codex CLI 0.166.0 |
| Claude and Grok hosts | `adapters/claude`, `adapters/acp`, `deskhelm agent` | Live-checked with Claude Code 2.1.312 and Grok 1.0.60 |
| Surfaces | `deskhelm_client`, `deskhelm_web`, `deskhelm_dashboard` | Web page on port 8766 and the dashboard |
| Voice host | `bridge/deskhelm_voice_host`, `deskhelm voice` | Paraformer ASR, Piper, Kokoro, or IndexTTS notices, right-Alt PTT; user-verified in 0.1.0 |
| Demo | `deskhelm simulate` | Fake session host with a scripted turn and approval |

What each release added:

- **0.1.0:** protocol v2, with a single-threaded Bridge, one session host per
  agent, and the page, dashboard, and voice surfaces (ADR 0043).
- **0.2.0:** the web page rebuilt on a server-built transcript (ADR 0044);
  tool outcomes and approvals that show what they would allow (ADR 0045);
  driver fixes for Claude, Grok, and Codex; and a README with a screenshot,
  a flowchart, and a quick start.
- **0.3.0:** answering agent questions on the page (ADR 0046), after the user
  saw a Codex question as a plain list and its TUI answer as raw
  `<send_user_message_question_reply>` JSON. An input request carries its
  questions and options as operator-only content; a non-blocking request
  outlives its turn; an operator-only `answer` command answers by question
  id; `input_resolved` says how a question ended and with which answers.
  Codex 0.166.0's `request_user_input_async` questions (an `agentMessage`
  with `questions`, which Codex does not wait on) and `requestUserInput`
  requests become answerable cards. DeskHelm answers an async question with
  the TUI's own reply message, recognizes the TUI's answers, and never shows
  the wrapper. The card offers the options (the first chosen, as in the
  TUI), a free-text answer, and one submit, and keeps a half-typed answer
  while the agent works.

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
- A request's detail, an input request's questions and answers, and a tool's
  command line are content: only the operator's interaction stream carries
  them, and console tickets stay content-free for the voice and device tiers.
- ADR 0046: only the operator tier answers questions, by question id. A Codex
  async question is answered with the TUI's own reply message, so the TUI
  takes it off its queue.
- The page has no build step, framework, or dependency; transcript logic and
  wording live in `deskhelm_client`, so the page and the dashboard agree.
- Superseded ADRs keep their text; their status lines name what changed.
- uv uses the official PyPI index; the Tsinghua mirror was removed from the
  user's shell configuration at their request.

## Validation

- Full suite in the voice runtime without `PYTHONPATH`: 351 tests pass at
  0.3.0 (340 at 0.2.0, 289 at 0.1.0), including a replay transcript of both
  ways a Codex async question is answered. The root environment passes every
  test except the ASR provider tests, which need NumPy.
- `tools/check-web-page.py`: all 51 browser checks pass in Chrome with the
  page's CSP enforced, including question cards, a half-typed answer kept
  while the session changes, approval details, a declined tool, and an
  approval rebuilt after a Bridge restart.
- 2026-11-04, live with Codex CLI 0.166.0 on a private Bridge: a question
  answered on the page reached Codex, which replied with the answer, and the
  TUI took it off its queue; a question answered in the TUI (`shift+←`, then
  Enter) showed on the page with its answer; no raw reply appeared.
- 2026-11-04, live with the 0.2.0 code: Claude Code 2.1.312 showed each
  approval's path or command, approved and rejected from the page, read the
  refused tool as declined, and stopped mid-table; Grok 1.0.60 showed
  `seq 1 5` once between two replies; Codex CLI 0.166.0 showed the command of
  its approval, `/quit` exited 0 with nothing left behind, and with an
  approval pending its host reopened on a replaced Bridge within 3.5 s.
- One Grok run right after a reboot, made alongside a Claude check, never
  became ready and its host exited 1. A direct ACP probe, a hand-started
  host, and a rerun all worked; the cause is unknown.

## Remaining Work

- Choose a repository license. The repository is public and has no license
  file.
- Phase 5: hardware and remote-access ADRs.
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
  secret questions stay in the TUI.
- DeskHelm runs from a source checkout: the Codex relay is
  `tools/run-with-pty.py`, and the voice host locates the checkout. A wheel
  install is not supported.
- A process from another release is not refused at the handshake: a 0.1.0
  host is disconnected by a newer Bridge at its first tool completion, a
  0.3.0 host by a 0.2.0 Bridge at its first question, and either then
  reconnects again.
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

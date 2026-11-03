# Handoff

Date: 2026-11-04

Project: DeskHelm

Repository: <https://github.com/cuihuir/deskhelm>

## Current State

DeskHelm 0.1.0 is released from `main` as tag `v0.1.0`. It completes phases
0 to 4 of ADR 0043: protocol v2 is the only protocol, and the v1 Bridge, its
planes and router, the stdio relays, the runtime directory, `emit`, the Codex
hook, the codex exec gateway with its project registry, `deskhelm ptt`, and
the `agent-io` aliases are gone. Phase 5, the hardware and remote-access ADRs,
has not started. The redesign baseline is tag `pre-architecture-v2`
(`ec128a3`).

| Component | Where | State |
|---|---|---|
| Bridge | `bridge/deskhelm_core` | Single-threaded actor at `bridge.sock`; hosts start it on demand |
| Codex host | `adapters/codex`, `deskhelm codex` | Managed TUI, approval proxy, PTY relay; user-verified |
| Claude and Grok hosts | `adapters/claude`, `adapters/acp`, `deskhelm agent` | Live-checked with Claude Code 2.1.312 and Grok 1.0.60 |
| Surfaces | `deskhelm_client`, `deskhelm_web`, `deskhelm_dashboard` | Web page on port 8766 and the dashboard; user-verified |
| Voice host | `bridge/deskhelm_voice_host`, `deskhelm voice` | Paraformer ASR, Piper, Kokoro, or IndexTTS notices, right-Alt PTT; user-verified |
| Demo | `deskhelm simulate` | Fake session host with a scripted turn and approval |

Since the release, branch `next` (not pushed) holds:

- The page tells an unknown prompt outcome (`dispatch_failed`, a Bridge
  timeout or lost connection, or a lost HTTP response) apart from a failed
  one, and its fallback wording mirrors `deskhelm_client/texts.py` under a
  test. The page never retries, so it needs no idempotency key of its own.
- The sidebar session card keeps its time label inside the card.
- A stopped `deskhelm agent` host returns 128 plus the signal, not 1.
- The page is rebuilt (ADR 0044), as the user asked on 2026-11-04: a
  mature agent page that stays light. `deskhelm_client.transcript` folds
  interaction events into items and patches; the web server sends a
  snapshot, then revisioned patches; the page is plain ES modules with
  Markdown replies, tool rows that show the command or path, inline
  approvals, a stop button, a details panel, and light and dark themes. The
  Bridge now sends a session in a console delta only when its view changed.
  The tab title counts waiting approvals, and the session list marks
  activity not yet seen. `tools/check-web-page.py` checks the page in
  Chrome (25 checks pass), and an end-to-end run with the real `simulate`
  and `web` commands carried a prompt through an approval to its reply.

`voice/runtime` installs the checkout as an editable path dependency, so no
command, test, or tool needs `PYTHONPATH`. The two uv projects stay separate
so model dependencies never enter the root package.

## Key Decisions

- ADR 0043 with D1 to D4 accepted as recommended: a bounded in-memory
  interaction window, exact voice phrases only for a single answerable
  approval, and removal of every v1 path.
- Superseded ADRs keep their text; their status lines name what changed.
  The v1 protocol documents stay in `protocol/` as the record ADRs link to.
- AGENTS.md now states the architecture as v2 invariants; component READMEs
  hold the details.
- uv uses the official PyPI index; the Tsinghua mirror was removed from the
  user's shell configuration at their request.

## Validation

- Full suite in the voice runtime without `PYTHONPATH`: 307 tests pass on
  `next` (289 at the release). The root environment passes every test except
  the ASR provider tests, which need NumPy.
- Every documented command starts from outside the checkout without
  `PYTHONPATH`, including the voice host through `uv` and the right-Alt
  listener with the root environment's Python.
- 2026-11-04, live with Codex CLI 0.166.0 in a private runtime directory:
  with an approval pending, the Bridge was stopped for 2 s and replaced. The
  TUI kept running, the host reopened its session on the new Bridge within
  3.5 s with the approval rebuilt under a new id, approving it finished the
  turn, and `/quit` exited 0 with no process or socket left.
- 2026-11-04, live with Grok 1.0.60: prompts, tool output, and turn results
  flow, and the host stops cleanly. The user's Grok config sets
  `permission_mode = "always-approve"`, so Grok never asks and its approvals
  cannot be exercised without changing that config; a session `GROK_CONFIG`
  overlay of `[ui] permission_mode` did not change it.
- Live checks on v2: Codex CLI 0.166.0 (session, one proxied approval,
  `/quit`, and SIGTERM with nothing left behind), Claude Code 2.1.312
  (approve and reject), and Grok 1.0.60 (prompt, reply, interrupt). The user
  verified the Codex TUI, the page and dashboard staying in sync, the voice
  path with `--default-source`, and the switched commands.

## Remaining Work

- Choose a repository license. The repository is public and has no license
  file.
- Phase 5: hardware and remote-access ADRs.
- Not yet verified live: Grok approvals (see Validation) and the Codex
  `/resume` picker under v2.
- The v1 voice turn timing record (ADR 0029) has no v2 equivalent, and the
  voice host does not enable advisory VAD.
- Every replay transcript is synthetic; recording a live one needs an
  operator decision and review.
- Voice selection: production VAD is unselected (it needs Chinese
  conversation and gain-variant evidence); Paraformer is the alpha ASR, not a
  final choice; SenseVoice's FunASR Model License 1.1 and Piper's GPL runtime
  need review before packaging; blinded TTS listening, native cancellation
  during inference, and acoustic first-audio latency are unmeasured.

## Risks

- The Codex app-server WebSocket transport is experimental, so the Codex host
  is Alpha-only. It cannot attach to an unrelated plain `codex` process, and
  secret or multi-question input stays in the TUI.
- DeskHelm runs from a source checkout: the Codex relay is
  `tools/run-with-pty.py`, and the voice host locates the checkout. A wheel
  install is not supported.
- Approval tracking is bounded at 16 per session; the interaction window is
  bounded and is not history.

## Operator Notes

- After pulling, resynchronize both environments: `uv sync --extra ptt
  --locked` at the root and `uv sync --locked --python 3.12` in
  `voice/runtime`. Voice fails to import `deskhelm_voice_host` until the
  voice runtime is resynchronized.
- For live checks, keep tests off the user's Bridge: Codex accepts a
  private `XDG_RUNTIME_DIR`, but the user's `grok` wrapper reads proxy
  settings through `gsettings` and hangs without the real session bus, so
  isolate Grok with a hand-started Bridge and `--socket` instead.
- A Claude Code session started before the mirror removal may still carry
  `UV_INDEX_URL` in its shell snapshot; unset it before running uv.
- `deskhelm codex --voice` needs the configured USB microphone; without it,
  add `--default-source`. The voice host logs to `codex-<digest>-voice.log`
  and the Bridge to `bridge.log`, both in `$XDG_RUNTIME_DIR/deskhelm/`.
- `t.sh` is a local script and stays untracked.

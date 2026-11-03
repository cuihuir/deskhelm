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

- Full suite in the voice runtime without `PYTHONPATH`: 289 tests pass. The
  root environment passes every test except the ASR provider tests, which
  need NumPy.
- Every documented command starts from outside the checkout without
  `PYTHONPATH`, including the voice host through `uv` and the right-Alt
  listener with the root environment's Python.
- Live checks on v2: Codex CLI 0.166.0 (session, one proxied approval,
  `/quit`, and SIGTERM with nothing left behind), Claude Code 2.1.312
  (approve and reject), and Grok 1.0.60 (prompt, reply, interrupt). The user
  verified the Codex TUI, the page and dashboard staying in sync, the voice
  path with `--default-source`, and the switched commands.

## Remaining Work

- Choose a repository license. The repository is public and has no license
  file.
- Phase 5: hardware and remote-access ADRs.
- Not yet verified live: Grok approvals, the Codex `/resume` picker under
  v2, and a Bridge restart during a Codex host session.
- The page merges the interaction stream in the browser, and the browser
  sends no idempotency key.
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
- A Claude Code session started before the mirror removal may still carry
  `UV_INDEX_URL` in its shell snapshot; unset it before running uv.
- `deskhelm codex --voice` needs the configured USB microphone; without it,
  add `--default-source`. The voice host logs to `codex-<digest>-voice.log`
  and the Bridge to `bridge.log`, both in `$XDG_RUNTIME_DIR/deskhelm/`.
- `t.sh` is a local script and stays untracked.

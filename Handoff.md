# Handoff

Date: 2026-11-03

Project: DeskHelm

Repository: <https://github.com/cuihuir/deskhelm>

## Latest Phase: Architecture v2, Phase 3 Complete

Status: paused on 2026-11-03 after phase 3, following the user's rule to
pause after each phase, on branch `refactor/architecture-v2`; nothing is
pushed. v1 stays the default: `bridge.sock`, `deskhelm codex`,
`deskhelm agent`, web, dashboard, and voice all run v1 code. Checks used
temporary sockets. No DeskHelm process is running.

- Baseline: commit `ec128a3`, tag `pre-architecture-v2`. Design:
  `docs/architecture/target-architecture.md` and ADR 0043, with D1–D4
  accepted as recommended. `t.sh` stays untracked.
- Phase 0, fixes on v1, `ed873e8` to `e6e4c9c`. Phase 1, `6330467` and
  `cda4cb8`: protocol v2 and the single-threaded core on `bridge-v2.sock`;
  contract `protocol/deskhelm-v2.md`.
- Phase 2: hosts (`ad0151d`), Claude and Grok drivers (`8cb9b5f`), Claude
  `--permission-prompt-tool stdio` and the ACP label fix (`d5d1db9`,
  `c522d7f`), the Codex adapter subscription fix that v1 shares
  (`8b8ef22`), and the Codex host (`a448a96`).
- Phase 3:
  - `19003f1`: `bridge/deskhelm_client`, one connection per surface with
    cursor resume, replay handed over once, command and query correlation,
    and the one copy of result wording; `python -m deskhelm_client
    sessions|focus`.
  - `b38d61c`: `python -m deskhelm_web` on port 8767 beside the v1 page. It
    keeps the v1 page and HTTP API, maps the console to it, decides
    approvals by approval id, reads the catalog from the Bridge cache, and
    shares the token file. A page opened later receives the Bridge's recent
    window and pending approvals. `app.js` now prefers the server's
    wording and words the working state; v1 is unaffected.
  - `367aa36`: `python -m deskhelm_dashboard` with the v1 keys; approvals
    come from the console and a lost Bridge is a notice.
  - `9d32fa8`: `python -m deskhelm_voice_host` attaches at the voice tier to
    a running Bridge. Right Alt speaks to the focused session; the D3
    phrase rules and `取消` are applied in the host; notices come from
    console transitions. The key listener is a child in the root
    environment holding `ptt.lock`.
  - `381fee0` closes a host socket after a failed reconnect; `d4c8a4a`
    removes a test race.
- Validation: the full suite passes, 537 tests, twice in a row, with
  `PYTHONPATH=bridge uv run --project voice/runtime --locked --no-sync python -m unittest discover -s tests`.
  The focused v2 command in AGENTS.md passes 100 tests. `app.js` parses
  under esprima, which settles the old unchecked-script item. A headless
  Chrome load of the v2 page rendered a fake session with its replayed
  message and pending approval and logged no script error. A PTY run of the
  dashboard approved a fake request from the keyboard and quit cleanly.
  `deskhelm_voice_host --check` passed against the encrypted voice profile
  without printing device names.
- Live checks on 2026-11-03: Grok 1.0.60 and Claude Code 2.1.312 on v2
  (prompts, replies, interrupt, and Claude approve and reject); Codex CLI
  0.166.0 through `deskhelm_host codex` (prompt, one approval through the
  TUI proxy, clean exit). The user ran the Codex host in a real terminal;
  the display, the Ctrl-C guard, and the exit were fine.

Open issues:

- Not yet exercised by a person: the voice host's speech path (hold right
  Alt and speak, then hear a notice) and the v2 page in a real browser.
- The page still merges the interaction stream in the browser; the target
  view model in Python is not done. The browser sends no idempotency key,
  which matters only if a POST is retried.
- v1 `deskhelm agent claude` never declines an expired or ninth approval and
  shows `AskUserQuestion` as an approval; v2 declines all three. The Codex
  adapter fixes reach v1 through unit tests only.
- Untested live: Grok approvals (needs a session outside always-approve
  mode), the Codex `/resume` picker under v2, and a Bridge restart during a
  Codex host session.
- Every replay transcript is synthetic; recording live ones needs an
  operator decision. v1 `deskhelm agent` started outside this checkout
  cannot import `deskhelm_voice`.

Next, after the user confirms:

1. Switch-over: v2 takes `bridge.sock`, and `deskhelm codex`, `agent`, `web`,
   `dashboard`, and voice start the v2 hosts and surfaces.
2. Phase 4: remove v1 per D4, move the page assets into `deskhelm_web`,
   uv workspace packaging, condensed AGENTS.md, and a rewritten Handoff.

Operator notes:

- v1 runs the Codex adapter inside the user bridge, so fixes reach v1 only
  after that bridge restarts, which ends every attached v1 session.
- To try v2 beside v1: from an Agent project directory run
  `PYTHONPATH=<checkout>/bridge python3 -m deskhelm_host codex`, `claude`, or
  `grok`; the first host starts a v2 Bridge on `bridge-v2.sock`. Then
  `PYTHONPATH=<checkout>/bridge python3 -m deskhelm_web` (open the printed
  link, port 8767) or `python3 -m deskhelm_dashboard`. For voice, run
  `project-secrets --project <checkout> run --env default --require
  DESKHELM_ALPHA_ASR_MODEL_DIRECTORY DESKHELM_ALPHA_TTS_DIRECTORY --
  env PYTHONPATH=<checkout>/bridge:<checkout>/voice uv run --project
  <checkout>/voice/runtime --locked --no-sync python -m deskhelm_voice_host`,
  focus a session on the page, and hold right Alt. Stop the v1 right-Alt
  listener first; both take `ptt.lock`.
- The v2 Bridge keeps running after its hosts exit. Stop it by the pid of
  `python3 -m deskhelm_core --socket …`; its output is in
  `$XDG_RUNTIME_DIR/deskhelm/bridge-v2.log`.

## Previous Phase: Web Port 8766

Status: changed on 2026-10-26. The process listening on 8765 was left running.

- `deskhelm web` now defaults to `http://127.0.0.1:8766`. Port 8765 is held by
  an unrelated local `tools.probe_monitor` process. `--port` still overrides
  the default.
- No web server was started. The user bridge was not restarted.

## Previous Phase: Live Grok Session

Status: verified on 2026-10-22. The user bridge and one Grok agent are still
running. No voice, TUI, or keyboard grab was started. Unrelated Grok and
Codex processes were left alone.

- The previous `bridge.sock` had no listener. A user bridge was started with
  `--agent-runtime-directory`, 32 slots, and 64 connections. `deskhelm agent
  grok` then attached from this repository.
- Session `grok` / `local-d38af4745134` / `next_keyboard` is active. Adapter
  `deskhelm-grok`. Runtime `grok 1.0.60 (01a8e1c591c0)`. Current model
  `grok-4.7`.
- A bounded prompt was accepted. The interaction plane published one user
  message for that command, the assistant reply, and `task_completed`. No
  tool event was published. The prompt was not logged.
- `deskhelm web` uses this same bridge. It shows activity after the page
  connects, so these verification turns are not replayed.
- Bridge pid 180537, agent pid 180548, Grok child pid 180581. The agent did
  not change repository files.

## Previous Phase: One User Message per Prompt

Status: implemented on 2026-10-22. No DeskHelm process was running, so none
was stopped. The system Codex app-server daemon was left running.

- ADR 0042. Codex, Claude, and Grok publish each accepted prompt once as a
  user message on the existing interaction plane. The correlation id is the
  control command id, so later prompts no longer replace earlier ones.
- A vendor echo of that same text is not published again. Codex keeps the
  last eight accepted texts. Claude and Grok keep the latest one. A different
  vendor user item is still published with its own id.
- The session list shows the latest visible line from events the page already
  has. Claude and Grok empty states no longer say the older dialogue is in
  Codex. The Codex empty state keeps that sentence.
- No transcript query, poll, or rollout read was added. A running adapter
  keeps the old behavior until that session is restarted. Restart
  `deskhelm web` to load the new page assets.
- Validation passed: `PYTHONPATH=bridge:adapters/codex:adapters/claude:adapters/grok
  python3 -m unittest tests.test_codex_app_server_adapter
  tests.test_agent_streams tests.test_web_client -q` (50 tests),
  `node --check bridge/deskhelm_bridge/web-assets/app.js`, and
  `git diff --check` on the touched files. No browser pass. No bridge, web,
  or agent process was started.

## Previous Phase: Idle Codex Steer Starts One Turn

Status: implemented on 2026-10-22. No bridge, web client, or Codex session
was restarted.

- ADR 0041. When Codex rejects `turn/steer` because no turn is active, the
  adapter clears that remembered turn and sends the same prompt once as
  `turn/start`. A review steer, a compact steer, and any other steer error
  still publish `codex_control_failed` and do not start a turn.
- No second transcript channel was added. Codex, Claude, and Grok output
  stays on the existing interaction events. The page does not poll a vendor
  thread or read rollout files.
- The already running Codex session still has the old adapter. Web and voice
  prompts to that session keep steering until the operator restarts it.
  Restart is an operator action.
- Validation passed: `PYTHONPATH=bridge:adapters/codex python3 -m unittest
  tests.test_codex_app_server_adapter tests.test_runtime_directory
  tests.test_web_client tests.test_session_catalog -q` (50 tests) and
  `git diff --check` on the touched files.

## Previous Phase: Stale Codex Steer Rejection

Status: diagnosed on 2026-10-21. The running session was left in place.

- Session `local-5f4d195c798d` / `fedora_enhance` was online. Its Codex turn
  had already completed. The next two DeskHelm prompts were sent as
  `turn/steer` with no parent turn. Codex did not start a turn. DeskHelm
  published `codex_control_failed`, and voice spoke the fixed line for that
  code. The page showed that failure twice and kept both optimistic prompts.
- That process still has the old adapter. ADR 0041 applies only after the
  operator starts a new DeskHelm session for the directory.

## Previous Phase: Codex Command Name

Status: the managed Codex startup command is `deskhelm codex`. Checked on
2026-10-21. No Codex process was started.

- `deskhelm codex --tui` is the public command. `deskhelm alpha` still parses
  and the user launcher still preserves the working directory for both names.
  `deskhelm --help` lists `codex` and hides `alpha`.
- Web empty-state copy uses the new command. Internal profile code stays in
  `alpha.py`.
- Validation passed: `bash -n tools/deskhelm-user-launcher`,
  `PYTHONPATH=bridge python3 -m unittest tests.test_local_alpha -q`
  (22 tests), and `git diff --check` on the touched files. Top-level help
  lists `codex` and does not list `alpha`.

## Previous Phase: Transparent Multi-Agent Supervision

Status: Claude, Codex, and Grok can share one bridge, and the loopback page
shows each runtime's capabilities and output. Checked on 2026-10-21 against a
private bridge. The user's bridge, private Alphas, and port 8765 were not
touched. No browser pass; the page was read over loopback HTTP.

- ADR 0040. `deskhelm agent claude` and `deskhelm agent grok` own the vendor
  process in the project directory and attach a relay socket. Codex stays on
  `deskhelm alpha --tui`. The attach frame still carries no working directory.
- Session queries now include adapter identity, runtime name, runtime version,
  and the existing capability list. The catalog adds `agent_capabilities` for
  vendor-declared tokens. The page shows those tokens as muted 声明 lines and
  shows the runtime on each session. Dashboard reads the same fields.
- Live check on a temporary `XDG_RUNTIME_DIR`: three active sessions,
  `claude-code`, `grok`, and `codex-cli`. Grok answered the bounded prompt.
  Claude published the user message, its model, 15 skills, and its declared
  capability tokens; that configured model did not finish the turn inside the
  wait. Codex attached and listed its control capabilities. Without a TUI
  thread its model catalog stays unavailable, which is the existing rule.
  The user bridge inode and mtime were unchanged.
- A bridge process started before this factory cannot attach Claude or Grok,
  and it still has the old Codex symlink check. The operator restarts that
  bridge. Do not kill it from this workspace. The next voice PTT loads ADR
  0039 without that restart; do not start `--voice` until the operator asks.
- Validation: `PYTHONPATH=bridge:adapters/claude:adapters/grok python3 -m
  unittest tests.test_runtime_directory tests.test_stdio_relay
  tests.test_agent_streams tests.test_session_catalog tests.test_bridge_e2e
  tests.test_web_client -q` passed, 48 tests. `git diff --check` passed for
  the touched files.

## Previous Phase: Right Alt Without a Keyboard Grab

Status: PTT no longer calls `EVIOCGRAB`. Checked on 2026-10-21. No voice
Alpha was started, and no real input device was opened.

- The whole-keyboard outage was `run_right_alt_listener` grabbing
  `/dev/input/deskhelm-ptt-kbd`. That symlink is event256, `keyd virtual
  keyboard`, which is the desktop keyboard. The right-Alt filter runs after
  the grab, so every other key was swallowed. The kill command only stopped
  the process; it did not cause the grab.
- ADR 0039. The listener reads right Alt and ignores other events. One
  listener is `deskhelm_runtime_directory()/ptt.lock`. A second listener
  still fails closed. Right Alt still reaches the compositor.
- The running user bridge does not need a restart for this change. The next
  `alpha --voice` starts a new PTT process and loads this listener. Do not
  start that until the bridge itself is one the operator means to use.
- Validation: `PYTHONPATH=bridge python3 -m unittest tests.test_right_alt_ptt -v`
  passed, 9 tests. `git diff --check` passed for the touched files.

## Previous Phase: Shared User Bridge

Status: default Alpha joins one user bridge. Checked on 2026-10-21. The
user's running Alpha processes and `deskhelm web` were not restarted.

- ADR 0038. Omitting `--socket` attaches the session to `bridge.sock`. Web
  and the dashboard read that same socket, so every attached DeskHelm session
  is on one control plane. Explicit `--socket` still starts a private bridge.
- The bridge hosts at most 32 runtimes. Attach names directory-scoped Codex
  sockets and carries no working directory. Exit detaches this session and
  does not stop the user bridge. The launcher does not inject a private
  socket and does not signal the user bridge.
- The first `alpha --voice` must start the user bridge. A bridge already
  running without voice stays that way until it is stopped.
- This slice does not add a remote app or an IM channel. Agents DeskHelm did
  not start stay off the page.
- Existing per-directory Alphas stay on their old sockets until those
  processes are restarted. Restart `deskhelm web` so it attaches to
  `bridge.sock` and loads the current page assets.
- A manual `deskhelm bridge` does not take `bridge.sock.start.lock`. Two
  starters can still race across the bind-unlink window. Alpha's own starter
  holds that lock.
- Codex app-server publishes its listen path as a symlink to
  `/tmp/codex-daemon-<uid>/`. Attach confines the parent directory and allows
  that final symlink. The user bridge process that is already running still
  has the old check; restart it before the next `alpha --voice`.
- Validation: `bash -n tools/deskhelm-user-launcher` passed.
  `PYTHONPATH=bridge python3 -m unittest tests.test_runtime_directory
  tests.test_paths tests.test_local_alpha -v` passed, 31 tests.
  `PYTHONPATH=bridge python3 -m unittest tests.test_bridge_e2e
  tests.test_web_client tests.test_transport -v` passed, 35 tests.
  `git diff --check` passed.

## Previous Phase: Web Composer Layout

Status: composer dock and popovers implemented and checked on 2026-10-20.
The user's running web process was not restarted.

- The transcript stays clear. An empty page centers one status line, and the
  history note lives there instead of under the header. The review rail stays
  closed until 审查 is opened.
- Model and reasoning open upward from the composer. Skills and MCP open from
  能力. Only one popover is open. Choosing a model or effort sends
  `configure_session` for the next new turn. 不覆盖 clears both. The choice
  does not write Codex configuration, and the current turn keeps its model.
- The page fetches the catalog when the selected session changes, and again
  on open if it is missing, or on refresh. It does not poll.
- Each skill is one line, the name only. Each MCP server is one line, the
  name plus a short status. Descriptions, scope, and tool lists stay off the
  page.
- Detail pass: a popover steps the empty stage back, and the capability
  header stays put while its list scrolls. Skill and MCP names share one
  edge. Long model names stay on one line. The narrow 会话 control stays
  one line.
- Restart `deskhelm web` and refresh the page. This phase only changes static
  assets. Restart Alpha only if that process is still older than the catalog
  provider.
- Validation: `PYTHONPATH=bridge:adapters/codex python3 -m unittest
  tests.test_session_catalog tests.test_control tests.test_web_client
  tests.test_codex_app_server_adapter -v` passed, 55 tests, before the last
  empty-state paint fix. After that fix, the page-byte test, `node --check
  bridge/deskhelm_bridge/web-assets/app.js`, and `git diff --check` passed.
- Browser check at 1280×800 and 390×800 used a temporary server on
  127.0.0.1:40731 with a missing socket. The empty stage was centered, 能力
  and the model popover opened above the composer, Escape and an outside
  click closed them, and 审查 opened the rail. Focus, interrupt, send, and
  approval were not clicked. A later stub catalog on that isolated server
  showed one name per skill and one MCP line with a short status. Clicking
  a skill inserted `$review ` and left the popover open. The check did not
  attach to the live Alpha socket.

## Previous Phase: Session Catalog and Model Selection

Status: model, MCP, and skills catalog implemented and checked on 2026-10-20.
The composer layout above replaces the stacked panel.

- ADR 0037. A controller sends `session_catalog_query` for one active session.
  Bridge checks the session, then calls the Codex adapter outside the router
  lock. The public result is models, MCP status, tool names, and skill names.
  Working directories, skill paths, tool schemas, and vendor errors stay out.
- `configure_session` stores a model and reasoning override after a catalog
  read from the last 120 seconds. The next `turn/start` sends it. `turn/steer`
  does not. Codex configuration is not written. An active turn keeps its model.
- The first page put the catalog in a panel above the composer. It fetched on
  open or refresh. 不覆盖 clears the override. A skill click inserts `$name`
  into the composer.
- `/compact`, `/plan`, `/fast`, `/status`, `/goal`, and `/rename` stay deferred.
- Restart both processes before expecting live data. Restart Alpha so the
  adapter registers the catalog provider, then restart `deskhelm web` and
  refresh `http://127.0.0.1:8765`. Restarting only the page shows the button
  and `catalog_unavailable`.
- Validation: `PYTHONPATH=bridge:adapters/codex python3 -m unittest
  tests.test_session_catalog tests.test_control tests.test_web_client
  tests.test_codex_app_server_adapter -v` passed, 55 tests. `git diff
  --check` and `node --check bridge/deskhelm_bridge/web-assets/app.js` passed.
- Browser check at 1280×800: the 能力 button opened the panel, the empty page
  said 先选择一个会话, 应用到下一轮 stayed disabled, and Escape closed the
  panel while 审查 stayed open. Model, MCP, and skill rows were not clicked
  because this check had no Codex catalog provider.

## Previous Phase: Local Loopback Web Client

Status: dependency-free Codex-layout page implemented and checked on
2026-10-20. No live Alpha session was opened.

- ADR 0036. `deskhelm web` is a replaceable Bridge client. One state
  subscription and one interaction subscription feed a loopback SSE page.
  Short controller connections send `session_query` and `control_command_v1`.
  The page does not own Agent processes, read transcript files, or send
  `focus` when a thread is selected.
- The layout follows the Codex page: project sidebar, transcript, composer,
  and review rail. The product name is DeskHelm. Operator copy is Chinese.
- The composer sends a normal `submit_prompt`. `user_input_required` stays in
  the Codex TUI. Approve and reject copy the stored request id, summary, and
  expiry. Status-derived attention is not an actionable approval.
- The page shows activity from the moment it connects. Earlier thread history
  stays in Codex. Item text appears when an item starts or completes.
- Subscription sockets are read without a buffered reader. Every complete
  NDJSON frame from one `recv` is applied before the loop waits again.
- Run `PYTHONPATH=bridge python3 -m deskhelm_bridge web`, then open
  <http://127.0.0.1:8765>. Omitting `--socket` uses this directory's Alpha
  socket or the only running Alpha bridge. The default bind is `127.0.0.1`.
  Any other host needs `--allow-remote`.
- An active Alpha session showed “离线” because the page treated Bridge's
  initial `slot-N` offline placeholder as the session's agent state. The page
  now ignores a state event whose `agent_id` does not match the session, so
  this connected session shows “在线”. Codex app-server still does not publish
  thinking, idle, or tool states. Restart the web process to load the change.
- A page with no sessions was traced to `deskhelm web --socket ''` while
  `deskhelm alpha --tui` for `/home/tope/project/luna_niao` was serving one
  active `local-alpha` session on its own socket. The empty path now fails
  immediately, and a missing flag follows that Alpha bridge. The already
  running web process still has the empty path until it is restarted.
- Changed files: `docs/decisions/0036-local-loopback-web-client.md`,
  `docs/decisions/README.md`, `docs/architecture/multimodal-agent-console.md`,
  `AGENTS.md`, `bridge/README.md`, `bridge/deskhelm_bridge/cli.py`,
  `bridge/deskhelm_bridge/web_client.py`, `bridge/deskhelm_bridge/web-assets/`,
  `adapters/codex/deskhelm_codex_adapter/app_server.py`,
  `tests/test_web_client.py`, `tests/test_codex_app_server_adapter.py`,
  `tests/README.md`, and `pyproject.toml`.
- Validation: `PYTHONPATH=bridge:adapters/codex python3 -m unittest
  tests.test_web_client tests.test_codex_app_server_adapter -v` passed,
  32 tests. A second `tests.test_web_client` run with `ResourceWarning`
  treated as an error passed, 9 tests. `git diff --check` and `node --check
  bridge/deskhelm_bridge/web-assets/app.js` passed.
- Browser check: a 1280×800 headless Chrome session reached `已连接`, showed
  the empty session, transcript, and review copy, switched the theme to
  light, and closed then reopened the review rail. Focus, interrupt, the
  composer, and approval buttons were not clicked because no session was
  registered. Those controls are covered by `tests/test_web_client.py`.
- Residual risk: the TUI dashboard still reads one buffered frame for each
  `select` wake, so a burst can delay its second interaction event. The web
  client does not.

## Earlier Phase: TUI Dashboard Concept

Status: first dependency-free prototype implemented and regression-verified on
2026-09-21.

- ADR 0035 defines the Dashboard as an optional, replaceable Bridge client
  using separate state subscription, interaction subscription, and controller
  connections.
- Cursor, selected session, Bridge focus, and attention are distinct. Browsing
  never redirects PTT or physical controls; only an explicit targeted `focus`
  command changes the Bridge-authoritative target.
- `deskhelm dashboard` now renders a responsive multi-session list plus bounded
  live detail, explicit focus, targeted interrupt controls, and bounded `a`/`r`
  approval/rejection controls. Approval metadata is copied exactly and routed
  through the existing Bridge correlation/expiry rules. It does not persist
  transcripts or own Agent processes.
- A bounded read-only `session_query` controller frame maps `AgentEvent v1`
  slots to complete session identities for local dashboards. Existing clients
  remain compatible.
- Attach/detach, PTY pooling, resume, termination, remote sessions, and durable
  history remain future supervisor capabilities requiring separate decisions.
- Changed documentation: `docs/decisions/0035-bridge-native-tui-dashboard.md`,
  `docs/decisions/README.md`,
  `docs/architecture/multimodal-agent-console.md`, and
  `docs/software/no-hardware-roadmap.md`.
- `git diff --check` passes. Focused Dashboard, transport, Bridge end-to-end,
  control, and display tests pass; the full repository suite passes `356 tests`.
  A real PTY smoke test starts Dashboard, consumes a state snapshot, and exits
  with `q` successfully.
- Dashboard approval command construction has a regression test, complemented
  by the live managed-Alpha approval checks below.
- Live managed Alpha/Codex validation is now complete: Dashboard approval
  returned `approve: dispatched`, a repeated decision returned
  `approval_already_decided`, and Dashboard rejection returned
  `reject: dispatched`. The real app-server proxy, Bridge correlation, expiry
  and duplicate-decision path have been exercised without changing private
  approval content.
- Voice preflight was checked against the current workstation on 2026-09-22.
  The encrypted model/runtime configuration and `/dev/input/deskhelm-ptt-kbd`
  are available, but the configured `DESKHELM_ALPHA_VOICE_SOURCE` names a
  missing PipeWire source. Explicitly selecting the current source
  `alsa_input.pci-0000_00_1f.3.analog-stereo` and sink
  `alsa_output.pci-0000_00_1f.3.analog-stereo` makes `deskhelm alpha --check
  --voice` pass. Live PTT/ASR/TTS verification now requires operator speech and
  listening; no Alpha process is currently running.
- A USB microphone is now present as
  `alsa_input.usb-C-Media_Electronics_Inc._USB_PnP_Sound_Device-00.analog-mono`.
  With that source and the current internal sink explicitly selected,
  `deskhelm alpha --check --voice` passes. The project environment was then
  updated to use these current stable names by default.
- The default profile now stores both stable audio names in the encrypted
  project environment: `DESKHELM_ALPHA_VOICE_SOURCE` for the USB microphone
  and `DESKHELM_ALPHA_VOICE_SINK` for the internal sink. Alpha CLI flags remain
  explicit overrides. A no-override `deskhelm alpha --check --voice` passes;
  source or sink changes can now be made in the project environment without
  editing launch commands.

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

### Session Wrap-up: 2026-09-04

Status: implementation and offline validation complete; live Alpha is stopped
cleanly and no DeskHelm process or socket is currently running.

- The `next_keyboard` Alpha was stopped after the TTS fix so a future launch
  will load the `$XDG_RUNTIME_DIR` temporary-audio change and the new IndexTTS
  WAV writer. Only its confirmed process groups were targeted; other project
  instances were not changed.
- Ownerless residual sockets were removed after `fuser` confirmed no process
  held them. The per-user DeskHelm socket directory is currently empty.
- The focused PTT and TTS changes remain uncommitted alongside the repository's
  existing user work. `git diff --check` passes; the previously completed
  no-hardware regression suite remains 352/352.
- Live audio playback, focused/no-focus PTT routing, focus switching, and
  second-listener rejection still need one fresh Alpha launch for end-to-end
  confirmation. The next operator can run `deskhelm alpha --tui --voice`
  from this checkout, then use `deskhelm focus` before `deskhelm ptt --focused`.

### Latest Phase: Focused PTT Routing

Status: implemented and regression-verified on 2026-09-04.

- PTT presses now resolve the Bridge's explicit `focused_session` through a
  bounded read-only focus query instead of always using the listener's startup
  target.
- Bridge routing rejects `press_ptt` for an active but unfocused session with
  `target_not_focused`; release remains bound to the target accepted at press.
- Alpha voice startup explicitly focuses its registered session, while
  approval/attention events remain notification-only and never steal focus.
- Added `deskhelm focus` as the reference UI/client action for selecting the
  visible session. `deskhelm ptt --focused` no longer needs a static target and
  avoids binding its status stream to one startup session.
- Focus-aware PTT, control-router, Alpha, Bridge end-to-end, and full
  no-hardware regressions pass (352 tests, including the new focus CLI/status
  coverage). Live end-to-end routing still needs a fresh Alpha launch.

### Focused PTT Handoff

- The product rule is fixed: only the explicitly focused active session may
  receive a PTT press; approval/attention never changes focus.
- A UI must issue a targeted `focus` control when the visible session changes.
  `deskhelm focus ...` is the reference terminal path.
- Current Alpha instances use per-directory Bridges, so focus is not global
  across independent Alpha windows. Do not claim cross-window routing until a
  shared Bridge or user-level focus/PTT broker is implemented.
- Next live check: restart one authorized Alpha, verify focused/no-focus PTT,
  focus changes, and exclusive listener behavior. Do not terminate the other
  running Alpha instances.

### Latest Phase: TTS Silence Diagnosis and Fix

Status: root cause identified and fixed on 2026-09-04; live Alpha restart still
required.

- The running Alpha has local voice enabled with Paraformer and IndexTTS-2.5;
  model/config/reference assets and audio preflight all pass.
- IndexTTS inference completed, but its temporary WAV was created under `/tmp`,
  where the user's tmpfs quota is exhausted (`Errno 122 Disk quota exceeded`).
  Bridge consequently reported the privacy-safe `voice_output_failed` event.
- IndexTTS now prefers `$XDG_RUNTIME_DIR` for temporary output, and the wrapper
  uses a standard-library PCM16 WAV writer instead of the failing
  torchaudio/libsndfile save boundary. A fixed-phrase synthesis produced a
  valid mono 22.05 kHz WAV and exited successfully; the temporary file was
  removed without playback.
- Full no-hardware regression passes 352/352 when its temporary directory is
  placed on the project filesystem; the system `/tmp` quota remains exhausted
  and is not modified by this task.
- Current notification-only semantics remain: assistant response text is
  silent; only completion, failure, approval, and required-input categories are
  spoken. A fresh Alpha launch is required to load and exercise this fix.

### Latest Phase: Exclusive PTT Device Ownership

Status: fixed and unit-verified on 2026-09-04.

- Multiple voice-enabled Alpha instances were all reading the same
  `/dev/input/deskhelm-ptt-kbd`, so one right-Alt edge was dispatched to every
  configured session.
- The evdev listener now takes an exclusive device grab and releases it during
  cleanup. A second listener fails closed with a fixed "already owned" error.
- The focused right-Alt, Alpha, and Voice integration suite passes 37/37.
- The prior live Alpha has now been stopped; one active PTT owner must be
  selected before the next live check.

### Latest Phase: VoiceStudio External Project Review

Status: public upstream review and documentation completed on 2026-09-04.
Per operator request, no tests were run. No DeskHelm source, dependency, model,
runtime, or live Alpha process was changed.

- VoiceStudio is an active local speech platform with a Tauri desktop shell,
  FastAPI backend, model/engine catalog, OpenAI-compatible HTTP, WebSocket,
  MCP, and native dictation-control surfaces. The public repository metadata
  currently reports 1,907 commits, 16,306 stars, and AGPL-3.0 application code.
- Reusable ideas for DeskHelm are true streaming Sherpa-ONNX ASR models,
  explicit engine jobs and acceptance criteria, lazy/warm model lifecycle,
  bounded crash-isolated sidecars, first-audio/RTF measurements, and a split
  capture/data plane versus target-control plane.
- Do not adopt VoiceStudio's OmniVoice default or PocketTTS as the DeskHelm
  default: OmniVoice's model/tokenizer terms include CC-BY-NC and separate
  community terms, while PocketTTS is gated and does not cover Chinese. Keep
  the existing Piper/Kokoro/IndexTTS decision and evaluate Sherpa bilingual
  streaming ASR as an additional candidate rather than replacing it blindly.
- VoiceStudio's Docker and remote-worker deployment is useful for a future
  headless GPU service, but direct PTT/PipeWire capture should remain host-side;
  no VoiceStudio dependency or AGPL code is to be copied into DeskHelm.

Sources: <https://github.com/debpalash/VoiceStudio>,
<https://github.com/debpalash/VoiceStudio/blob/main/docs/speech-platform.md>,
<https://github.com/debpalash/VoiceStudio/blob/main/docs/engines/sherpa-onnx-asr.md>,
<https://github.com/debpalash/VoiceStudio/blob/main/docs/engine-acceptance.md>,
<https://github.com/debpalash/VoiceStudio/blob/main/docs/install/docker.md>.

Detailed notes: `docs/research/2026-09-04-voicestudio-review.md`.

### Latest Phase: IndexTTS-2.5 Deployment

Status: runtime, auxiliary models, and first protected smoke synthesis
completed on 2026-09-03. No Alpha process was changed.

- The complete `indextts` source needed by `infer_v2_5.IndexTTS2` is present
  under ignored `references/vendor/index-tts-src/`.
- An isolated Python 3.11 `uv` runtime is synchronized under ignored
  `references/vendor/index-tts-runtime/`, using the dedicated
  `/home/tope/.cache/deskhelm-index-tts` cache and CUDA 12.8 wheels.
- `tools/run-index-tts25.py` is a bounded stdin-based wrapper matching the
  DeskHelm external TTS provider contract. Alpha auto-selects this wrapper
  whenever the configured TTS directory has `config.yaml` and `voice.wav`;
  Kokoro remains selected until the model/reference files arrive.
- Alpha also discovers a marked IndexTTS child directory (such as
  `indextts-2.5/`) beneath a directory that still contains Kokoro/Piper assets,
  so the model download does not overwrite existing voice files.
- ModelScope's main `IndexTeam/IndexTTS-2.5` checkpoint (about 5.2 GiB) is in
  the encrypted TTS resource directory's `indextts-2.5/` subdirectory. A
  public demo `voice_01.wav` is installed there as temporary `voice.wav` for
  smoke testing; replace it with an authorized reference before normal use.
- The first smoke synthesis downloaded the official auxiliary `w2v-bert` and
  feature resources, loaded on the RTX 5060, and produced
  `/tmp/deskhelm-index-tts-smoke.wav` (mono PCM16, 22.05 kHz, 101,932 bytes).
  The file was played once through the current PipeWire default sink.
- `deskhelm alpha --check --voice` passes with the IndexTTS directory selected;
  a manual `deskhelm alpha --tui --voice` audition remains operator-driven.

### Latest Phase: Alpha Stale Socket Cleanup

Status: fixed and verified on 2026-09-03.

- A prior `wechat_miniprogram` TUI exit left the Codex app-server process
  group alive on the derived `-codex-upstream.sock`, while the main Bridge
  socket had already disappeared. The launcher therefore killed too narrow a
  process set and the next launch failed at app-server startup.
- The launcher now collects owners from the Bridge, TUI-proxy, and upstream
  sockets, terminates their complete process groups, escalates after a bounded
  wait, and removes only ownerless remnants after explicit rebuild
  confirmation.
- The orphaned `wechat_miniprogram` app-server/PTT groups were stopped and all
  three derived sockets are currently clear. Other Alpha instances were not
  changed.

### Latest Phase: Managed TUI Ctrl-C Exit

Status: fixed and unit-verified on 2026-09-03.

- Full-screen Codex can disable pty line-discipline `ISIG`, so forwarding a
  confirmed raw/Kitty Ctrl-C byte did not always terminate the managed TUI.
- `run-with-pty.py` now sends the confirmed second Ctrl-C directly to the
  child process group and suppresses the duplicate byte/Kitty sequence. The
  first press remains clear-input only, with the existing bounded window.
- `tests.test_run_with_pty` passes with the new confirmation-forwarding check;
  an interactive Alpha exit should be manually rechecked once.

### Latest Phase: PipeWire Duplicate-Name Preflight

Status: fixed and verified on 2026-09-03.

- WirePlumber exposed two transient RAOP sink objects with the same stable
  `node.name`, causing inventory validation to reject the entire audio path
  before Alpha startup.
- Audio discovery now deduplicates repeated source/sink names within a fresh
  snapshot, retaining the last object, while still rejecting malformed or
  ambiguous defaults.
- `audio status --list` and `deskhelm alpha --check --voice` now pass with the
  current Fedora audio graph; no running Alpha process was changed.

### Previous Phase: Alpha TTS Silence Diagnosis

Status: diagnosed on 2026-09-03; no process was terminated and no source
change was required.

- The new `wechat_miniprogram` Alpha is running with `--voice-provider local`,
  Paraformer ASR, and the Kokoro artifact directory. Its process tree confirms
  the Voice Gateway/PTT path is enabled, but the current encrypted TTS
  directory does not contain the IndexTTS marker layout, so provider selection
  remains Kokoro.
- DeskHelm voice output is notification-only by design: it speaks fixed,
  privacy-safe completion, failure, approval-attention, and input-required
  notices. It does not synthesize every Agent assistant response. In `--yolo`
  mode approval notices are also not expected.
- If a qualifying notification occurred but no audio was heard, inspect the
  Alpha terminal for the fixed `voice_output_failed` event and verify the
  selected PipeWire sink; the TTS worker reports failures without exposing
  text/audio content.

### Previous Phase: Automatic Per-Directory Alpha Sockets

Status: implemented and shell-verified on 2026-09-03; no process was
terminated.

- A live Alpha stack is still running from 2026-09-02 15:13:42. Its Bridge
  process is PID 702178, the PTT child is PID 702197, and the Codex app-server
  processes are PIDs 702115/702122.
- The process was launched from the DeskHelm checkout
  `/home/tope/project/next_keyboard` but its Agent working directory is
  `/home/tope/Documents/fedora_enhance`. It owns the default user socket
  `/run/user/1000/deskhelm/bridge.sock` and the derived Codex sockets.
- Alpha intentionally rejects an existing default socket, so two instances
  using the default endpoint cannot run concurrently. Multiple isolated Alpha
  instances are supported by giving each a unique `--socket`; the derived
  app-server/proxy socket names then remain separate. Use unique session and
  client IDs as well, and avoid sharing one physical PTT listener between
  instances.
- `tools/deskhelm-user-launcher` now derives a stable per-launch-directory
  socket under the user runtime directory when `--socket` is omitted. If that
  socket already exists, an interactive terminal asks whether to interrupt the
  owning process and rebuild; non-interactive startup fails closed. Existing
  TUI instances are not auto-attached because the proxy cannot safely share a
  primary frontend.
- Core `deskhelm alpha` now uses the same directory-scoped default when invoked
  directly, while `deskhelm bridge` retains the shared `bridge.sock` endpoint.

### Previous Phase: IndexTTS-2.5 Preferred TTS

Status: implemented and software-verified on 2026-09-03; no model/audio test
was run.

- Alpha's code-level fallback is Piper `zh_CN-chaowen-medium`; when the
  configured TTS directory contains Kokoro, provider detection selects Kokoro
  instead. The current encrypted default is documented as Kokoro-82M with
  `zf_xiaobei` for Chinese/mixed text and `af_heart` for English-only text.
- The committed Piper/Kokoro benchmark measured Piper as the low-latency
  baseline (cold load 1.99 s, synthesis p50 174 ms, peak RSS 1.01 GiB) and
  Kokoro as the quality-oriented candidate (cold load 3.36 s, p50 1,090 ms,
  peak RSS 2.52 GiB). The run did not include human listening, so it is not a
  naturalness verdict.
- IndexTTS-2.5 is a separate zero-shot voice-cloning TTS model (0.8B listed in
  its upstream evaluation), with stronger expressive/emotion and pronunciation
  controls than the current fixed voices. Upstream does not publish a single
  minimum-VRAM number; its stack is materially heavier than Kokoro and should
  be treated as an opt-in experiment until manually measured.
- IndexTTS-2.5 requires a reference speaker audio and uses the restrictive
  bilibili Model Use License. It should not replace the default notification
  path without a license review, bounded latency/cancellation integration, and
  human listening evaluation.
- Alpha now detects a standard IndexTTS directory (`config.yaml` plus
  `voice.wav`) before Piper. If IndexTTS artifacts are absent, Piper is the
  automatic fallback; explicit Kokoro CLI selection remains compatible.
- Added a bounded external-CLI `IndexTtsProvider` that sends synthesis text via
  stdin, writes only to a temporary WAV, enforces mono S16LE/output/time limits,
  and terminates its process group on cancellation or timeout. The IndexTTS
  Python/CUDA environment remains external to `voice/runtime`; the executable
  can be replaced with an operator-provided IndexTTS-2.5 wrapper when using the
  repository's `indextts2` CLI entry point is not suitable for the 2.5 release.

### Previous Phase: Unified ASR/TTS Model Research

Status: official upstream research completed on 2026-09-03; no model,
runtime, audio configuration, or application source was changed.

- `Qwen3-ASR-0.6B` is ASR-only. Its upstream model family provides language
  identification, offline/streaming transcription, and forced alignment; it
  does not include a speech decoder or TTS path.
- The user's remembered “index2” is `IndexTTS-2` (the repository now advertises
  `IndexTTS-2.5`). It is a zero-shot, reference-audio voice-cloning TTS system,
  not ASR and not a unified speech-to-speech model. The published 2.5 model is
  listed as 0.8B and supports Chinese, English, Japanese, Spanish, and Arabic,
  with emotion, speed, and pronunciation controls. Its model license is the
  bilibili Model Use License, not Apache/MIT; retain the license and review its
  redistribution and commercial-use restrictions before bundling.
- `Qwen3-Omni-30B-A3B-Instruct` is a genuine Thinker-Talker model with audio
  input and text/audio output (19 speech-input languages, 10 output languages).
  Official BF16 minimum-memory estimates are 78.85 GB for a 15-second video
  input (including the talker); disabling the talker saves about 10 GB. It is
  therefore not viable on the local RTX 5060 8 GB GPU.
- `MiniCPM-o 4.5` is another genuine bilingual (Chinese/English) end-to-end
  speech model with voice cloning and full-duplex streaming. The official demo
  asks for at least 28 GB Nvidia GPU memory; its quantized llama.cpp-omni path
  is a separate edge experiment and still needs integration work.
- `GLM-4-Voice-9B` is an end-to-end Chinese/English spoken-chat stack composed
  of a 9B chat model plus tokenizer and streaming speech decoder. Upstream
  provides BF16 and Int4 launch modes but no reliable minimum-VRAM figure;
  treat it as a multi-component experiment, not an 8 GB production choice.
- `Step-Audio` is conceptually unified, but its open `Step-Audio-Chat` model is
  130B with a stated 265 GB minimum, while `Step-Audio-TTS-3B` (8 GB minimum)
  is TTS-only. It is not suitable for this workstation.
- `Moshi` is a 7B full-duplex speech-text dialogue model with a stated 24 GB
  GPU requirement for the PyTorch path. Its weights are CC-BY 4.0, but it is
  not a practical Chinese DeskHelm candidate.
- Recommendation: keep DeskHelm's modular ASR -> Agent -> TTS boundary. For
  this 8 GB machine, evaluate Qwen3-ASR and a small standalone TTS separately;
  consider MiniCPM-o/GLM-4-Voice only as opt-in research branches, never as a
  default replacement. No further benchmark was run because the operator
  requested manual testing.

Sources: <https://github.com/QwenLM/Qwen3-ASR>,
<https://github.com/index-tts/index-tts>,
<https://github.com/QwenLM/Qwen3-Omni>,
<https://github.com/OpenBMB/MiniCPM-o>,
<https://github.com/THUDM/GLM-4-Voice>,
<https://github.com/stepfun-ai/Step-Audio>,
<https://github.com/kyutai-labs/moshi>.

### Previous Phase: Local ASR Replacement Research

Status: GitHub research completed on 2026-09-02; no model, runtime, audio
configuration, or application source was changed.

- The current Paraformer experience is reported as weak on Mandarin-English
  code switching and does not supply the punctuation required for voice coding
  prompts.
- FireRedASR2S is the leading evaluation candidate because its upstream README
  explicitly claims Chinese/English code-switch ASR and ships a separate
  Chinese/English punctuation model. Its all-in-one composition requires a
  new optional PyTorch runtime and model-license verification.
- SenseVoiceSmall can emit punctuation and ITN through its `use_itn=True`
  path and has an ONNX/GGUF CPU route, but its upstream materials do not make
  the same explicit code-switching claim. It remains the low-footprint
  candidate.
- Whisper `large-v3-turbo` has a mature local `whisper.cpp` route already
  reflected in the repository boundary and is a 809M multilingual model with
  an upstream approximate 6 GB VRAM requirement. It remains the practical
  general multilingual baseline. Qwen3-ASR offers 0.6B/1.7B local GPU models
  and vLLM-only streaming. Its official ModelScope artifacts are 1.876 GB
  BF16 for 0.6B and 4.698 GB BF16 for 1.7B before runtime/KV-cache overhead;
  on the local 8 GB GPU, test 0.6B alone first and do not combine 1.7B with
  the optional 0.6B forced aligner.
- A bounded two-track feasibility run completed. `whisper.cpp` v1.9.2 built
  CPU-only with four jobs and without CUDA/Vulkan, but the official 547 MiB
  `large-v3-turbo-q5_0` mirror timed out before transfer; no Whisper
  transcription result is claimed. The temporary no-model build remains under
  `/tmp` because the environment disallowed its removal.
- FireRedPunc completed from an external cache: 2.288 s CPU load and 0.060 s
  for three public Chinese-English technical texts. It restored sentence
  punctuation but lowercased identifiers and changed `README.md` to
  `readme. Md`; it cannot be applied blindly to coding commands. The FireRed
  AED checkpoint is 4,731,558,506 bytes. Its isolated CUDA 12.8/PyTorch
  runtime did not finish its 600 s protected synchronization deadline, so no
  AED weight transferred and no GPU ASR result is claimed.
- Qwen3-ASR-0.6B was evaluated in a separate Python 3.12 / CUDA 13 runtime
  using an isolated `UV_CACHE_DIR`; this avoided the active Alpha process's
  shared-cache lock without interrupting it. The official 1.876 GB BF16 model
  loaded successfully on the local RTX 5060 8 GB GPU. Process memory measured
  by `nvidia-smi` was 1,980 MiB (PyTorch peak allocation 1,789.2 MiB).
- The official Chinese and English files decoded correctly in 1.033 s and
  0.576 s respectively after a 0.942 s model load. A 19.255 s sequential
  Chinese-then-English composition decoded in 3.024 s, emitted punctuation,
  but labeled the complete clip English and converted most of its Chinese
  prefix to English-like text. This is evidence that the 0.6B model fits the
  hardware and handles each language, not evidence that it meets code-switch
  accuracy requirements.
- Next, only on operator request: use a separately authorized and versioned
  natural Mandarin-English coding corpus to measure ASR, symbols, and
  punctuation. Keep audio, weights, results, and model artifacts outside Git.

### Previous Phase: Resume-Aware Voice Thread Targeting

Status: implemented and software-verified on 2026-09-02; restart Alpha before
using voice after a resumed session.

- The resume-picker connection fixed in the previous phase allowed the TUI to
  switch threads, but its separate DeskHelm adapter observer remained attached
  to the prior thread. Voice prompts could therefore be submitted invisibly to
  that old thread.
- A confirmed proxied `thread/resume` now gives only the selected thread ID to
  the adapter, which resumes its own observer connection before routing later
  voice controls. Prompts, approval content, and rich interaction data remain
  private to their original connections.
- Resource-limited proxy and app-server adapter regressions pass 30/30,
  including a voice prompt routed to the picker-selected thread.

### Previous Phase: Managed-TUI Resume Picker Connection

Status: implemented and software-verified on 2026-09-02; restart Alpha before
retrying `/resume`.

- The managed TUI proxy previously serialized all frontend connections. Codex
  opens a second short-lived app-server connection for its `/resume` picker,
  so that connection could not complete its WebSocket handshake.
- The proxy now permits one bounded auxiliary picker connection alongside the
  primary TUI. It forwards normal app-server frames only and cannot replace the
  primary approval channel or receive voice approval decisions.
- Resource-limited TUI-proxy, app-server adapter, and Alpha tests pass 46/46,
  including a primary-approval regression while the auxiliary picker connects.

### Previous Phase: Explicit Managed-TUI YOLO Mode

Status: implemented and software-verified on 2026-09-02.

- `deskhelm alpha --tui --yolo` now launches the managed remote Codex TUI with
  Codex's explicit full-access, no-approval `--yolo` mode. Default Alpha still
  launches read-only Codex with `on-request` approvals.
- YOLO affects only the remote TUI command; it does not weaken Bridge, proxy,
  session, or queue bounds. Since Codex bypasses approvals, neither keyboard
  nor voice approval routing is invoked in that mode.
- Resource-limited Alpha regression validation passed 18/18, including exact
  remote command construction. A separate `deskhelm alpha --check --yolo`
  preflight against an isolated temporary socket also passed.

### Previous Phase: Project-Labelled Voice Notifications

Status: implemented and software-verified on 2026-09-02.

- Automatic completion, failure, approval-attention, and input-required
  notifications now begin with the bounded target project identity, for
  example `项目 deskhelm，Codex 已完成工作，请查看结果。`.
- The label is derived only from the opaque `project_id`, is normalized to a
  64-character safe display value, and never resolves or announces a working
  directory. Explicit `speak` controls remain unchanged.
- The notification path continues to omit prompts, assistant text, approval
  summaries, tool output, and failure details. A regression verifies a
  non-default project label without exposing private failure content.
- Resource-limited focused validation passed 15/15:
  `PYTHONPATH=bridge:adapters/codex uv run --project voice/runtime --locked
  --no-sync python -m unittest tests.test_voice_integration
  tests.test_client_voice_status -v`.

### Previous Phase: Voice Source Preflight Diagnosis

Status: diagnosed on 2026-09-02; no source or dependency changes required.

- The encrypted manual voice-source selection is currently absent from the
  PipeWire inventory. Alpha correctly fails closed rather than silently falling
  back to a different microphone.
- PipeWire currently exposes the built-in analog-stereo source as its default.
  An explicit temporary selection of that source passes the complete
  no-audio `deskhelm alpha --check --voice` preflight.
- To use a manually selected USB microphone again, reconnect it and verify its
  stable node name before restoring that selection. Do not replace the saved
  selection merely to force fallback.

### Previous Phase: Manual Managed-TUI Smoke Test

Status: live-verified on 2026-09-01 in an isolated temporary work directory;
no repository or runtime source files changed. The temporary directory and its
single generated file were removed after validation.

- `deskhelm alpha --tui` started the managed app-server, Bridge, proxy, and
  remote Codex TUI successfully. A real text-only turn completed with the
  requested fixed response and no tool or file activity.
- A separate `workspace-write` turn created one temporary file with its exact
  expected content. The TUI reported completion, independent local inspection
  verified the result, and cleanup removed the file and directory.
- A third read-only TUI turn requested one isolated file write and displayed
  Codex's explicit command-approval card. The operator selected the one-time
  approval (not persistent permission); the command completed and an
  independent inspection verified its exact expected result before cleanup.
  This confirms the managed TUI's original approval channel. Microphone/voice
  routes remain intentionally out of scope; the deterministic disconnect and
  stale-approval regression below remains the DeskHelm control-router safety
  evidence.
- The automated terminal harness had to emit Kitty-protocol Enter after the
  TUI requested that keyboard mode; an ordinary interactive terminal supplies
  the required protocol itself. No DeskHelm source change is indicated.

### Previous Phase: App-Server Disconnect Approval Safety

Status: software-verified on 2026-09-01. This focused regression phase adds
coverage only; no runtime behavior changed. It verifies that a direct
app-server approval cannot survive a transport disconnect, while the known
thread can reconnect and complete a later text-controlled turn with the right
correlation.

- Added a deterministic adapter regression for this order: direct approval,
  synthetic disconnect, same-thread reattachment, rejected stale decision,
  then successful fresh turn completion. Approval and source-path values remain
  private in the published event and test assertion surface.
- The first version of the test mismatched the fake `turn/start` response ID
  with the injected lifecycle notification and therefore modeled a different
  turn. Correcting the fixture made the expected correlation pass without a
  production-code change.
- Resource-limited focused adapter, managed-TUI proxy, and control-router
  tests pass 40/40. Manual microphone and human-managed TUI validation remains
  intentionally out of scope for this unattended phase.

### Previous Phase: Consecutive Managed-TUI Voice Approvals and Female TTS

- Codex CLI 0.150.1 added `item/permissions/requestApproval`. The adapter and
  managed-TUI proxy now correlate that request, grant only its requested subset
  for the current turn, and reject with an empty permission set. Voice-granted
  permissions never persist to the session.
- Exact voice approval phrases are `批准`, `确认`, `同意`, `允许`, and `可以`;
  exact rejection phrases are `拒绝`, `不同意`, `不允许`, and `取消`. When no
  approval is pending, the same transcript remains ordinary prompt text.
- After a completed turn, the next voice prompt now starts a new Codex turn
  instead of attempting to steer the completed one. Control failures publish a
  fixed actionable category instead of the generic “工作未完成” notice.
- Alpha now detects Piper or Kokoro from the configured TTS directory. The
  current encrypted default selects Kokoro female voice `zf_xiaobei` for
  Chinese or mixed notifications and `af_heart` for English-only notifications.
  Kokoro dependencies are pinned in `voice/runtime`; a real in-memory Chinese
  synthesis produced 24 kHz mono PCM for about 3.325 seconds without playback
  or file persistence.

- Root cause: a turn started by the Codex TUI receives its server-initiated
  approval request only on the TUI app-server connection. DeskHelm's separate
  observer saw only `waitingOnApproval`, which has no request ID and therefore
  could not safely execute spoken `批准` or `拒绝`.
- Added a bounded dependency-free local WebSocket proxy between the managed TUI
  and Codex app-server. Alpha now uses distinct Bridge, TUI-proxy frontend, and
  app-server upstream sockets; the adapter keeps its separate observer/control
  connection to the upstream.
- The proxy forwards ordinary JSON unchanged, captures only bounded approval
  correlation metadata, and publishes the existing fixed private-content-free
  approval summary. It never injects terminal keystrokes or infers a decision
  from status.
- Voice decisions return on the original TUI/upstream JSON-RPC channel.
  Keyboard-first forwards normally and clears DeskHelm's pending record;
  voice-first sends one `accept` or `decline` and suppresses the late duplicate
  TUI response. Disconnect, expiry, and capacity remain bounded and fail
  closed; excess approvals stay keyboard-actionable.
- Alpha waits for the app-server upstream before Bridge startup and for the TUI
  proxy before launching Codex, then removes all three sockets during cleanup.
  The existing hardware-independent `--tui`, explicit `--voice`, PTY relay,
  and two-press Ctrl-C behavior are unchanged.
- The first real model turn exposed a transport bug: the proxy treated its
  bounded 64-entry forwarding queue as a fail-fast queue and closed the
  app-server connection when a notification burst filled it. The TUI reported
  `Connection reset without closing handshake` and Alpha stopped. Queue
  saturation now pauses upstream reads with bounded stop-aware polling, so the
  Unix socket applies backpressure without dropping, reordering, or closing a
  healthy streamed turn.
- A deterministic regression sends 128 ordered 4 KiB notifications through a
  one-entry proxy queue, delays TUI reads briefly, and verifies that every frame
  arrives before another bidirectional request succeeds.
- Live acceptance then found a second lifecycle bug: the first spoken approval
  executed correctly, but the second approval in the same session did not
  react. The proxy removed its voice-first duplicate suppression as soon as it
  forwarded `serverRequest/resolved`; the real TUI sent its late response after
  that notification, so DeskHelm forwarded a duplicate decision back to
  app-server. The approval attention state also lacked an exact proxy
  acknowledgement boundary for announcing the next approval.
- Voice-first suppression now survives `serverRequest/resolved` until the late
  TUI response or a fixed 30-second post-resolution expiry. It is bounded by the
  approval capacity, acknowledges the adapter exactly once, accepts later reuse
  of the same JSON-RPC ID as a new token, and clears attention so a later
  approval in the same turn can announce independently. Voice decisions use a
  separate bounded priority queue, so streamed-notification backpressure cannot
  reject them.
- The consecutive-approval regression covers this exact order: voice decision,
  app-server resolution, late TUI duplicate, reused request ID, and a second
  voice decision. The burst test also resolves a voice approval while 128
  notifications cross a one-entry forwarding queue.
- Focused proxy, adapter, Alpha, Voice integration, and TTS-selection tests pass
  66/66. The exact voice phrase tests cover approval, ordinary prompt fallback
  without a pending request, and ambiguity refusal. The resource-limited
  complete no-hardware suite passes 333/333. A real Codex CLI 0.150.1 run from
  `/home/tope/project/wechat_miniprogram` completed a streamed model turn through
  `deskhelm alpha --tui`, displayed the requested `STREAM_OK`, exited normally
  through `/quit`, printed `local alpha stopped`, and left no DeskHelm sockets.
  The test requested no tools or file changes. Live `deskhelm alpha --tui
  --voice` must be restarted to load the consecutive-approval repair before
  final microphone confirmation.
- A 2026-08-31 isolated live `codex exec` probe against Codex CLI 0.151.0
  verified text-only Agent behavior without audio: a `workspace-write` turn
  created one exact expected file while preserving its source fixture, and
  read-only turns completed without creating a requested file or modifying the
  fixture. Prompts were delivered on stdin and Agent output was discarded.
  A noninteractive PTY cannot substitute for the managed, human-operated
  remote TUI: it did not create a discoverable user thread. A direct app-server
  probe did verify two client initializations but confirmed that an explicitly
  created pre-rollout thread cannot be resumed by a second client. This remains
  a documented fail-closed boundary, not a DeskHelm defect; managed-TUI
  approval validation still requires the interactive live check listed below.

Phase 0 is working. The earlier baseline is pushed to `main`; the newer local
commits listed above require a separate push. The Bridge has a Unix socket transport,
normalized `AgentEvent v1`, CLI simulator, Codex hook adapter, `StateStore`,
in-process subscriptions, `SessionRegistry`, terminal projection, bounded
concurrent connections, and negotiated state publishers. `InteractionEvent v1`
and `ControlCommand v1` are implemented and covered by compatibility fixtures,
and negotiated state subscribers receive atomic snapshots followed by ordered
live updates. Negotiated interaction publishers and bounded live-only
interaction subscribers are implemented without entering state projection.
`ControlRouter`, bounded control state, correlated results, and negotiated
controller connections are implemented. Modern adapter publishers can now
declare capabilities and drive complete session registration, disconnect,
restore, and release lifecycle with connection ownership. Versioned Codex JSONL
evidence is present. The opt-in bounded text Agent Gateway now handles targeted
prompt submission and interruption, streams normalized Codex JSONL interactions,
and supports timeout, cancellation, and process-local resume. The isolated,
bounded Voice Gateway skeleton is now implemented with fake capture, ASR, TTS,
and playback providers. Bridge composition completes the no-hardware
`PTT -> transcript -> Agent -> speech` path and routes targeted speech controls.
The local voice benchmark foundation now has a versioned synthetic corpus,
bounded provider-neutral runners and NDJSON observations, accuracy/latency/
resource summaries, and explicit licensing identity. The first real local audio
boundary is implemented: explicit raw PCM models plus bounded PipeWire capture
and playback providers support the current default devices or stable-name
overrides. They remain disabled by default but can be composed explicitly by
the Bridge CLI. A unified ignored Python 3.12 runtime has now executed the
pinned Paraformer and Piper providers together, and a real four-second PTT run
completed capture, final ASR, fixed-response TTS, and playback through the
current USB microphone and computer sink without saving PCM or transcript
text. The diagnostic does not call Codex, publish partials, or measure actual
first-speaker-audio. PipeWire and Voice Gateway capture now use
the frame-positioned streaming boundary: chunks retain one format and
contiguous absolute frame positions, and the Gateway aggregates them under
fixed chunk, byte, and duration limits until PTT release. Legacy batch capture
providers remain compatible. The streaming PCM and
VAD benchmark boundary now has its first reproducible real-audio implementation:
pinned FSDD sources, deterministic prepared samples, lazy WebRTC and Silero
ONNX adapters, and privacy-safe aggregate observations. The result validates
both paths but does not select a production VAD. The first real streaming ASR
baseline is also complete: a pinned Paraformer provider and external audio set
produced 24/24 successful observations. It remains a Chinese candidate rather
than DeskHelm's sole ASR because English word boundaries and short English
commands performed poorly. The first Piper/Kokoro TTS comparison is complete
with 36/36 successful observations per candidate. Piper is now the initial
low-latency notification baseline; Kokoro remains the quality candidate, and a
final production voice awaits human listening, live playback, recovery, and
packaging license evidence. Application-level local audio selection and
diagnostics are now implemented: the CLI can resolve/list PipeWire defaults or
stable manual targets without opening audio, explicitly test an input while
discarding PCM, and explicitly play a bounded low-volume output tone. These
diagnostics do not activate model-backed voice. Negotiated controllers can now
start and release PTT for a complete session target. Each
release copies its matching press command ID, so stale or cross-session
releases cannot stop the active capture. The Bridge CLI can now explicitly
compose provisional PipeWire capture/playback, Paraformer ASR, and Piper TTS.
The path remains disabled by default, validates devices and artifact files
before startup, and loads model runtimes only on first use. Optional WebRTC VAD
is now attached as an explicit disabled-by-default advisory observer. It emits
bounded frame-positioned input activity, while PTT release remains authoritative
and final ASR always receives the complete bounded recording. VAD failures emit
one fixed non-terminal event and fall back to the unchanged PTT path.
The controlled microphone-only ASR diagnostic now pairs one versioned public
phrase with signal, accuracy, and latency metadata while suppressing provider
output and retaining neither PCM nor recognized text. Its first run produced
`voice_no_transcript`; the user later confirmed that they did not speak, so the
result is retained only as an unspoken negative control. A later user-confirmed
spoken run produced 27 characters, matched both expected keywords, and had
0.545455 CER. This verifies basic recognition and command-intent recovery, but
literal accuracy remains inadequate for code-sensitive commands. The controlled
diagnostic now also reports advisory speech segment count, active duration, and
active fraction without allowing VAD to gate ASR. An immediate-capture,
user-confirmed run improved CER to 0.181818 with full keyword accuracy and
0.579396 speech-active fraction; it is the current live baseline.
The second ASR baseline is now implemented and measured: SenseVoiceSmall through
`sherpa-onnx` uses a verified INT8 ONNX artifact, a lazy final-only adapter, and
the same privacy-safe diagnostic contract. Its public run completed 24/24
observations with 412.512 MiB peak RSS and 0.055813 mean RTF, but failed most
isolated English digit clips. A user-confirmed synchronized live run recovered
both keywords with 0.136364 CER and 1,328.308 ms cold-process final latency,
improving on the current Paraformer live phrase. Neither provider is selected
as production ASR; SenseVoice's custom FunASR model license also needs review.
The recovery phase now has an explicit retry boundary: provider failures release
their locks for a later request, cancellation is checked at each provider's
documented boundary, PipeWire sessions can be reopened after disconnect, fresh
default-device snapshots rebind, and missing manual stable names fail closed.
Live hot-unplug and hard in-flight cancellation measurements remain open.
The multi-phrase diagnostic phase is now implemented under ADR 0022. It accepts
up to eight repeated `--utterance-id` values, opens a fresh capture for each,
reuses one lazy provider for warm-path behavior, and marks each result as
requiring separate post-run confirmation. Four Chinese/mixed coding phrases
were run for the Paraformer/SenseVoice comparison. The first
SenseVoice batch completed all four captures and the user confirmed speaking
each phrase, but reported that the spoken pace did not fully keep up with the
phrase transitions; the result is therefore live diagnostic evidence rather
than a tightly synchronized provider comparison.
The repeated Paraformer batch also completed all four captures with `ok` status.
The user described the prompt cadence as faster than comfortable natural speech
and asked to begin analysis; the four records are treated as spoken with that
timing caveat. In that immediate batch Paraformer had lower mean CER but much
higher final latency, and neither provider recovered the mixed coding keywords
reliably. The later handshake batches favored SenseVoice on both CER and
latency, but remain separate human recordings.
ADR 0023 now adds an opt-in one-by-one `ready` stdin handshake before each
phrase capture, with a bounded timeout and fixed `voice_phrase_not_ready`
failure. The existing immediate mode remains unchanged. A live SenseVoice
handshake batch completed all four phrases with `ok` status, followed by a
matching Paraformer handshake batch. The cleaner evidence favors SenseVoice on
this small live set. The CER/keyword spacing inconsistency found in that run is
now fixed in code; historical live keyword scores remain pre-fix because
recognized text was not retained.

The first recovery sub-phase is now complete under ADR 0024. Voice Gateway ASR
has a configurable 30-second default deadline (bounded to 120 seconds), emits
`voice_asr_timeout` on expiry, returns PTT to `idle`, and allows the next
explicit request to retry. Cancellation and close poll in short intervals so
they do not wait for the full ASR deadline. A native provider call may still
continue after the Gateway returns because hard cancellation remains
cooperative; no audio or transcript content is retained or logged.

The no-hardware whisper.cpp fallback phase now has an isolated provider
prototype. Official upstream sources confirm stable `v1.9.2`, MIT runtime/model
terms, in-memory float-PCM C API entry points, model hashes, and abort-callback
hooks. A DeskHelm-specific C shim and lazy Python provider are implemented and
validated against an external `v1.9.2` checkout. The first external-artifact
benchmark of multilingual `base`/`small` is also complete: `base` achieved
24/24 successful observations, while `small` achieved 21/24 and failed the
short `zero` clip in all repetitions. Neither model is selected for production.
No model weights or raw observations are tracked in Git.

The recovery-measurement phase has started with a no-hardware baseline. The
full dependency-free suite passes, and current PipeWire discovery resolves the
internal analog-stereo source and sink as both defaults. This differs from the
earlier USB-microphone baseline and establishes a concrete default-device
change state. Real disconnect, reconnect, and in-flight cancellation evidence
still needs a user-performed physical device action; no audio was opened during
this baseline check.

The user then reported switching the default input. A bounded two-second input
diagnostic succeeded and discarded its PCM, but PipeWire continued to expose
only the same internal analog-stereo source as the default. This verifies that
the current source is usable after the attempted switch, not default-device
change recovery; a source with a distinct stable name must appear before that
claim can be tested.

The user selected the internal microphone for subsequent work. An explicit
stable-name two-second diagnostic also succeeded and discarded its PCM, proving
both default and manual-selection paths for
`alsa_input.pci-0000_00_1f.3.analog-stereo`. Missing manual sources still fail
closed by contract, but live removal cannot be measured with this fixed source.

An attempted controlled ASR diagnostic stopped during runtime preflight. The
current host, the bounded `ubuntu24-r23` container, and retained benchmark
virtual environments lack both optional ASR imports (`funasr` and
`sherpa_onnx`); the retained host environments report Python 3.14.6 rather than
the previously recorded Python 3.12 runtime. No packages were installed, no
model was loaded, and no microphone capture began. Rebuilding an ignored,
pinned Python 3.12 runtime requires explicit user authorization because it
downloads large optional model-runtime dependencies.

The user approved a repository-wide uv migration. `uv` now owns Python
dependency resolution and environment synchronization: the root has `uv.lock`,
and `voice/runtime` is an independent Python 3.12 uv project with its committed
lock file. Its ignored `.venv` was rebuilt from 93 pinned packages, including
FunASR, NumPy, Piper, WebRTC VAD, and sherpa-onnx. Paraformer successfully ran
the controlled diagnostic. SenseVoice remains unavailable in this environment:
the `sherpa-onnx` wheel requires its matching external native ONNX Runtime ABI,
which is not provided by the pinned PyPI `onnxruntime` wheel. No unpinned native
artifact was added.

The exact-`ready` diagnostic path now handles stdin descriptors that cannot be
polled by `selectors`, returning the existing bounded not-ready outcome instead
of raising. With a PTY-assisted readiness exchange, the user confirmed speaking
the public Chinese repeat phrase during a 11,944.938 ms internal-microphone
capture. Paraformer completed with keyword accuracy `1.0`, CER `0.181818`,
estimated first partial `4,893.686 ms`, and final ASR `10,171.631 ms`. The
diagnostic retained no PCM or recognized text. Peak fraction `1.0` and clipped
sample fraction `0.00363125` leave input-gain clipping as an open risk.

The reusable single-project Alpha startup phase is now complete. `deskhelm
alpha` preflights environment-injected Paraformer/Piper artifacts, the isolated
voice uv runtime, Codex version, PipeWire selection, and the stable right-Alt
device without opening audio or loading model weights. It then owns one Bridge
child, one modern Codex session registration, and one PTT child. A real startup
smoke reached both `local alpha active` and `right-Alt PTT armed`; explicit
termination released the session, stopped both child process groups, removed
the temporary socket, and left no `pw-cat` process.

The bounded multi-project Agent routing phase is now complete under ADR 0031.
Bridge accepts repeated operator-owned `project_id=directory` mappings, validates
them before startup, and resolves each complete session to its configured Agent
working directory. `project_id` remains opaque protocol identity; adapters
cannot supply paths, and once mappings are configured an unmapped project fails
before any provider run or provider session record is created. The registry is
immutable until Bridge restart, defaults to 64 entries, and preserves the
existing single-directory and single-project Alpha behavior.

The noisy/conversational VAD evidence phase is now complete under ADR 0032. A
backward-compatible manifest v2 pins an 18-second annotated AMI meeting clip and
ESC-10 rain/clock noise, prepares clean plus 10 dB/0 dB SNR scenarios, and runs
bounded WebRTC and Silero profile sweeps. WebRTC mode 0 led aggregate replay F1
at `0.849`; the current live mode 2 reached `0.807`. Silero remained highly
precise but had low recall at all three tested threshold pairs. A three-second
ambient PipeWire negative control measured 181.362 ms to first chunk and
0.107/0.138 ms VAD processing p50/p95 with no speech event. This evidence does
not select or change the production default. The confirmed spoken mode 0/2
comparison is recorded below; normalized-gain and Chinese conversational
coverage plus live failure/recovery checks remain required. VAD stays optional,
disabled by default, advisory, and unable to end capture or gate final ASR.

The confirmed-live comparison preparation is now implemented. The live VAD
tool selects a versioned public corpus phrase, can wait up to 60 seconds for one
exact `ready` line by default under a configurable 120-second maximum, and
marks every result as requiring separate post-run speech confirmation.
Unpollable stdin falls back to a bounded daemon reader, so a chat-driven run
cannot block indefinitely or start recording on a failed readiness exchange.
This changes diagnostic synchronization only; it does not change the Gateway
live mode, VAD authority, or audio retention.

The first confirmed live mode comparison is complete on the current built-in
default source. The user separately confirmed fully speaking `zh-repeat-01`
during both six-second captures. Mode 0 emitted one 140 ms segment; mode 2
emitted no speech event. First-chunk timing was 180.146/178.516 ms and VAD
processing p50/p95 was 0.041/0.048 ms versus 0.045/0.050 ms. Mode 0 was only
marginally less severe; both profiles failed the spoken condition and neither
is acceptable as the live default. The diagnostic intentionally omitted signal
level metadata, so low/clipped input and profile mismatch remain unresolved.
PCM was discarded and no transcript or provider output was retained.

After a user-requested closer-microphone repeat, mode 0 detected only 100 ms and
mode 2 again detected no speech; the user confirmed fully speaking both times.
Distance therefore produced no usable improvement. The live diagnostic now
derives peak, RMS, clipping, and near-silence fractions incrementally without
retaining complete PCM, plus the same provisional input-level hint used by the
controlled ASR diagnostic. This is the next evidence boundary; it cannot change
system gain or grant VAD endpoint control.

The enhanced signal run resolved the failure. On the default built-in source,
the confirmed phrase measured only `0.001831` peak, `0.000114` RMS, and
`0.996492` near-silence fraction with no clipping; read-only PipeWire inspection
showed that source at 7% (`-70.40 dB`) on the internal-microphone port. The
connected PCM2902 USB source was available at 93% (`-1.98 dB`). DeskHelm made no
gain change. A user-confirmed explicit USB/mode 2 capture then measured
`0.328552` peak, `0.052386` RMS, `0.013756` near-silence fraction, no clipping,
and three segments totaling 2,800 ms. The earlier misses were therefore
dominated by the default source level, while mode 2 is usable on this phrase
with a healthy source. One phrase remains insufficient for production VAD
selection.

The bidirectional manual-Codex phase is now software-complete under ADR 0033.
The Alpha supervises one experimental `codex app-server`, prints the exact
`codex --remote unix://...` command for a manually operated TUI, and runs one
long-lived adapter beside Bridge and right-Alt PTT. The adapter performs the
required JSON-RPC initialization, directly attaches one loaded user thread,
retries resume until its event subscription is active, observes completion and
human-attention events, continues the same thread from PTT, and handles one-shot
approval or rejection through the exact phrases `批准` and `拒绝`. One
positively identified ephemeral internal thread is ignored; multiple user
threads remain ambiguous. Notifications remain fixed and private-content-free.
This does not attach to an unrelated plain `codex` process. Live validation now
covers a manually entered completion and a correlated Bridge-submitted
same-thread completion; a live approval prompt remains unexercised.

Alpha now also supports the optional single-terminal `--tui` mode. After the
app-server socket, Bridge session, and PTT controller are ready, it launches the
same remote Codex command as a foreground child in the current interactive
terminal. TUI input is protected by the launcher PTY: the first Ctrl-C clears
current input, and only a second press within the bounded confirmation window
reaches Codex.
TUI exit or a background-component failure triggers bounded cleanup of the
TUI, session, PTT, Bridge, app-server, and both sockets. Omitting `--tui`
preserves the existing printed-command workflow.

The source checkout now has a user-level launcher for cross-project use. The
current workstation's `~/.local/bin/deskhelm` resolves to
`tools/deskhelm-user-launcher`. It preserves the invoking directory as the
Alpha Agent working directory, runs the already synchronized root environment,
and dynamically loads only the required Alpha settings from this checkout's
encrypted `project-secrets` profile. Users can therefore start the managed TUI
from another repository with only `deskhelm alpha --tui`. The live TUI path now
uses a standard-library PTY relay so Alpha and Codex retain interactive terminal
semantics. Because the outer encrypted-settings wrapper forwards complete
lines, the relay writes live TUI updates directly to the controlling terminal
and performs boundary-safe streaming redaction of the injected Alpha values.

## Completed Work

- Created and pushed the initial project baseline.
- Selected `DeskHelm` as the product and repository name.
- Migrated the distribution, primary Python package, CLI, Codex hook CLI, and
  runtime socket path to DeskHelm names under ADR 0003.
- Preserved temporary `agent-io`, `agent-io-codex-hook`, and
  `python -m agent_io_bridge` compatibility entry points.
- Added repository Git attributes, ignore rules, local commit identity, and an
  HTTPS `origin`.
- Separated Bridge state storage, session projection, and terminal rendering.
- Added explicit session lifecycle and focus semantics: register, restore,
  focus, disconnect, release, and expiration.
- Accepted ADR 0005: one negotiated Unix socket with fixed connection roles,
  bounded frames and queues, legacy first-frame compatibility, and
  snapshot-based reconnect recovery.
- Replaced the sequential accept/read loop with a 16-connection bounded worker
  pool, configurable through `--max-connections`.
- Added byte-accurate 1 MiB UTF-8 NDJSON frame enforcement and process-local
  stream identifiers.
- Implemented `client_hello` / `server_hello` for negotiated publishers using
  the `agent_event_v1` capability and self-describing `agent_event` frames.
- Added a validated `send_negotiated_event` Python client path for integrations.
- Added structured protocol errors for invalid negotiation, unavailable roles
  or capabilities, and invalid negotiated frames.
- Preserved no-handshake `AgentEvent v1` clients, including continuing after a
  malformed event on an established legacy connection.
- Enabled negotiated `state_subscription_v1` clients with an atomic current
  snapshot followed by subscription-local ordered state updates.
- Added a separate subscriber limit below the total connection limit, an
  8-frame non-blocking queue per subscriber, a two-second first-frame deadline,
  and a two-second subscriber write deadline.
- Added terminal recovery behavior for slow, read/write-invalid, oversized, or
  capacity-exceeding subscriptions; reconnect always starts from a new snapshot.
- Enabled negotiated `interaction_event_v1` publishers and
  `interaction_subscription_v1` live subscribers with bounded non-blocking
  queues and no retained history.
- Allowed one adapter publisher connection to negotiate both state and rich
  interaction capabilities while requiring each subscriber connection to
  select exactly one plane.
- Verified rich interaction events do not update `StateStore`, session
  projection, terminal rendering, or ordinary Bridge logs.
- Implemented the `InteractionEvent v1` model for messages, tools, approvals,
  user-input requests, and task terminal events.
- Added four complete wire fixtures and validation/round-trip tests for
  `InteractionEvent v1`.
- Accepted ADR 0006: controls require a complete session target, issuer,
  timestamps, expiry, and idempotency key; approvals copy pending request
  metadata and are never automatically replayed.
- Implemented `ControlCommand v1` for focus, prompt submission, interruption,
  approval, rejection, speech, and stopping speech.
- Added seven complete control wire fixtures plus validation and expiry tests.
- Implemented `ControlResult v1` with fixed accepted/rejected codes and no
  private command or handler-error content.
- Implemented `ControlRouter` validation for controller identity, expiry,
  active full-session targets, approval metadata, and idempotency conflicts.
- Added bounded idempotency and pending/decided approval records. Capacity
  refuses new work instead of evicting live deduplication state.
- Enabled negotiated `control_command_v1` controller connections with one
  correlated result per structurally valid command.
- Made `focus` an internal router action and exposed explicit non-blocking
  handler registration for Agent and Voice Gateway commands.
- Consumed approval requests after any dispatch attempt, including ambiguous
  handler failure, so approval decisions cannot be replayed.
- Added unit and end-to-end coverage for events, display, Codex hooks,
  `StateStore`, and `SessionRegistry`.
- Recorded Phase 0 and Bridge-boundary ADRs.
- Added local voice-stack research and a no-hardware software roadmap.
- Accepted ADR 0007: adapter sessions declare adapter/runtime identity,
  capabilities, full session identity, and connection-owned lifecycle.
- Implemented `adapter_session_v1` register, disconnect, and release frames plus
  correlated lifecycle acknowledgements.
- Bound modern sessions to server-assigned publisher owners so an old
  connection cannot disconnect a replacement registration.
- Required lifecycle publishers to register active owned sessions before state
  or interaction publishing, while preserving the full session record through
  state updates.
- Made complete modern sessions targetable by `ControlRouter` and verified
  focus, disconnect rejection, restore, and release behavior end to end.
- Added versioned Codex `exec --json` fixtures with explicit official-document
  versus synthetic provenance and malformed/unknown/failure boundaries.
- Accepted ADR 0008: use an in-process, fixed-capacity generic Agent Gateway
  while keeping Codex process construction and JSON parsing in its adapter.
- Implemented bounded `submit_prompt` and `interrupt` handlers with one active
  run per complete session, fixed worker capacity, and bounded provider-session
  records.
- Implemented a deterministic fake provider for prompt streaming, resume,
  capacity, cancellation, and terminal-event tests.
- Implemented the Codex subprocess provider using `codex exec --json`, bounded
  JSONL frames, supported item mapping, forward-compatible unknown-event
  handling, timeout, process-group termination, and nonzero/malformed failure
  outcomes.
- Passed prompts through stdin so private text does not appear in process
  command-line arguments, and suppressed Codex stderr from ordinary logs.
- Added socket-level coverage proving a targeted controller prompt produces an
  assistant message and task terminal event for interaction subscribers.
- Accepted ADR 0009: keep Voice provider-neutral and Bridge-independent, bound
  capture and speech work, target every operation by complete session identity,
  and defer VAD until the streaming capture boundary is selected.
- Added `voice/deskhelm_voice` with validated models, capture/ASR/TTS/playback
  provider contracts, one-at-a-time PTT, and a bounded priority speech queue.
- Preserved raw and normalized transcripts separately and added content-free,
  recoverable lifecycle and provider-failure events.
- Added deterministic fake providers and full no-hardware coverage for
  `PTT -> transcript -> Agent -> TTS -> playback`, playback interruption, queue
  capacity, and targeted speech controls.
- Added Bridge composition for targeted prompt submission, `speak`,
  `stop_speaking`, and complete assistant-message speech routing. Voice queue
  exhaustion remains isolated from interaction publishers.
- Accepted ADR 0010: compare voice providers through a versioned synthetic
  corpus and bounded provider-neutral observations with explicit versions,
  licenses, anonymous system profile, latency, accuracy, and resource fields.
- Added 12 stable Chinese, English, and mixed-language utterances covering
  commands, paths, URLs, symbols, numbers, negation, repetition, and recovery.
- Implemented dependency-free fake/production ASR and TTS benchmark runners,
  bounded UTF-8 NDJSON I/O, CER, English WER, keyword accuracy, p50/p95 latency,
  CPU time, real-time factor, and optional RSS/VRAM summaries.
- Ensured provider failures use fixed codes without exception text; generated
  audio, microphone recordings, and local results remain outside Git by default.
- Verified PipeWire 1.6.8 tools, one default source and sink, raw record/playback
  options, and stable node names without capturing audio or changing settings.
- Accepted ADR 0011: local audio uses complete-frame raw S16LE PCM with explicit
  rate/channels, default or stable-name PipeWire targets, and bounded provider
  lifecycle semantics.
- Added dependency-free `pw-cat` capture and playback providers with byte/time
  limits, cancellation, fixed private-content-safe errors, and owned process
  groups that escalate from SIGTERM to SIGKILL.
- Added deterministic fake-`pw-cat` coverage for default/manual targets, PCM
  alignment, bounds, startup/nonzero failures, cancellation, and forced cleanup
  without opening live audio devices.
- Accepted ADR 0012: streaming capture uses contiguous complete-frame PCM chunks
  with absolute frame positions, and each VAD run owns an independent session
  with explicit end-of-stream flushing.
- Added streaming PCM, VAD event, and speech-segment models plus provider
  protocols for owned chunk streams and session-based VAD implementations.
- Extended the benchmark with bounded VAD samples and privacy-safe NDJSON
  observations for segmentation overlap, detection delay, processing latency,
  CPU time, real-time factor, and optional memory peaks.
- Added deterministic fake VAD sessions and coverage for chunk continuity,
  event ordering, precision/recall/F1, failure isolation, NDJSON, and CLI output
  without audio devices or model dependencies.
- Accepted ADR 0013: compare WebRTC VAD 2.0.14 and Silero VAD 6.2.1 ONNX as the
  first classical and neural baselines without adding optional runtimes to
  Bridge or committing third-party audio/model files.
- Added a versioned external VAD manifest with six FSDD speakers, pinned source
  revision and SHA-256 values, CC BY-SA 4.0 identity, and seven deterministic
  speech/silence composition scenarios.
- Added bounded preparation and run tools that verify downloads, convert to
  16 kHz mono S16LE, checksum prepared WAV files, load exact reference frame
  intervals, and keep raw observations under ignored storage.
- Implemented WebRTC and Silero ONNX streaming adapters with arbitrary-chunk
  buffering, cancellation, explicit flushing, bounded endpointing state, and
  lazy optional runtimes. Silero resets recurrent state/context per stream.
- Ran 35 observations per candidate on Linux x86-64/Python 3.14.6 with no
  failures. WebRTC recorded F1 0.894 and replay p50/p95 0.19/0.31 ms; Silero
  recorded F1 0.859 and 2.98/4.57 ms. The quiet trimmed corpus is explicitly
  insufficient for final production selection.
- Accepted ADR 0032 and added manifest v2 support for bounded source clips,
  annotation-derived non-overlapping speech intervals, deterministic real-noise
  SNR mixtures, and v1 compatibility.
- Pinned AMI `ES2008a` conversation audio plus manual annotation identity and
  ESC-10 rain/clock sources by revision, SHA-256, and CC BY licenses. Prepared
  audio and results remain ignored.
- Added bounded WebRTC modes 0-3 and Silero threshold sweeps with independent
  identities/summaries plus an explicit live PipeWire VAD timing diagnostic
  that discards PCM and emits no transcript or provider output.
- Added an opt-in exact-`ready` handshake and versioned public phrase to the
  live VAD diagnostic so separately confirmed mode comparisons do not race the
  capture window.
- Added streaming aggregate signal-level metrics to the live VAD diagnostic so
  confirmed misses can be separated from low/clipped input without saving PCM.
- Identified the built-in source's 7% input level as the live miss root cause
  and verified explicit USB/mode 2 speech activity with healthy signal metadata.
- Completed 15/15 observations per profile. WebRTC modes 0/1/2/3 recorded F1
  `0.849/0.827/0.807/0.716`; Silero threshold pairs recorded
  `0.377/0.295/0.252`. No provider failure or mix clipping occurred.
- Accepted ADR 0014: use `funasr/paraformer-zh-streaming` as the first streaming
  ASR baseline at immutable tag `apache-2.0-20260804`, resolved commit
  `fd2af606b37d7fb8b3b8a218c5be5b07b53ef6ba`, with a verified `model.pt`
  SHA-256 and isolated FunASR/PyTorch runtime.
- Added a bounded Paraformer adapter using official 600 ms chunks, one cache per
  transcription, serialized shared-model inference, lazy imports, cancellation
  checks, and explicit input/output limits.
- Added a versioned external ASR manifest plus bounded preparation and runner
  tools. The set pins one Apache-2.0 official Chinese sample, one official
  English sample with `unverified` audio license, and six CC BY-SA 4.0 FSDD
  samples; downloaded and prepared audio remains ignored.
- Ran 24/24 successful Paraformer observations on Python 3.12.3 with FunASR
  1.3.21 and CPU-only PyTorch/torchaudio 2.11.0. Mean CER was 0.438, English WER
  0.643, keyword accuracy 0.438, mean RTF 0.121, estimated first-partial p50/p95
  376/1,848 ms, cold load 4.97 seconds, and peak RSS 3.09 GiB.
- Accepted ADR 0020 and added a lazy, serialized, final-only SenseVoiceSmall
  adapter through `sherpa-onnx` 1.13.6. The verified INT8 release artifact is
  pinned by GitHub asset ID plus archive, model, and token SHA-256 values.
- Ran 24/24 successful SenseVoice observations on the same public ASR set. Mean
  RTF was 0.055813, final latency p50/p95 was 21.726/122.086 ms, cold load was
  681.488 ms, and peak RSS was 412.512 MiB. Long Chinese/English samples were
  strong, while six isolated English digits exposed a severe short-command gap.
- Ran one synchronized user-confirmed SenseVoice microphone diagnostic on the
  same public Chinese phrase. It matched both keywords with 0.136364 CER and
  1,328.308 ms cold-process final latency without saving PCM or recognized text.
- Accepted ADR 0021 and added deterministic recovery tests for ASR failure and
  cancellation boundaries, PipeWire process disconnect/retry, and default versus
  manual device changes. No implicit recognition retry or device fallback is
  introduced.
- Accepted ADR 0022 and added a bounded multi-phrase microphone diagnostic for
  repeated Chinese/mixed coding commands. Batch output contains only per-phrase
  privacy-safe metrics and fixed statuses.
- Ran the first four-phrase SenseVoice batch with fresh captures and warm
  provider reuse. All four records completed with fixed `ok` status and the
  user confirmed speaking each phrase; pace/synchronization was imperfect, so
  literal accuracy and keyword misses remain qualified evidence.
- Ran the repeated four-phrase Paraformer batch with fresh captures and warm
  provider reuse. All four records completed with fixed `ok` status; the user
  reported the cadence was faster than comfortable natural speech, so the
  records are qualified live evidence rather than paired comparison data.
- Compared the two qualified batches: Paraformer mean CER was `0.726123` versus
  SenseVoice `0.809943`; mean final latency was `2,158.430 ms` versus
  `442.620 ms`. Both providers had `0.166667` mean keyword accuracy and zero
  keyword accuracy on the three mixed coding phrases, so neither is selected.
- Accepted ADR 0023 and added an opt-in one-by-one phrase readiness handshake.
  Each bounded stdin wait requires an exact `ready` line, reports its mode in
  the privacy-safe output, and fails with `voice_phrase_not_ready` on timeout
  or EOF without opening capture.
- Ran the first live SenseVoice handshake batch. All four captures completed
  with `ok` status after separate readiness confirmations; mean CER was
  `0.177557` and mean final latency was `430.398 ms`. The `mixed-number-01`
  result had exact whitespace-insensitive CER but zero keyword accuracy,
  exposing a scoring-normalization caveat.
- Ran the matching live Paraformer handshake batch. All four captures completed
  with `ok` status after separate readiness confirmations; mean CER was
  `0.431913` and mean final latency was `2,267.219 ms`. SenseVoice had lower CER,
  two exact matches, and substantially lower latency in this small set; neither
  provider is a final production selection.
- Fixed keyword-score normalization to ignore whitespace consistently with CER
  for code-sensitive matching, and added regression coverage. Historical live
  handshake keyword scores remain unchanged and cannot be recomputed without
  retaining recognized text.
- Accepted ADR 0024 and added a bounded Voice Gateway ASR deadline with fixed
  `voice_asr_timeout` failure, responsive cancellation/close polling, explicit
  retry semantics, and tests for timeout recovery. Native provider hard
  cancellation remains unverified.
- Completed the dated whisper.cpp fallback research. It records upstream
  `v1.9.2`, MIT licensing, in-memory C API/abort hooks, model-size tradeoffs,
  provenance requirements, and a C-shim prototype boundary without adding a
  native dependency or model weights.
- Accepted ADR 0025 and added an explicit `WhisperCppAsrProvider` behind a
  versioned DeskHelm C ABI. The provider validates bounded 16 kHz mono S16LE,
  converts PCM to float32 in memory, serializes one native context, supports
  cooperative abort propagation, caps transcripts at 4,096 characters, and
  remains outside default local voice composition.
- Added `voice/native/whisper-cpp-shim/deskhelm_whisper_shim.{h,c}` and build
  notes. The shim compiles and links against an external whisper.cpp `v1.9.2`
  checkout with no repository-native dependency or model artifact.
- Added fake-native provider tests for lazy loading, conversion, fixed status
  outcomes, lock recovery, cancellation, and output bounds. No microphone,
  PCM persistence, transcript logging, or real model inference was performed.
- Added fake-native lifecycle coverage showing that an explicit provider close
  can be followed by a fresh model load and transcription; this validates
  in-process resource restart but not hard cancellation of a live native call.
- Added `tools/run-whisper-cpp-benchmark.py` to verify upstream model size/SHA,
  reuse the existing ASR observation contract, and record aggregate model,
  load, and resource metadata without changing default provider composition.
- Downloaded the pinned multilingual `base` and `small` artifacts only under
  ignored `references/vendor/whisper-cpp-bench/`. The three-repetition run
  measured `base` at 24/24 success, 1.2662 CER, 1,519.6 ms p50, 391.9 MiB RSS;
  `small` at 21/24 success, 0.5218 CER, 5,887.9 ms p50, 902.4 MiB RSS.
- Fixed the shim's auto-language configuration after source-level validation
  showed `detect_language=true` is detection-only in upstream `v1.9.2`.
  A real cancellation probe returned `VoiceCancelled` after about 1.326 s for
  `base` and 5.876 s for `small` from the cancel flag.
- Accepted ADR 0015: use Piper Chaowen as the initial low-latency notification
  TTS baseline while retaining Kokoro 82M as the quality candidate and
  deferring the final production selection.
- Added pinned Piper/Kokoro candidate and artifact manifests, bounded download
  and preparation tooling, lazy serialized providers, first-provider-chunk and
  RTF benchmark metrics, ignored WAV export, and interruption probes.
- Ran 36/36 successful observations per TTS candidate on Python 3.12.3 and four
  CPU threads. Piper recorded 0.034 mean RTF, 174/592 ms first-chunk p50/p95,
  and 1.01 GiB peak RSS; Kokoro recorded 0.185 RTF, 1,090/3,441 ms, and 2.52
  GiB. Piper cancelled between chunks; Kokoro completed before cancellation.
- Transcribed 24 generated WAV files through the pinned Paraformer model as an
  explicitly model-dependent intelligibility proxy. Both candidates were strong
  on Chinese-only keywords and weak on mixed coding commands; no human quality
  or MOS claim was made.
- Accepted ADR 0016: keep local audio selection explicit and process-local,
  prefer manual stable names over current defaults, fail without fallback when
  a manual target is absent, and separate read-only discovery from active tests.
- Added bounded `pw-dump`/`wpctl` discovery, validated provider/device
  composition, `deskhelm audio status`, explicit signal-only input diagnostics,
  and explicit short low-volume output diagnostics.
- Verified the current default USB mono microphone and internal stereo sink.
  Two-second default and manual-source input tests succeeded without saving
  PCM, and a 250 ms default-sink tone succeeded. A one-second input test
  produced no audio during device startup and failed explicitly.
- Accepted ADR 0017: expose targeted `press_ptt` and correlated `release_ptt`
  controls while keeping cancellation internal.
- Added control fixtures and Bridge composition handlers for external PTT. A
  release must match both the complete active target and the press command ID;
  idle, stale, and cross-session releases fail without changing capture state.
- Accepted ADR 0018: add an explicit provisional local voice composition while
  keeping all model-backed voice disabled by default and VAD deferred until a
  real streaming capture migration.
- Added `LocalVoiceConfig` preflight and `deskhelm bridge --voice-provider
  local`. Startup resolves PipeWire devices, verifies required Paraformer/Piper
  files, composes bounded providers, and leaves heavyweight imports and model
  loading lazy.
- Verified the local composition can start and stop against the current
  PipeWire graph and prepared artifact paths without opening audio or importing
  model runtimes.
- Added one pinned optional Python 3.12 CPU runtime specification for combined
  Paraformer/Piper execution. Piper Chinese synthesis additionally required
  `g2pw==0.1.1` and `sentence-stream==1.3.0`.
- Made the PipeWire `pw-cat` command prefix configurable. Distrobox execution
  uses the compatible host PipeWire 1.6.8 client through
  `host-spawn -no-pty pw-cat`; the container's 1.0.5 client lacks `--raw`.
- Reverified Paraformer at 8/8 and Piper at 12/12 in the unified runtime.
- Added an explicit privacy-safe live diagnostic and completed a real
  four-second `PTT -> final ASR -> fixed TTS -> playback` run. It emitted the
  complete lifecycle through `speech_completed`, retained only a one-character
  transcript count, and did not save or print audio or transcript text.
- Migrated PipeWire capture to an owned `PcmChunkStream` that emits complete
  frames with contiguous absolute positions while retaining `capture()` as a
  compatibility wrapper.
- Migrated Voice Gateway to prefer streaming capture, reject gaps and format
  changes, and enforce 10,000-chunk, byte, and duration bounds before final ASR.
- Added deterministic streaming capture fakes and coverage for release gating,
  stream cleanup, split-frame buffering, discontinuity, cancellation, byte
  limits, and legacy batch compatibility.
- Distinguished an explicit empty ASR result as `voice_no_transcript` without
  exposing provider errors or private content.
- Rechecked the live stream: a four-second privacy-safe signal diagnostic
  captured 128,286 bytes with peak 0.399 and RMS 0.0617. Two full attempts
  reached transcription but produced no final text; the second included a
  user-confirmed spoken sentence. No PCM or transcript was retained.
- Recorded ESP32-S3 wireless-audio research: BLE HID for keyboard controls,
  reliable BLE/Wi-Fi state, and Wi-Fi Opus as the preferred future voice path.
- Selected a simpler local POC path: follow the computer's current PipeWire
  default capture and playback devices, with optional stable-name overrides and
  no Opus.
- Set the future product preference: manual source selection first, then a
  connected DeskHelm keyboard microphone, then the computer default source.
- Reviewed two public Agent I/O projects and extracted streaming, adapter,
  fixture, observability, privacy, and idempotency lessons.
- Added the project constitution and durable DeskHelm-specific collaboration
  rules to `AGENTS.md`.
- Accepted ADR 0026: Paraformer/Piper is the explicit local voice alpha; VAD
  remains advisory and no model, device, or cloud fallback is implicit.
- Added the reusable `deskhelm alpha` supervisor with environment-injected
  model locations, fixed single-project targeting, read-only preflight, modern
  session ownership, and bounded child cleanup.
- Added privacy-safe system guidance for microphone, no-transcript, ASR timeout,
  playback, and speech-queue failures. Voice failure cannot block Agent/text
  work or an existing Voice event observer.
- Adopted uv as the sole Python dependency manager. Added root and local-voice
  lock files, while keeping the optional model runtime separate from Bridge.
- Made the controlled ASR readiness handshake tolerate unpollable stdin with a
  bounded safe failure, and added regression coverage.
- Rebuilt the ignored local-voice uv environment and completed a
  user-confirmed internal-microphone Paraformer diagnostic without retaining
  PCM or transcript text.
- Accepted ADR 0031 and added a bounded, immutable, operator-configured project
  working-directory registry for multi-repository Agent routing.
- Added repeated `--agent-project PROJECT_ID=PATH` CLI mappings with strict
  fail-closed routing, eager directory validation, and compatibility with the
  existing `--agent-workdir` single-directory mode.
- Added registry, gateway, CLI, and socket-level tests for invalid mappings,
  capacity, two-project routing, and rejection before provider work.
- Accepted ADR 0033 and added a generic long-lived Agent runtime boundary that
  is mutually exclusive with the owned-process Agent Gateway.
- Added a bounded dependency-free Unix WebSocket Codex app-server adapter with
  initialization, thread discovery/resume, reconnect, prompt/steer/interrupt,
  terminal normalization, approvals, and limited structured input handling.
- Updated Alpha to supervise app-server, Bridge, and PTT, print the manual
  remote-TUI command, and clean up all child processes and both local sockets.
- Added optional `deskhelm alpha --tui` foreground launch with an interactive
  terminal requirement, bounded app-server readiness wait, guarded two-press
  Ctrl-C behavior, exit-status propagation, and complete stack cleanup on TUI
  exit.
- Added `tools/deskhelm-user-launcher` for direct `deskhelm alpha --tui` use
  from any project directory. It preserves the caller's working directory,
  reuses the synchronized root environment, and loads the current Alpha
  settings dynamically from the DeskHelm `project-secrets` profile without
  storing their values.
- Added `tools/run-with-pty.py` to preserve terminal input, output, resize, job
  control, and exit-status behavior behind `project-secrets`. It renders live
  TUI output directly and performs boundary-safe streaming redaction so
  no-newline cursor updates remain interactive.
- Fixed Alpha lifecycle registration to negotiate `interaction_event_v1` when
  its declared app-server capabilities include interaction and approval events;
  Bridge no longer rejects the otherwise live Alpha session connection.
- Added fixed notification and exact-phrase voice approval handling without
  exposing commands, paths, patches, reasons, questions, prompts, or secrets.

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
- Modern adapter lifecycle uses `adapter_session_v1`. Registration binds the
  complete session to one publisher owner; re-registration transfers ownership
  and restores activity without restoring focus.
- Declared state production requires negotiated `agent_event_v1`; declared
  interaction, tool, or approval production requires `interaction_event_v1`.
- Lifecycle-managed state publishing bypasses legacy agent-only session
  observation so control routing retains full identity.
- Adapter registrations are process-local; version 1 has no durable persistence
  and no control delivery over the publisher connection.
- The text Agent Gateway is opt-in, uses a fixed worker pool without an
  unbounded pending queue, and retains only a bounded set of provider session
  IDs and interaction sequences.
- `project_id` remains an identity rather than a path. Working directories come
  only from operator configuration; adapters cannot provide them. Configured
  project registries are strict and immutable until Bridge restart.
- A `dispatched` result means bounded work was accepted, not completed. Agent
  completion, cancellation, timeout, and failure use interaction terminal
  events.
- Codex prompts use stdin, JSONL records are limited to 1 MiB, stderr is not
  logged, and owned process groups are terminated on cancellation or timeout.
- The Codex provider is disabled by default and uses a read-only sandbox unless
  workspace-write is selected explicitly.
- Manual Codex integration uses the official app-server JSON-RPC surface, not
  terminal inspection or keystroke injection. It requires a TUI started with
  the Alpha's printed `codex --remote` command.
- The app-server adapter is Alpha-only while its Unix WebSocket transport is
  experimental. One Bridge may compose either this long-lived runtime or the
  existing owned-process Agent Gateway, never both.
- Voice approval recognizes only exact normalized `批准` and `拒绝`, requires
  exactly one non-expired approval for the complete target, copies its metadata
  exactly, and uses only one-shot app-server `accept` or `decline` decisions.
- A single non-secret app-server input question may consume the next PTT prompt;
  secret or multi-question input remains in the Codex TUI.
- Streams and queues require explicit bounds, ordering, cancellation,
  correlation, and slow-consumer behavior.
- Approval and rejection require precise targets and must not be blindly
  retried.
- Bridge remains dependency-minimal; voice models and GPU runtimes stay outside
  it.
- The Voice core has no Bridge imports. `VoiceBridgeIntegration` is the only
  composition layer translating transcripts, interactions, and controls.
- Voice input allows one capture/transcription flow. Speech uses a bounded
  priority queue and one playback worker; new PTT cancels current interruptible
  playback and queued playback waits until PTT returns to idle.
- Voice lifecycle events expose identifiers and fixed error codes, not audio,
  transcripts, prompts, or speech text. Raw and normalized transcripts remain
  separate in memory.
- Voice Gateway integration of VAD remains deferred. The Gateway now consumes
  frame-positioned capture chunks but aggregates them until PTT release; live
  provider-owned VAD sessions and partial ASR are not attached.
- Streaming chunks keep one immutable PCM format, contiguous absolute frame
  positions, and a 1 MiB per-chunk limit. VAD events are ordered, alternating
  speech boundaries no later than supplied audio; every active region must end
  during processing or `finish()`.
- VAD benchmark samples are limited to 100,000 chunks, 256 segments, and
  64 MiB PCM. Observations persist derived durations, counts, timing, and
  resources rather than PCM or provider exception text.
- Offline VAD replay measures compute time and frame-relative detection delay
  separately; it is not reported as live microphone end-to-end latency.
- Initial production candidates are WebRTC VAD and Silero VAD ONNX. Their
  optional runtimes remain lazy and external to Bridge; Silero shares the
  immutable ONNX Runtime session but resets state and context per stream.
- Paraformer is the initial Chinese streaming ASR candidate, not the sole
  production ASR. Its optional FunASR/PyTorch runtime and weights stay outside
  Bridge and Git; the provider uses one cache per transcription and serializes
  access to the shared model.
- Paraformer first-partial results are offline pacing estimates: required audio
  availability plus processing time for the first non-empty chunk. They are not
  live microphone capture-to-UI measurements.
- Piper is the initial notification TTS integration baseline because it was
  materially faster and smaller in the first run. Kokoro remains a comparison
  candidate; neither is the final production voice.
- TTS first-audio timing means the first complete provider chunk, not PCM-frame
  streaming. Piper and Kokoro cancel only between inference/chunk boundaries.
- Piper's GPL-3.0-or-later runtime requires packaging review before bundling or
  distribution, especially while the repository has no selected root license.
- External VAD audio is reconstructed from a versioned manifest with pinned
  HTTPS URLs, revisions, checksums, licenses, and explicit silence recipes.
  Raw/prepared audio, models, and observation files remain ignored.
- Voice benchmark v1 fixes corpus IDs and reference text, limits records to
  1 MiB, files to 64 MiB, and runs to 10,000 observations, and requires
  provider/model versions, licenses, anonymous system profile, and device
  identity.
- CER uses NFKC/case-folded text with whitespace ignored; WER is reported only
  for English-labeled utterances, while keyword accuracy preserves visibility
  into paths, symbols, versions, names, numbers, and negation.
- Numeric PipeWire object IDs are not durable. Providers must resolve configured
  defaults or stable node names and must not assume an undeclared PCM format.
- Local capture/playback audio is complete-frame raw S16LE PCM with explicit
  sample rate and channels. Capture defaults to 16 kHz mono, 30 seconds, and
  1 MiB; playback defaults to 120 seconds and 16 MiB.
- PipeWire providers use raw `pw-cat`, own a process session, suppress stderr,
  poll stop/cancel, and terminate then kill their process group within a bounded
  grace period. They remain composition-layer options and are not Bridge
  service defaults.
- The local POC follows the computer's current PipeWire default source and sink.
  Users may override either with a stable node name; a missing explicit override
  fails recoverably instead of silently falling back. Opus is reserved for a
  constrained future wireless link, with an initial research profile of 16 kHz
  mono, 20 ms frames, and 24 kbps VoIP mode.
- Local audio selection is process-local. Read-only status never opens audio;
  input/output tests require explicit commands. Diagnostic input is reduced to
  duration/peak/RMS metadata and discarded.
- The audio diagnostic CLI does not select VAD, ASR, or TTS and does not enable
  the Bridge Voice Gateway. External PTT and model composition remain separate.
- After hardware integration, the DeskHelm keyboard microphone becomes the
  default input when connected, unless the user manually chose another source.
  Disconnect fallback and user notification remain an ADR decision.
- ESP32-S3 and its wireless framing remain research directions, not frozen
  hardware or protocol decisions; implementation requires later ADRs.
- Canonical runtime identifiers are `deskhelm`, `deskhelm_bridge`, and
  `deskhelm-codex-hook`; legacy names are compatibility aliases only.
- Registration and Agent events never change focus implicitly. Only active
  sessions may be focused; disconnect, release, replacement, and expiration
  clear focus, while restore requires a new explicit focus action.
- New local clients negotiate one fixed role through
  `client_hello` / `server_hello`; only a first-frame `AgentEvent v1` receives
  legacy publisher compatibility.
- Local protocol frames are UTF-8 NDJSON limited to 1 MiB, with bounded
  per-connection queues and no automatic retry for control commands.
- The Bridge accepts at most 16 concurrent connections by default. It never
  places accepted connections into an unbounded application work queue.
- Negotiated publishers support `adapter_session_v1`, `agent_event_v1`,
  `interaction_event_v1`, or a valid combination. Negotiated subscribers select
  exactly one of `state_subscription_v1` and `interaction_subscription_v1`.
  Negotiated controllers use `control_command_v1`.
- Version 1 has no durable event history or replay. State subscribers recover
  through a fresh snapshot; interaction subscribers restart live-only delivery.
- Snapshot capture and subscriber registration are atomic. Snapshot sequence is
  zero; later sequences are monotonic within one `subscription_id`.
- Interaction subscriptions start at sequence zero and then deliver only new
  rich events. They have subscription-local wrapper sequences but no snapshot,
  retained history, replay, or state-projection side effects.
- At most half of the connection workers are subscribers by default. Queue
  overflow or a blocked write disconnects the subscriber without blocking
  publishers.
- Every control targets `agent_id + session_id + project_id`; slots never route
  controls. Voice controls also retain session ownership.
- External PTT is not a toggle. The press command ID identifies one activation,
  and release must copy that ID in addition to matching the complete session.
  Exact retries use router deduplication; clients do not automatically replay
  PTT controls after an ambiguous result.
- Model-backed local voice is an explicit experimental composition, not a
  production default. Startup validates the selected devices and required
  external files but does not load FunASR, PyTorch, Piper, or ONNX Runtime.
- The current local path uses PTT release as its capture endpoint. Optional
  advisory VAD may consume streaming chunks before final aggregation, but it
  cannot trim capture, end PTT, skip final ASR, or publish partial transcripts.
- Manifest v2 noisy/conversational replay and bounded parameter sweeps are
  comparative evidence only. They do not change the live mode or select a
  production VAD without confirmed spoken and broader language/gain evidence.
- An explicit empty provider recognition maps to `voice_no_transcript`; other
  capture, format, runtime, and model failures remain `voice_input_failed`.
- The live diagnostic substitutes a fixed public TTS response for Codex. Its
  `SPEECH_STARTED` event occurs before synthesis, so it must not be reported as
  actual first-speaker-audio latency.
- Native audio execution defaults to `pw-cat`. A composition may provide a
  validated command prefix; the verified Distrobox prefix is
  `host-spawn -no-pty pw-cat`.
- Control idempotency is scoped by `issued_by + idempotency_key`. An allowed
  retry preserves the complete command identity and content.
- Controller `client_id` is bound to `issued_by`. Exact retained retries return
  the original result without redispatch; changed content is a conflict.
- Live idempotency entries are never evicted to admit new work. The default
  bounds are 1024 idempotency entries, five-minute minimum retention, and 1024
  combined pending/decided approval records.
- Approval and rejection echo the pending request ID, summary, and expiry. The
  command expiry equals the request expiry, and automatic replay is forbidden.
  Any dispatch attempt consumes the request because downstream failure may be
  ambiguous.

## Important Files

- `AGENTS.md`: constitution and repository rules.
- `README.md`: project overview and current quickstart.
- `docs/decisions/0033-bidirectional-codex-app-server-adapter.md`: supported
  manual-TUI attachment model, privacy boundaries, approval safety, and limits.
- `bridge/deskhelm_bridge/agent_runtime.py`: generic long-lived Agent runtime
  composition contract.
- `adapters/codex/deskhelm_codex_adapter/app_server.py`: bounded Codex JSON-RPC
  lifecycle, same-thread controls, approvals, and input-request adapter.
- `adapters/codex/deskhelm_codex_adapter/app_server_transport.py`: bounded
  dependency-free Unix WebSocket transport.
- `tests/test_codex_app_server_adapter.py` and
  `tests/test_codex_app_server_transport.py`: runtime and transport regression
  coverage, including the root full-suite import path.
- `bridge/deskhelm_bridge/project_registry.py`: bounded validated mapping from
  opaque project identity to operator-approved Agent working directories.
- `tests/test_project_registry.py`: registry validation, capacity, and strict
  resolution coverage.
- `docs/decisions/0031-operator-configured-project-working-directories.md`:
  multi-project path ownership, bounds, strictness, and compatibility decision.
- `docs/decisions/0032-pinned-noisy-conversation-vad-evidence.md`: annotated
  conversation/noise evidence, bounded sweeps, live timing, and advisory-only
  VAD decision.
- `docs/research/2026-08-26-vad-noisy-conversation-expansion.md`: pinned source
  provenance, measured profile results, live negative control, and limitations.
- `bridge/deskhelm_bridge/alpha.py`: reusable local Alpha preflight, session
  registration, Bridge/PTT process supervision, and cleanup.
- `tools/deskhelm-user-launcher`: user-level source-checkout entry point with
  dynamic encrypted Alpha configuration.
- `tools/run-with-pty.py`: standard-library PTY relay for the redacted managed
  TUI launch.
- `tests/test_run_with_pty.py`: PTY stream and exit-status regression coverage.
- `bridge/deskhelm_bridge/client.py`: negotiated adapter-session publisher used
  to hold the Alpha target active.
- `tests/test_local_alpha.py`: artifact injection, fixed composition, preflight,
  and cleanup coverage.
- `docs/decisions/0026-local-voice-alpha-profile.md`: selected providers and
  reusable single-project startup behavior.
- `docs/software/no-hardware-roadmap.md`: active milestone plan.
- `docs/architecture/multimodal-agent-console.md`: component architecture.
- `docs/research/2026-08-14-local-voice-stack.md`: ASR and TTS research.
- `docs/research/2026-08-17-agent-io-design-lessons.md`: external design review.
- `docs/decisions/0001-phase-0-python-unix-socket.md`: Phase 0 transport.
- `docs/decisions/0002-separate-bridge-state-and-session-projection.md`:
  Bridge state and session boundary.
- `docs/decisions/0003-adopt-deskhelm-name.md`: naming and compatibility plan.
- `docs/decisions/0004-session-lifecycle-and-focus.md`: session lifecycle and
  safe focus semantics.
- `docs/decisions/0005-single-socket-negotiated-local-protocol.md`: negotiated
  local transport, roles, bounds, compatibility, and reconnect behavior.
- `docs/decisions/0006-targeted-expiring-idempotent-controls.md`: targeting,
  expiry, idempotency, retry, and approval safety decisions.
- `docs/decisions/0007-adapter-session-capabilities-and-ownership.md`: adapter
  identity, capabilities, lifecycle, ownership, and restore semantics.
- `docs/decisions/0008-bounded-text-agent-gateway.md`: provider boundary,
  capacity, process safety, privacy, cancellation, and terminal semantics.
- `docs/decisions/0009-isolated-bounded-voice-gateway.md`: Voice isolation,
  provider contracts, bounds, targeting, privacy, and cancellation.
- `docs/decisions/0010-versioned-voice-benchmark-contract.md`: corpus stability,
  observation format, scoring, bounds, privacy, resources, and licensing.
- `docs/decisions/0011-bounded-pipewire-pcm-providers.md`: local PCM format,
  PipeWire targets, provider bounds, process ownership, and privacy contract.
- `docs/decisions/0012-streaming-pcm-vad-benchmark-boundary.md`: streaming PCM
  chunks, VAD sessions/events, benchmark metrics, bounds, and privacy contract.
- `docs/decisions/0013-select-webrtc-and-silero-vad-baselines.md`: initial VAD
  candidates, dependency isolation, FSDD provenance, and selection limits.
- `docs/decisions/0014-use-paraformer-as-initial-streaming-asr-baseline.md`:
  pinned model/runtime identity, streaming configuration, bounds, and limits.
- `docs/decisions/0020-evaluate-sensevoice-as-second-asr-baseline.md`: selected
  comparison, immutable artifact identity, final-only semantics, and license
  risk.
- `docs/decisions/0021-bounded-voice-recovery-and-device-rebind.md`: retry,
  cancellation, process recovery, and strict device rebinding semantics.
- `docs/decisions/0022-bounded-multi-phrase-asr-diagnostic.md`: bounded batch
  capture, per-phrase confirmation, and privacy-safe comparison output.
- `docs/decisions/0023-one-by-one-asr-readiness-handshake.md`: bounded per-phrase
  stdin readiness and explicit capture-start semantics for chat-driven runs.
- `docs/decisions/0024-bounded-asr-timeout.md`: bounded ASR deadline, fixed
  timeout outcome, explicit retry, and cooperative cancellation boundary.
- `docs/decisions/0025-isolated-whisper-cpp-shim-provider.md`: pinned external
  C ABI, explicit provider opt-in, bounds, and native dependency boundary.
- `docs/decisions/0015-use-piper-as-initial-notification-tts-baseline.md`:
  initial TTS baseline, streaming semantics, licensing, and selection limits.
- `docs/decisions/0016-explicit-local-audio-selection-and-diagnostics.md`:
  provider/device selection, privacy-safe diagnostics, and deferred composition.
- `docs/decisions/0017-correlate-external-ptt-press-and-release.md`:
  external PTT targeting, activation correlation, retry, and failure semantics.
- `docs/decisions/0018-opt-in-provisional-local-voice-composition.md`:
  explicit composition, preflight, lazy loading, provider status, and VAD
  deferral.
- `docs/research/2026-08-18-pipewire-preflight.md`: verified local PipeWire
  capabilities and provider-design implications.
- `docs/research/2026-08-18-esp32-s3-audio-transport.md`: official ESP32-S3 and
  Opus evidence, wireless control split, parameters, risks, and local USB path.
- `docs/research/2026-08-18-vad-candidates-and-first-benchmark.md`: verified
  candidate facts, first-run configuration, aggregate results, and gaps.
- `docs/research/2026-08-18-paraformer-first-benchmark.md`: pinned identity,
  external-set provenance, measurements, per-sample results, and next evidence.
- `docs/research/2026-08-18-piper-kokoro-first-benchmark.md`: pinned TTS
  identities, performance, interruption, proxy intelligibility, and gaps.
- `docs/research/2026-08-19-local-audio-diagnostics.md`: current PipeWire
  inventory, live default/manual tests, signal metadata, and startup gap.
- `docs/research/2026-08-19-local-voice-runtime-and-live-path.md`: combined
  runtime identity, PipeWire host boundary, live timings, and limitations.
- `docs/research/2026-08-19-controlled-live-asr-diagnostic.md`: controlled
  public-phrase contract, first real signal/ASR metrics, interpretation, and
  remaining evidence.
- `docs/research/2026-08-19-sensevoice-second-asr-baseline.md`: candidate
  comparison, pinned artifacts, public/live results, and licensing boundary.
- `docs/research/2026-08-19-provider-recovery-and-device-change.md`: recovery
  matrix, fake-boundary evidence, and outstanding live hot-plug measurements.
- `docs/research/2026-08-19-multi-phrase-asr-diagnostic.md`: phrase set,
  privacy bounds, live provider comparison, and handshake follow-up.
- `docs/research/2026-08-20-whisper-cpp-fallback.md`: official whisper.cpp
  facts, shim boundary, and first external base/small benchmark results.
- `protocol/adapter-session-v1.md`: lifecycle frames, acknowledgements,
  declared capabilities, and event ownership validation.
- `protocol/interaction-event-v1.md`: rich session event contract.
- `protocol/control-command-v1.md`: targeted control command contract.
- `protocol/control-result-v1.md`: correlated control outcomes and fixed result
  codes.
- `protocol/state-subscription-v1.md`: state snapshot, live update, sequencing,
  resource bounds, and reconnect contract.
- `protocol/interaction-subscription-v1.md`: live-only rich updates, bounds,
  sequencing, and gap behavior.
- `protocol/local-transport-v1.md`: implemented framing, handshake, publisher,
  compatibility, limits, and error contract.
- `bridge/deskhelm_bridge/interaction.py`: `InteractionEvent v1` model and
  validation.
- `bridge/deskhelm_bridge/control.py`: `ControlCommand v1` payload models,
  validation, serialization, and expiry checks.
- `bridge/deskhelm_bridge/control_result.py`: correlated result model and fixed
  status codes.
- `bridge/deskhelm_bridge/control_router.py`: live target checks, approval
  tracking, bounded idempotency, focus, and handler dispatch.
- `bridge/deskhelm_bridge/adapter.py`: adapter lifecycle and acknowledgement
  protocol models.
- `bridge/deskhelm_bridge/adapter_registry.py`: connection-owned registration,
  lifecycle, and event validation.
- `bridge/deskhelm_bridge/agent_gateway.py`: generic bounded prompt/interrupt
  scheduling, provider-session resume, sequence ownership, and normalization.
- `bridge/deskhelm_bridge/fake_agent_provider.py`: deterministic provider used
  by gateway tests without external services.
- `voice/deskhelm_voice/gateway.py`: PTT lifecycle, bounded streaming capture
  aggregation, speech queue, playback ownership, interruption, and Voice events.
- `voice/deskhelm_voice/providers.py`: provider-neutral capture, ASR, TTS, and
  playback contracts.
- `voice/deskhelm_voice/fake_providers.py`: deterministic batch/streaming
  capture, ASR, TTS, playback, and VAD providers.
- `voice/deskhelm_voice/pipewire.py`: owned frame-positioned raw-PCM `pw-cat`
  capture stream, batch compatibility wrapper, and bounded playback provider.
- `voice/deskhelm_voice/audio_config.py`: bounded discovery, provider/device
  selection, signal-only input reports, and generated output tones.
- `voice/deskhelm_voice/local_gateway.py`: provisional provider selection,
  artifact preflight, bounds, and lazy Voice Gateway construction.
- `voice/runtime/pyproject.toml` and `voice/runtime/uv.lock`: uv-pinned
  optional combined Paraformer/SenseVoice/Piper CPU runtime.
- `voice/deskhelm_voice/streaming.py`: frame-positioned PCM chunks, speech
  boundaries, and segment models.
- `voice/deskhelm_voice/benchmark.py`: bounded runners, observation models,
  NDJSON CLI, accuracy metrics, and summaries.
- `voice/benchmarks/utterances-v1.json`: stable synthetic benchmark corpus.
- `voice/benchmarks/vad-external-v1.json`: pinned public VAD audio provenance,
  checksums, format, speakers, and deterministic scenario recipes.
- `voice/benchmarks/vad-conversation-noise-v2.json`: pinned AMI conversation,
  annotation intervals, ESC-10 noise, and deterministic SNR scenarios.
- `voice/benchmarks/asr-external-v1.json`: pinned public ASR audio references,
  checksums, licenses, speakers, and expected transcripts.
- `voice/benchmarks/tts-candidates-v1.json`: pinned TTS runtime/model identity,
  artifacts, sizes, checksums, and licenses.
- `voice/benchmarks/README.md`: measurement and artifact-handling contract.
- `voice/deskhelm_voice/vad_manifest.py`: bounded external-audio manifest model.
- `voice/deskhelm_voice/vad_samples.py`: checksum-validating prepared WAV loader
  and deterministic PCM chunk construction.
- `voice/deskhelm_voice/webrtc_vad.py`: lazy WebRTC VAD streaming adapter.
- `voice/deskhelm_voice/silero_onnx_vad.py`: lazy stateful Silero ONNX adapter.
- `voice/deskhelm_voice/paraformer.py`: lazy bounded streaming Paraformer ASR
  adapter and offline first-partial measurement.
- `voice/deskhelm_voice/sensevoice.py`: lazy bounded final-only SenseVoice ASR
  adapter with serialized inference and explicit cancellation boundaries.
- `voice/deskhelm_voice/whisper_cpp.py`: lazy explicit ctypes provider for the
  external DeskHelm whisper.cpp shim.
- `voice/native/whisper-cpp-shim/`: DeskHelm C ABI source and external build
  notes for pinned whisper.cpp `v1.9.2`.
- `voice/deskhelm_voice/asr_manifest.py`: bounded external ASR manifest and
  prepared-set checksum/duration validation.
- `voice/deskhelm_voice/piper_tts.py`: lazy bounded Piper provider with pinned
  local Chinese G2P resources.
- `voice/deskhelm_voice/kokoro_tts.py`: lazy bounded Kokoro Chinese/English
  provider.
- `voice/deskhelm_voice/tts_manifest.py`: bounded TTS candidate and artifact
  manifest validation.
- `tools/prepare-vad-benchmark.py`: bounded download, clipping, verification,
  deterministic SNR mixing, conversion, and local prepared-index generation.
- `tools/run-vad-benchmark.py`: isolated candidate/sweep runner, NDJSON writer,
  and per-profile summary output.
- `tools/run-live-vad-diagnostic.py`: explicit bounded PipeWire/VAD timing
  diagnostic with incremental signal metrics, a bounded public-phrase readiness
  handshake, and privacy-safe derived output.
- `tools/prepare-asr-benchmark.py`: bounded ASR download, checksum verification,
  conversion, and prepared-index generation.
- `tools/run-asr-benchmark.py`: explicit Paraformer/SenseVoice verification and
  isolated runs, observations, resource metadata, and summary output.
- `tools/run-whisper-cpp-benchmark.py`: pinned-model verification and explicit
  whisper.cpp shim benchmark runner.
- `tools/prepare-tts-benchmark.py`: bounded downloads, verification, and safe
  G2PW extraction.
- `tools/run-tts-benchmark.py`: isolated candidate runner, WAV export,
  interruption probe, observations, and summary output.
- `tools/run-local-voice-live.py`: explicit privacy-safe live capture, ASR,
  fixed-response TTS, playback, and timing diagnostic.
- `tools/run-local-asr-diagnostic.py`: controlled public-phrase microphone and
  selectable ASR diagnostic with privacy-safe signal, accuracy, latency, and
  bounded unpollable-stdin readiness handling.
- `bridge/deskhelm_bridge/voice_integration.py`: transcript, interaction, and
  control composition between Bridge and Voice.
- `adapters/codex/deskhelm_codex_adapter/provider.py`: Codex command, stdin,
  JSONL parsing, timeout, cancellation, and process-exit handling.
- `bridge/deskhelm_bridge/subscription.py`: subscription wire models and bounded
  per-subscriber update queue.
- `bridge/deskhelm_bridge/interaction_subscription.py`: rich subscription wire
  models, bounded queue, and in-process fan-out hub.
- `tests/test_pipewire_providers.py`: fake-subprocess PCM, targeting, bounds,
  failure, cancellation, process cleanup, and disconnect/retry coverage.
- `tests/test_vad_benchmark.py`: streaming chunk/session validation, VAD metrics,
  failure records, NDJSON, and CLI summary coverage.
- `tests/test_vad_providers.py`: manifest, prepared checksum, WebRTC buffering,
  format, and hysteresis coverage.
- `tests/test_vad_tools.py`: manifest compatibility, annotated preparation,
  SNR mixing, sweep bounds, live signal aggregation/privacy, and readiness
  coverage.
- `tests/test_asr_providers.py`: ASR manifest/prepared-set, measured streaming
  result, lazy loading, input bounds, cancellation, and failure-reuse coverage.
- `tests/test_tts_providers.py`: TTS manifest, benchmark metrics, provider
  routing, lazy loading, bounds, and cancellation coverage.
- `tests/test_audio_config.py`: synthetic discovery, default/manual selection,
  device rebinding, provider composition, diagnostics, and CLI argument coverage.
- `tests/test_voice_integration.py`: targeted PTT/speech control composition,
  transcript-to-prompt routing, and speech failure isolation.
- `tests/test_local_voice_config.py`: disabled-by-default CLI behavior, exact
  preflight failures, lazy provider composition, and gateway ownership cleanup.
- `tests/test_local_voice_live_tool.py`: explicit live-audio bounds and
  transcript/audio-free summary coverage.
- `tests/test_local_asr_diagnostic_tool.py`: controlled phrase selection,
  signal hints, privacy suppression, fixed failures, metric coverage, and
  unpollable-stdin readiness coverage.
- `voice/runtime/pyproject.toml` and `voice/runtime/uv.lock`: isolated pinned
  Python 3.12 local-voice uv environment; the old requirements file is removed.
- `tests/test_whisper_cpp_benchmark_tool.py`: pinned artifact metadata and
  benchmark runner bounds.
- `bridge/deskhelm_bridge/transport.py`: hello and protocol-error wire models.
- `bridge/deskhelm_bridge/server.py`: bounded concurrent socket handling and
  connection role dispatch.
- `bridge/deskhelm_bridge/state_store.py`: state snapshots and subscriptions.
- `bridge/deskhelm_bridge/session_registry.py`: session-to-slot projection.

## Validation

Last verified on 2026-08-20:

```bash
PYTHONPATH=bridge python3 -m unittest discover -s tests -v
```

Result: 231 tests passed under the workstation resource limiter. This includes
the existing Bridge, protocol,
adapter, voice, benchmark, and fake-subprocess PipeWire coverage plus streaming
capture continuity, bounds, premature-end, cleanup, compatibility, empty-ASR
classification, advisory VAD ordering/fallback/cancellation, explicit local VAD
composition, privacy-safe live-summary tests, and the bounded multi-phrase
diagnostic path. The full unit suite opened no live audio device and did not
import optional model runtimes. The focused local ASR diagnostic module has 11
tests covering corpus selection, signal/VAD metrics, provider-output
suppression, fixed failures, lazy final-only behavior, recovery boundaries,
batch phrase selection/privacy output, and the bounded exact-`ready` handshake.

On 2026-08-21, the same limited full suite again passed 231/231 tests in 6.980
seconds. `git diff --check` passed. Read-only `deskhelm audio status --list`
resolved the current default capture and playback devices as
`alsa_input.pci-0000_00_1f.3.analog-stereo` and
`alsa_output.pci-0000_00_1f.3.analog-stereo`; no live audio device was opened.

The user then authorized uv as the sole Python dependency manager. uv 0.12.5
created a root lock and an isolated `voice/runtime` Python 3.12.14 environment
from its 93-package committed lock. The full no-hardware suite passed 232/232
in 6.657 seconds with `PYTHONPATH=bridge uv run --project voice/runtime
--locked python -m unittest discover -s tests -v`. The root project's minimal
uv environment also synchronized successfully. Running the full suite from that
minimal environment is not canonical because provider tests require NumPy and
the editable distribution introduces a second Voice module namespace.

Final alpha-runtime validation refreshed the local-voice lock explicitly for
Python 3.12, synchronized its ignored environment, and passed 233/233
no-hardware tests in 9.952 seconds. The strict `uv sync --locked` completed
before the suite; `git diff --check` passed.

The uv voice runtime imported FunASR successfully. sherpa-onnx could not load
because its wheel requires external `libonnxruntime.so` ABI `VERS_1.27.1`, while
the pinned PyPI ONNX Runtime wheel exposes a different ABI; no external native
library was downloaded or retained. A real user-confirmed Paraformer diagnostic
therefore used the internal analog-stereo microphone. It captured 382,238 bytes
over 11,944.938 ms, completed with keyword accuracy `1.0`, CER `0.181818`,
estimated first partial `4,893.686 ms`, and final ASR `10,171.631 ms`. WebRTC
VAD reported two advisory segments totaling 7,420 ms. The output retained no
PCM or recognized text. Peak `1.0` and clipped-sample fraction `0.00363125`
indicate possible input clipping.

After the reported default-input switch, `deskhelm audio status --list` still
resolved only `alsa_input.pci-0000_00_1f.3.analog-stereo`. An explicit
two-second input diagnostic captured 1,939.6 ms with peak `1.0000` and RMS
`0.1707`, then discarded the PCM. It proves the observed default remains
usable, but not a device-change/rebind path because its stable name did not
change. The full-scale peak suggests the input gain may clip and should be
rechecked separately from recovery behavior.

The user then selected the internal microphone explicitly. The same bounded
manual-source diagnostic captured 1,939.6 ms with peak `1.0000` and RMS
`0.1706`, then discarded the PCM. It validates the stable manual target without
any fallback; it does not constitute live missing-device recovery evidence.

A subsequent live ASR diagnostic preflight did not start capture. The current
host, `ubuntu24-r23` system Python, and retained benchmark virtual environments
lack `funasr` and `sherpa_onnx`; the retained host environments report Python
3.14.6 rather than the recorded Python 3.12 runtime. Container resource limits
remain compliant (CPUs 4-23, 18 CPUs, 18 GiB memory, 20 GiB memory plus swap,
and 2,048 PIDs). No package installation, model load, audio capture, PCM
retention, or transcript processing occurred.

The first live SenseVoice multi-phrase batch completed four fresh captures with
`ok` status and the user confirmed all four phrases were spoken. The user also
reported that the speaking pace did not fully keep up with phrase transitions;
the measurements are recorded as qualified diagnostic evidence, not a tightly
synchronized provider comparison.

The repeated live Paraformer batch also completed four fresh captures with `ok`
status. The user reported that the prompt cadence was faster than comfortable
natural speech and asked to begin analysis; all four are treated as spoken with
that caveat. Across the two qualified batches, Paraformer mean CER was `0.726123`
versus SenseVoice `0.809943`, while mean final latency was `2,158.430 ms` versus
`442.620 ms`. Both providers had `0.166667` mean keyword accuracy and zero
keyword accuracy on the three mixed coding phrases.

The new handshake mode now has live coverage for both providers. SenseVoice
mean CER was `0.177557` with `430.398 ms` mean final latency; Paraformer mean
CER was `0.431913` with `2,267.219 ms` mean final latency. These are separate
human recordings, not paired audio. The reported keyword scores were produced
before the whitespace-normalization fix and require a future rerun if they
affect a provider gate.

An additional startup smoke test used the current PipeWire graph and ignored
prepared Paraformer/Piper artifact paths. The opt-in Bridge composed the local
gateway, accepted one event, exited cleanly, and removed its socket without
opening audio or loading model runtimes.

The current Voice Gateway focused suite passed 18/18 tests covering streaming
and legacy capture, advisory activity ordering, output bounds, fallback,
cancellation, and event metadata validation.

The isolated real-candidate run also passed with 35/35 successful observations
for both WebRTC and Silero. Downloaded FSDD audio, prepared WAV files, the ONNX
model, the virtual environment, and raw NDJSON results remain ignored.

The isolated Paraformer run passed with 24/24 successful observations across
eight samples and three repetitions. It verified the pinned model checksum,
recorded 4.97-second cold load, 3.09 GiB process peak RSS, 0.121 mean RTF, and
the accuracy/latency results in the dated research report. The Python 3.12
environment, weights, downloaded/prepared audio, raw NDJSON, and local summary
remain ignored.

The isolated SenseVoice run passed with 24/24 successful observations across
the same eight samples and three repetitions. It verified the GitHub archive
digest plus extracted model/token checksums, recorded 681.488 ms cold load,
412.512 MiB peak RSS, 0.055813 mean RTF, and the accuracy/latency results in the
dated report. The wheel, model, raw observations, and local summary remain
ignored.

After the Paraformer cancellation-boundary change, a one-repetition public
smoke run passed 8/8. CER and keyword accuracy remained 0.4375/0.4375, final
latency p50 was 44.473 ms, estimated first-partial p50 was 373.653 ms, and peak
RSS was 3,088.203 MiB. The run used the constrained `ubuntu24-r23` container;
its raw observations and summary remain ignored.

The isolated TTS run passed with 36/36 successful observations per candidate,
and a post-change offline smoke run passed with 12/12 per candidate. The dated
research report records exact performance, memory, interruption, licensing,
signal, and ASR-proxy results. Environments, weights, generated WAV files, raw
NDJSON, proxy output, and local summaries remain ignored.

The combined Python 3.12 runtime passed 8/8 Paraformer samples and 12/12 Piper
utterances. The live four-second run resolved the default USB mono microphone
and internal stereo sink, then emitted `ptt_started`, `transcribing`,
`transcript_ready`, `speech_started`, and `speech_completed`. Release-to-final
was 7,186.167 ms and total time was 15,294.695 ms. Only the transcript character
count was reported; PCM and transcript text were neither saved nor printed.
The earlier signal diagnostic peak of 1.0 and RMS near 0.394 indicate probable
input clipping/high gain.

After the streaming migration, a four-second signal-only run captured 128,286
bytes over 4,008.938 ms with peak 0.399292 and RMS 0.061689. Two full-chain
attempts reached `transcribing` but ended without a final transcript or
playback; the user confirmed speaking during the second. PCM and transcript
text were not saved. The healthy signal makes capture failure less likely, but
the exact Paraformer failure still requires a live rerun after the new
`voice_no_transcript` classification.

The subsequent advisory WebRTC VAD run used the pinned
`webrtcvad-wheels==2.0.14` runtime. It emitted `input_speech_started` at frame 0
and `input_speech_ended` at frame 1,280 before `transcribing`, with no VAD
failure. PTT release still ended capture and final ASR returned the now-confirmed
`voice_no_transcript` classification. PCM and transcript text were neither
saved nor printed. The 80 ms activity region validates live event plumbing and
fallback only; it does not prove detection of the intended utterance or
acceptable VAD/ASR quality.

The unspoken `zh-repeat-01` microphone-only run captured 223,178 bytes over
6,974.312 ms at 16 kHz mono S16LE. Peak was 0.506927, RMS was 0.035902, clipped
sample fraction was zero, and the provisional level hint was
`within_diagnostic_range`. Paraformer returned `voice_no_transcript` after
11,329.329 ms. PCM and recognized text were neither saved nor printed. The user
later confirmed they did not speak, so these values describe a background
negative control rather than voice input or recognition quality.

The third controlled attempt was user-confirmed as spoken. It captured
256,628 bytes over 8,019.625 ms with peak 0.914215, RMS 0.120620, and no clipped
samples. Paraformer returned 27 characters, matched both expected keywords,
reported 0.545455 CER, first partial at 1,850.498 ms, and final output at
5,785.955 ms. PCM and recognized text were neither saved nor printed. The user
did not hear the attempted speaker cue, so future interpretation requires
explicit post-run speech confirmation rather than relying on an audible cue.

The advisory-activity phase found that an unspoken eight-second negative control
still produced five VAD segments and 739.625 ms of false activity. A separate
user-confirmed attempt with a ten-second unseen lead-in captured only 180 ms of
activity and no transcript, indicating a likely synchronization miss rather
than model failure. The workflow now defaults to immediate capture after an
explicit readiness handshake.

The final confirmed immediate-capture run recorded 384,970 bytes over
12,030.312 ms. WebRTC reported seven segments totaling 6,970.312 ms
(`0.579396` active fraction). Paraformer returned 18 characters with both
keywords, 0.181818 CER, first partial at 4,850.187 ms, and final output at
8,008.233 ms. Peak was 0.779144, RMS was 0.091119, and no samples clipped.

The synchronized SenseVoice comparison was also user-confirmed as spoken. It
captured 384,970 bytes over 12,030.312 ms, returned 19 characters, matched both
keywords, and recorded 0.136364 CER with 1,328.308 ms cold-process final ASR.
WebRTC reported two advisory segments totaling 4,700 ms. Peak was 0.696960, RMS
was 0.086324, and no samples clipped. PCM and recognized text were not saved or
printed.

Live local audio diagnostics resolved three sources and three sinks. Two-second
default and manual USB-source tests each captured about 1.98 seconds of 16 kHz
mono S16LE PCM and discarded it after signal measurement. The default internal
sink played a 250 ms, 660 Hz, 5% level tone. A one-second input test returned no
audio and failed explicitly; hot unplug and default changes remain untested.

The isolated whisper.cpp provider suite passed 6/6 fake-native tests covering
lazy loading, PCM conversion, fixed status mapping, serialization recovery,
cancellation, transcript bounds, and explicit close/reload lifecycle recovery.
The C shim passed strict syntax checks and linked against an external CPU-only
whisper.cpp `v1.9.2` build. A missing-model smoke load returned the fixed safe
provider error; no model weights, microphone input, PCM persistence, or
transcript logging were used.

The benchmark tool suite passed 3/3 tests covering parser defaults, pinned model
identity, and rejection of an unpinned artifact. The runner now follows the
repository's standard parser/validation split, so direct parser tests do not
attempt to parse process arguments during parser construction.

The external ASR benchmark runner passed 24/24 observations for multilingual
`base` and 21/24 for `small` across three repetitions. `base` measured 1.2662
mean CER, 1,519.6 ms p50 final latency, 3.5158 mean RTF, and 391.9 MiB peak
RSS. `small` measured 0.5218 CER on successful observations, 5,887.9 ms p50,
12.7997 mean RTF, and 902.4 MiB peak RSS; all three `zero` repetitions returned
the fixed empty-result boundary. A cooperative cancellation probe returned
after 1.326 s for `base` and 5.876 s for `small` from the cancel flag.

Repository checks also passed:

```bash
git diff --check
python3 -m compileall -q bridge adapters/codex voice tests tools
! rg -n '[[:blank:]]+$' . --glob '!.git/**'
! rg -n 'deskhelm_bridge|bridge\.' voice/deskhelm_voice
git check-ignore -v references/vendor/paraformer-bench/py312/bin/python \
  references/vendor/tts-bench/py312/bin/python \
  voice/benchmarks/results/paraformer-v1.ndjson \
  voice/benchmarks/results/piper-chaowen-v1.ndjson \
  voice/benchmarks/results/kokoro-v1.ndjson
```

On 2026-08-26, the resource-limited isolated Python 3.12 suite passed 257/257
tests in 5.786 seconds with `--locked --no-sync`; `git diff --check` passed.
The actual Alpha `--check` passed against the existing ignored Paraformer and
Piper artifacts, current Codex installation, PipeWire defaults, and stable PTT
device without opening audio. A real no-input startup smoke registered the
modern session and armed right-Alt PTT. Direct supervisor termination removed
the exact temporary socket and left no Bridge, PTT, `uv`, Python, or `pw-cat`
process from the smoke run.

After the multi-project registry phase, the resource-limited complete
no-hardware suite passed 264/264 tests in 5.727 seconds. Focused registry,
gateway, CLI, and Bridge socket tests covered eager validation, strict unmapped
rejection, two-project routing, and preservation of legacy single-directory
composition. `git diff --check` passed.

After the noisy/conversational VAD phase, focused VAD tests passed 16/16. The
resource-limited isolated Python 3.12 suite then passed 269/269 tests in 5.971
seconds with `--locked --no-sync`. This includes manifest v1/v2 validation,
annotated clip preparation, deterministic SNR mixing, profile isolation and
capacity, live diagnostic privacy/bounds, and all prior Bridge/Voice coverage.
The first full run exposed a test-only duplicate Voice package identity; the
new tool test now aliases the existing package instead of loading a second
copy. The rerun passed, and `git diff --check` passed.

After adding the confirmed-live readiness handshake, focused VAD tool tests
passed 7/7 and the resource-limited complete suite passed 272/272 in 6.136
seconds. Coverage includes exact readiness input, bounded fallback for
unpollable stdin, versioned public phrase selection, and preservation of the
existing privacy and capture bounds. Command help and `git diff --check` also
passed.

Two readiness-gated live WebRTC captures then completed successfully on the
same default built-in source. The user separately confirmed fully speaking the
public phrase in both. Mode 0 detected one 140 ms segment; mode 2 detected none.
Both retained approximately six seconds of PCM only in memory and discarded it.
This is valid negative live-quality evidence, not a provider/runtime failure.

The signal-metadata follow-up passed 8/8 focused tests. After the unrelated
concurrent C++ build finished, the resource-limited complete no-hardware suite
passed 273/273 in 6.206 seconds, and `git diff --check` passed. Live evidence
then classified the built-in source as `too_quiet` at 7% volume and confirmed
the explicit USB source as `within_diagnostic_range`; USB WebRTC mode 2 detected
three speech segments totaling 2,800 ms. All PCM was discarded.

A final readiness-gated built-in/mode 2 retry was performed after the user moved
closer to the microphone and separately confirmed reading the complete phrase.
It remained `too_quiet`: peak `0.001587`, RMS `0.000156`, `0.992523`
near-silence fraction, no clipping, and zero detected speech segments. This
confirms that the earlier built-in failure was not resolved by speaking closer.
Alpha now accepts `DESKHELM_ALPHA_VOICE_SOURCE` as a persistent local stable-name
selection, with explicit `--voice-source` taking precedence. Either path keeps
the existing fail-closed behavior when the selected device is unavailable. The
focused Alpha/audio composition suite passes 19/19, the resource-limited full
no-hardware suite passes 274/274 in 6.170 seconds, the current USB stable name
passes a read-only manual PipeWire selection check, and `git diff --check`
passes.

The operator then stored `DESKHELM_ALPHA_VOICE_SOURCE` in the encrypted local
project settings. A fresh dynamically injected read-only resolution selected
`PCM2902 Audio Codec` with `source_uses_default=false`, confirming that Alpha's
stored selection targets the USB microphone rather than the PipeWire default.
The operator also stored the existing external ASR/TTS model directories. A
fresh three-name status check returned ready, and the dynamically injected
Alpha `--check` passed without opening audio or loading model weights. No model
path was written to Git.

A resource-limited live Alpha session then started with the encrypted settings,
registered the `next_keyboard` session, and armed right-Alt PTT. The user
performed one PTT test and explicitly confirmed hearing the fixed completion
notification with no microphone-input failure guidance. This verifies the
configured USB path through live capture, final ASR, Agent dispatch, completion,
TTS, and playback. Voice failure guidance remains terminal-only rather than
spoken under the pure-notification policy.
Ctrl-C ended the foreground resource wrapper without removing its child scope,
so the exact transient service was stopped explicitly. The Bridge socket and
all scoped Alpha, PTT, and `pw-cat` processes were then confirmed absent.

After the Codex app-server adapter phase, the focused adapter and transport
tests passed 8/8 using the documented full-suite `PYTHONPATH=bridge` import
environment. The resource-limited isolated Python 3.12 suite then passed
287/287 tests in 6.996 seconds with `--locked --no-sync`; it opened no live
audio and loaded no model weights. A fresh dynamically injected Alpha
`--check` also passed against the current encrypted device and model settings
without opening audio or loading model weights. `git diff --check` passed. A
real local `codex-cli 0.149.0` Unix WebSocket check completed `initialize`,
`thread/loaded/list`, and no-model `thread/resume`; the temporary synthetic
thread and app-server process were removed afterward. An actual model turn in
the manually started remote TUI remains intentionally unverified.

After adding Alpha `--tui`, the focused Alpha and app-server suite passed 21/21.
The resource-limited complete suite passed 293/293 tests in 7.247 seconds. A
fresh dynamically injected `deskhelm alpha --tui --check` passed with the
current encrypted device and model settings without opening audio, loading
models, or starting a TUI. A real local PTY smoke transferred foreground
ownership to a separate child process group and restored the terminal with exit
status zero. CLI help exposes the new flag, and both the default printed-command
path and foreground-TUI cleanup path remain covered. A live interactive Codex
turn is still required for terminal and voice confirmation.

After adding the user launcher, its Bash syntax, executable bit, and installed
symlink target were verified. From `/home/tope/project/device-api`, both
`deskhelm --help` and `deskhelm alpha --help` succeeded without requiring Alpha
configuration. A fresh three-name encrypted-config check was ready, and the
direct cross-project command `deskhelm alpha --tui --check` passed without
opening audio, loading models, or starting a TUI. The resource-limited focused
Alpha suite passed 12/12, and `git diff --check` passed.

The first direct interactive test from `/home/tope/project/wechat_miniprogram`
exposed two launcher-path defects. `project-secrets` output redaction removed
the child's stdout TTY, and Alpha's lifecycle publisher omitted the interaction
transport capability required by its own registration. The PTY relay and
publisher negotiation fixes are now live-verified in that exact directory:
`deskhelm alpha --tui` reached `local alpha active`, armed right-Alt PTT, opened
Codex 0.149.0 with the displayed directory
`~/project/wechat_miniprogram`, and exited through Codex shutdown after Ctrl-C.
Both DeskHelm sockets, the Alpha process, its `pw-cat` children, and the
transient Codex app-server were absent afterward. Focused PTY, Alpha, and Bridge
publisher tests passed 16/16. The complete resource-limited no-hardware suite
then passed 296/296 in 7.284 seconds, and `git diff --check` passed.

The user's first follow-up TUI reached Codex but appeared unable to accept
input. Process inspection showed correct PTY foreground ownership and no evdev
keyboard grab. The actual failure was visual: `project-secrets` forwarded child
output with `readline()`, so Codex's no-newline keystroke redraws remained
buffered even though input reached the TUI. The relay now writes to the
controlling terminal itself and redacts the three injected Alpha values across
arbitrary output chunk boundaries. In a fresh real run from
`/home/tope/project/wechat_miniprogram`, `/status` appeared immediately in the
input area and executed successfully, proving both typing and Enter handling.
Ctrl-C then shut down the complete stack, and both sockets plus DeskHelm and
`pw-cat` processes were absent afterward. Focused PTY and Alpha tests passed
16/16. The complete resource-limited no-hardware suite passed 298/298 in 7.264
seconds, and `git diff --check` passed.

## Remaining Work

1. Ensure exactly one voice-enabled Alpha owns the shared PTT input, then live-
   verify that only the focused session receives PTT, followed by first and
   second voice approvals, a second voice-started turn, the selected
   notification voice, and complete cleanup.
2. Confirm complete process and socket cleanup after exiting that restarted TUI.
3. Add a controlled normalized-gain AMI variant and a licensed Chinese
   conversational set before selecting a production VAD default.
4. Test live VAD disconnect, runtime failure, manual-source disappearance, and
   provider reuse without enabling auto-endpointing.
5. Decide whether further whisper.cpp tuning or a coding-command corpus is
   justified; the first base/small run is complete but does not select a
   production ASR.
6. Measure native hard cancellation during model inference; timeout, provider
   retry, process disconnect, physical device switching, and restart recovery
   are already covered.
7. Choose and add the repository license; the GitHub repository is currently
   public but has no root license file.
8. Run blinded Piper/Kokoro listening and repeat controlled Chinese/mixed
   commands before selecting production defaults. Keep acoustic
   speaker-first-audio deferred until it affects a product decision.

## Risks and Blockers

- Agent integrations remain disabled by default. The legacy `codex` gateway
  handles prompt submission and interruption but not approval; the Alpha-only
  `codex-app-server` runtime adds one-shot approval and rejection for its
  explicitly managed remote TUI.
- Codex app-server and its Unix WebSocket transport are experimental. DeskHelm
  cannot attach to an unrelated already-running plain Codex TUI; multiple
  loaded threads are ambiguous, and secret or multi-question input remains in
  the manually operated TUI.
- PipeWire discovery, diagnostics, external PTT, streaming capture, an opt-in
  Paraformer/Piper composition, combined-runtime execution, advisory WebRTC
  VAD, real Codex turns, pure notifications, and live recovery are verified.
  Automatic VAD endpointing and partial transcript publication remain outside
  the Alpha.
- The benchmark runner records VAD compute/detection timing, batch ASR final
  latency, Paraformer offline first-partial estimates, and provider-chunk TTS
  first audio/interruption. Live capture-to-decision/partial,
  playback-to-speaker first audio, mid-inference interruption, and recovery
  timing remain unmeasured.
- Live default/manual input, output, streaming PCM, process disconnect,
  physical hot-plug/default switching, ASR timeout, and provider retry are
  verified. The best synchronized spoken run
  recovered all expected keywords with 0.181818 CER, which is improved but still
  unsuitable for exact code-sensitive commands. Repeated command coverage,
  alternative-ASR comparison, startup calibration, and actual
  speaker-first-audio latency need explicit product behavior.
- Adapter registrations are process-local and must be re-established after a
  Bridge restart. No durable session history or replay is promised.
- Approval tracking is bounded. When its capacity is exhausted, new approval
  requests remain visible to interaction subscribers but cannot be decided
  through the router and return `approval_not_found`.
- The default socket path changed during the pre-release rename; existing
  Bridge and hook processes must restart together after updating.
- Legacy CLI and module-execution aliases still exist and need a deprecation
  review before the first stable release.
- The public repository has no selected open-source license.
- The renamed Python distribution has not been built as a wheel on this
  machine because the system Python environment does not include `setuptools`;
  source-tree imports, both module entry points, and `pyproject.toml` metadata
  were validated directly.
- Codex compatibility has official-document and synthetic JSONL fixtures,
  fake-subprocess coverage, and repeated authenticated local runs. Claude and
  Gemini compatibility still lack versioned fixture sets.
- WebRTC and Silero VAD now have both the original FSDD set and one annotated
  AMI conversation set with real rain/clock noise and parameter sweeps. The
  evidence still lacks Chinese conversation, controlled gain variants, music,
  keyboard sounds, distant microphones, readiness-gated live signal metadata,
  and live VAD failure/recovery. The first built-in mode 0/2 comparison missed
  essentially the complete phrase because the default input was at 7%; an
  explicit healthy USB mode 2 run detected 2,800 ms. Production VAD remains
  unselected.
- Paraformer is measured on a tiny public set, one synchronized live Chinese
  command, one immediate four-phrase batch, and one handshake four-phrase
  batch. The handshake batch trailed SenseVoice on CER and latency, while mixed
  coding keyword scores in the historical run used the pre-fix spacing metric;
  English spacing, isolated digits, about 3.09 GiB peak RSS, and cancellation
  only between inference calls remain product risks. It is the local alpha ASR,
  not a final production selection.
- SenseVoice is final-only and cannot provide partials or cancel its native
  decode midway. It missed most isolated English digit clips, and its custom
  FunASR Model License 1.1 requires review before packaging or production use.
  Its handshake batch improved CER on the selected phrases. Historical keyword
  scores reflect the pre-fix whitespace behavior; current code now aligns
  keyword and CER whitespace normalization.
- The whisper.cpp base/small run is real CPU evidence, but the corpus is only
  eight public utterances and the Python runtime differs from earlier ASR runs.
  `small` failed all three short `zero` repetitions and both models have poor
  RTF for interactive use; cancellation remains cooperative and slow.
  In-process close/reload is covered only with a fake native library; process
  restart and hard in-flight cancellation remain unmeasured.
  The provider still requires an external compatible `v1.9.2` build and
  explicit model/library paths, and must not silently replace another ASR.
- Piper and Kokoro performance, resources, provider-chunk first audio,
  interruption boundaries, and licenses are measured, and live PipeWire
  interruption is verified. Blinded human quality and acoustic first-audio are
  not measured. Piper's GPL runtime still needs packaging review.

## Next Step

After selecting one Alpha to own the shared PTT device, stop/restart only that
instance as needed and verify that PTT follows its explicit focus, then verify
two consecutive approvals, one later voice-started turn, the selected
notification voice, and complete cleanup after exiting the TUI. A second
voice-enabled Alpha must fail closed rather than receive the same PTT input.

## Phase History

Local voice alpha is now fixed as Paraformer ASR plus Piper TTS. The first
live PTT run reached the fixed ASR-timeout failure after roughly 36 seconds;
two subsequent user-confirmed PTT commands completed and had audible Piper
output. The explicit output-tone check was audible. A live cancellation probe
then emitted `speech_cancelled` after audio began; the diagnostic response is
now Chinese-only to avoid Piper's poor English-brand-name pronunciation.

The alpha does not yet meet interactive latency expectations. The diagnostic
holds capture for six seconds, and current Paraformer final results took about
9.7-21.1 seconds after release. Its `speech_started` event precedes Piper
synthesis, so speaker-first-audio remains unmeasured; the user observed further
audible playback delay. Do not market this as low latency. Next decide whether
to first add real PTT release plus speaker-first-audio instrumentation and
persistent warm providers, or replace/tune the selected ASR path. ADR 0027
selects an explicit foreground terminal right-Alt PTT mode with one complete
active-session target. A future microcontroller button replaces the key source
and drives LEDs from Voice lifecycle state; it does not change PTT authorization
or targeting. Arbitrary application text fields remain a future IBus/Fcitx
integration. Then measure default-device disconnect/reconnect and input gain
behavior. Keep VAD advisory, do not add automatic provider fallback, and defer
SenseVoice until its matching verified native ABI is available. Obtain the
repository license decision before packaging models or accepting external
contributions.

Right-Alt coexistence with the user's required `keyd` left-Alt mappings is now
verified. `keyd` forwards unmodified `KEY_RIGHTALT` press, repeat, and release
events through its virtual keyboard; DeskHelm must listen only on the stable
`/dev/input/deskhelm-ptt-kbd` path rather than configuring `keyd` or opening
the physical keyboard. The next live check is to run the explicit controller
against that path with a temporary complete Bridge session and confirm accepted
press/release capture behavior. This validates physical PTT control only; the
temporary session has no Agent response path, and the alpha's latency remains
an open issue.

That controller check is now running with the internal microphone and was
confirmed by the user: holding right `Alt` activates microphone capture while
the `keyd` left-Alt mappings remain untouched. Model runtime memory rose during
the check, consistent with lazy ASR loading. The temporary target deliberately
has no prompt or assistant-message capability, so this proves the physical PTT
and capture path but cannot produce an automatic spoken reply. Wire a real
Agent session before treating the test as end-to-end ASR-to-TTS behavior.

The real-session voice phase is now entered. ADR 0028 defines the new
privacy-safe latency boundaries as TTS audio-ready and first PipeWire write;
they are not a claim of acoustic speaker-first-audio. The focused PTT,
Voice-to-Agent integration, and live-diagnostic tests pass under the documented
single Voice-module import path, and `git diff --check` passes. Strict uv
validation is currently blocked because the uncommitted root `pyproject.toml`
adds the `ptt` extra after the untracked root lock was generated; do not rewrite
that lock implicitly. A real run still needs an explicitly registered active
Agent session and the ignored local model artifact paths.

The temporary session was then replaced by a complete local Codex session using
the Bridge's bounded read-only gateway. The right-Alt controller remains bound
to the `keyd` virtual keyboard. The no-GUI controller now also subscribes only
to target-scoped, Voice-Gateway system failure messages and prints that fixed
guidance; it never displays audio, transcripts, prompts, or assistant text.
This closes the prior silent-failure gap during physical PTT testing.

The user then confirmed a successful live end-to-end run through the real
local Codex session: right-Alt activation, internal-microphone capture,
Paraformer recognition, bounded read-only Codex processing, and Piper playback
all completed. This is functional Alpha evidence only; capture-to-response and
speaker-first-audio latency remain unmeasured and are not low-latency claims.

ADR 0028 starts the performance-closure phase. Voice Gateway now emits
privacy-safe `tts_audio_ready` after a complete TTS provider result and
`playback_started` after PipeWire accepts its first PCM write. These are timing
handoff boundaries, not acoustic speaker-first-audio evidence. Focused gateway,
Bridge integration, and live diagnostic tests passed (30 tests); a full suite
and a new bounded live timing run remain next.

The full no-hardware suite now passes 237/237 in 5.760 seconds under the
isolated Python 3.12 local-voice runtime. Two deterministic regression tests
also verify the Voice lifecycle ordering from speech acceptance through PipeWire
handoff and that PipeWire invokes the first-write callback exactly once. The
focused Voice Gateway, PipeWire, and live-diagnostic suite passes 42/42.

The bounded live timing run then completed through capture, Paraformer, Piper,
and PipeWire. A follow-up revealed provider logs reaching the diagnostic
terminal, so Paraformer now suppresses third-party stdout, stderr, and Python
logging only around its model load and inference boundary, restoring the prior
logging state afterwards. The clean repeat emitted only the final privacy-safe
JSON: release-to-transcript was 7,030.224 ms, TTS result was 889.935 ms,
PipeWire first write was 0.656 ms later, and first write to completion was
2,581.790 ms. The diagnostic retained no audio or recognized text. Actual
speaker-first-audio and Codex turn timing remain unmeasured.

The user explicitly deferred acoustic loopback measurement. ADR 0029 therefore
starts content-free Voice-to-Agent turn timing instead: it will use only
target-scoped generated identifiers, monotonic timestamps, and terminal status
inside an explicit local diagnostic. It must not open audio devices, retain
interaction content, extend `InteractionEvent v1`, or claim acoustic latency.

The Agent Gateway now preserves the originating `submit_prompt` command ID as
an internal turn ID and places it only on the corresponding terminal
interaction's existing correlation field. This is the minimum correlation
needed for the local timer; provider message correlation remains unchanged and
no text is retained. Focused Agent and Voice integration tests pass 8/8.

The first `VoiceAgentTurnTimer` is now implemented as an explicit in-process
callback. It records only monotonic numeric deltas from dispatch to Agent
terminal, speech start, and PipeWire first-write, and emits one bounded result
without a turn ID or text. `VoiceBridgeIntegration` supplies the generated turn
ID only for Voice-originated replies; other speech retains its existing random
ID behavior. The timer is inactive unless a composition explicitly supplies a
result sink. Focused timer, Agent, and Voice integration tests pass 10/10.

The Bridge CLI now exposes that sink only through `--voice-turn-timing`. The
flag requires both Voice and Agent providers and writes one numeric-only JSON
record to stderr per terminal Voice-originated turn. Default startup remains
unchanged, and the output omits interaction text, PCM, and the internal turn ID.

The timer now tolerates Agent/Voice thread reordering: successful Agent turns
wait for the matching speech terminal before emitting, so a fast Agent terminal
does not erase later speech-start or PipeWire-first-write timing. Failed Agent
turns still emit immediately. Focused tests pass 18/18, the complete
no-hardware suite passes 244/244 in 5.489 seconds, and `git diff --check` passes.

The first live `--voice-turn-timing` run completed against a real local Codex
session. From transcript dispatch, speech was accepted after 4,004.357 ms,
PipeWire received its first write after 4,848.857 ms, and the Agent terminal
arrived after 4,995.123 ms. These values exclude ASR and acoustic output. No
interaction content or PCM was retained. The run also exposed and fixed a PTT
CLI cleanup issue: Ctrl-C now exits without printing a KeyboardInterrupt
traceback. All temporary processes and the explicit temporary socket were
removed after the run.

The end-to-end latency-closure phase has started. The same opt-in timer now
correlates accepted PTT release and `transcript_ready` with the existing Agent,
speech, and PipeWire stages, producing one same-turn numeric record from release
through software playback handoff. It still makes no acoustic claim and stores
no content. Focused tests pass 16/16; the complete no-hardware suite passes
246/246 in 5.677 seconds, and `git diff --check` passes.

The repeated live cold/warm run is complete with three right-Alt PTT turns in
one persistent Bridge process. Release-to-transcript was 7,597.163 ms cold,
then 214.626 and 201.639 ms warm. Release-to-speech-start was 13,861.000 ms,
then 4,354.864 and 5,085.818 ms; release-to-first-PipeWire-write was 14,826.557
ms, then 4,441.793 and 5,152.520 ms. Release-to-Agent-terminal was 24,041.706
ms, then 14,449.440 and 15,204.418 ms. From transcript dispatch, speech-start
was 6,263.794, 4,140.205, and 4,884.157 ms; first write was 7,229.352,
4,227.134, and 4,950.859 ms; Agent terminal was 16,444.500, 14,234.781, and
15,002.757 ms. The two warm turns averaged 208.132 ms to transcript and
4,797.156 ms to first write, confirming that the large first-turn ASR model
load is removed after warm-up while Agent processing remains the dominant warm
latency. These are software timing boundaries only; no acoustic latency or
interaction content was measured or retained. The PTT controller, temporary
publisher, persistent Bridge child, and exact temporary socket were cleaned up
after the run.

The next latency split is implemented. The opt-in timer now records the first
complete assistant response before it is enqueued for speech and observes the
existing `tts_audio_ready` event. Numeric output therefore separates Agent
response readiness, speech queue handoff, complete Piper synthesis, and
PipeWire first write while retaining the existing ASR and Agent-terminal
boundaries. It still receives no interaction text and emits no internal turn
identifier. Focused timer, Voice integration, and local composition tests pass
17/17 using the repository's single Voice-module import path. Successful Agent
turns with no assistant speech and speech-queue rejection now terminate their
timing records with missing stages instead of retaining capacity. The complete
no-hardware suite passes 247/247 in 5.694 seconds from the existing isolated
voice runtime. Strict `uv run --project voice/runtime --locked` remains blocked
because the current uncommitted root lock needs synchronization; it was not
rewritten implicitly. A repeated warm live run with these finer boundaries is
the next evidence step.

The first split-boundary live attempt was aborted after the user reported that
long speech would not stop. Its cold-path numeric record showed 6,594.815 ms
release-to-transcript, 8,932.513 ms dispatch-to-complete-assistant-response,
0.224 ms assistant-to-speech-start, 1,444.216 ms Piper synthesis, and 0.656 ms
audio-ready-to-first-PipeWire-write. The Agent terminal arrived 153,558.289 ms
after dispatch, far after playback began, so terminal time is not a useful
spoken-response latency boundary for this run. Two subsequent PTT captures
reported the fixed `voice_input_failed` status even though a separate bounded
one-second PipeWire input diagnostic succeeded. The exact Bridge child and its
active `pw-cat` playback process were terminated, and the PTT controller,
publisher, and temporary socket were removed. Do not resume performance runs
until live right-Alt playback interruption and retry behavior are verified.

The isolated live interruption check then passed without starting Codex. A
bounded interruptible Piper utterance was dispatched through the real Bridge
and PipeWire path; the user pressed right Alt during playback and confirmed
that speech stopped. Process inspection showed no remaining playback `pw-cat`,
while the Bridge, publisher, and PTT controller remained alive, proving that
right-Alt press cancels current speech rather than the unrelated Agent run.
After release, the PTT path again reported the fixed `voice_input_failed`
status. All isolated test processes and the temporary socket were removed.
The next step is to diagnose this repeatable post-interruption capture failure
before resuming latency measurements.

That failure is diagnosed and fixed: microphone capture and ASR were not the
failing stages. Pressing right Alt during a still-active Agent run correctly
cancelled playback and captured a new utterance, but the one-active-run bound
rejected its downstream `submit_prompt`; the Gateway's broad exception mapping
misreported that rejection as `voice_input_failed`. Prompt-sink absence or
rejection now emits the distinct privacy-safe `voice_prompt_failed` code, and
the PTT controller presents fixed guidance that the voice instruction could
not be submitted. Capture/model failures remain `voice_input_failed`. Focused
Voice Gateway, integration, and right-Alt tests pass 31/31; the complete
no-hardware suite passes 248/248 in 5.907 seconds under the resource-limited
isolated voice runtime, and `git diff --check` passes. The next live check is to
repeat interruption during an active Codex run and confirm the corrected busy
message before resuming split warm-latency measurements.

The corrected busy path is now verified live with a real read-only Codex run.
After right-Alt interruption, three attempted follow-up utterances while the
original run remained active all produced the fixed guidance that the voice
instruction could not be submitted; none was mislabeled as microphone input
failure. The user also observed that speech stopped immediately but a later
partial utterance played. This is consistent with the current contract: PTT
press cancels only the current interruptible playback and does not cancel Codex
or suppress a later complete assistant message from the same active run. All
temporary Bridge, Codex, publisher, PTT, playback, and socket resources were
removed. Before resuming latency work, decide whether right-Alt interruption
should also mute subsequent speech from the same Agent turn while allowing the
Agent run itself to continue; that would be a new product behavior and requires
an ADR update before implementation.

The user selected pure notification mode. ADR 0030 now supersedes automatic
assistant-response speech from ADR 0009 for this path: assistant messages,
tool output, progress, prompts, and final response text never enter TTS or
notification state. Only `task_completed`, `task_failed`, `approval_request`,
and `user_input_required` enqueue short fixed content-free notifications.
Explicit targeted `speak` controls remain available, and right Alt still
cancels only current interruptible playback before starting PTT; it does not
cancel Codex. Turn timing now names notification readiness instead of assistant
readiness and remains numeric-only. Focused integration, timer, Gateway, and
composition tests pass 42/42. The complete no-hardware suite passes 250/250 in
5.940 seconds under the resource-limited isolated voice runtime, and
`git diff --check` passes. A live speaker-only check of the fixed completion
notification then traversed `speech_started`, `tts_audio_ready`,
`playback_started`, and `speech_completed` with no failure. It did not open the
microphone or start Codex. The user confirmed hearing the complete fixed phrase,
so the pure-notification completion path is verified through actual speaker
output.

The pure-notification event-closure phase is complete. A temporary modern
adapter session submitted a real read-only Codex prompt through the bounded
gateway. The interaction subscriber observed one assistant message followed by
`task_completed` but printed no response text, and the user heard only the fixed
completion notification. A second modern publisher emitted
`approval_request`, `user_input_required`, and `task_failed` events containing
private synthetic content; the user confirmed hearing the three fixed category
notifications in order and none of the private content. All temporary Bridge,
Codex, publisher, controller, PipeWire, socket, and directory resources were
removed. The next development phase is live recovery testing: device changes,
disconnects, timeouts, provider failures, and repeated turns. Focused Voice
integration, timer, Gateway, and composition tests pass 42/42; the complete
resource-limited no-hardware suite passes 250/250 in 5.769 seconds, and
`git diff --check` passes.

The first live recovery slice is implemented and software-verified. A new
Agent Gateway regression test proves that a failed terminal run releases its
session and permits the next prompt. In one persistent resource-limited Bridge,
the same modern adapter session then completed three real Codex turns without
restarting Bridge, Codex session state, or Piper; every control was accepted,
every terminal was `task_completed`, and no response text was printed. A
follow-up real run accepted an immediate `interrupt`, emitted `task_failed`
without an assistant message, then accepted a same-session retry that emitted
one assistant message and `task_completed`; response text again remained
silent. The expected audible sequence was three completion notifications,
followed by one failure and one completion notification; the user confirmed
hearing that sequence. All temporary processes, PipeWire sessions, sockets,
and directories were removed. Live audio disconnect, device-change, and
provider-runtime recovery remain next. Focused Agent, Voice Gateway, PipeWire,
and audio-selection recovery tests pass 49/49; the complete resource-limited
no-hardware suite passes 251/251 in 5.522 seconds, and `git diff --check`
passes. For future delegated test or validation work, use a Luna-model
subagent; direct primary-agent test execution remains allowed.

The first live audio-process recovery slice is software-verified. Two fresh
PipeWire inventory snapshots independently resolved the same current default
source and sink. During a real microphone capture, the owned `pw-cat` process
was forcibly terminated after receiving audio; the provider reported only the
fixed `PipeWire capture process failed` category, retained no PCM, and the same
provider then completed a fresh roughly 297 ms capture. The playback equivalent
reported the fixed `PipeWire playback input failed` category after its owned
process was terminated, then the same provider completed a fresh 250 ms
low-volume tone. A missing manually selected source failed closed with no
fallback. No `pw-cat` process remained. The user heard several short beeps,
consistent with the first playback beginning before forced termination and the
final 250 ms recovery playback completing; this confirms audible playback
recovery as well as the software result. Physical hot-unplug, actual
default-device switching, ASR timeout, and model-runtime recovery remain next.
Focused PipeWire and audio selection tests pass 22/22, the previously completed
full suite remains 251/251, and `git diff --check` passes.

The live ASR recovery slice is software-verified with the real pinned
Paraformer model and public prepared Chinese audio. A deliberately tiny 50 ms
Gateway deadline during cold model startup emitted `voice_asr_timeout`; after
raising only the next request's bounded deadline, the same Gateway, ASR worker,
and provider returned a 19-character transcript without printing its text.
The loaded real model was then wrapped to inject exactly one private controlled
`generate` failure. The provider released its inference lock, and the same
instance successfully decoded the next request. The diagnostic retained no PCM
and printed neither transcript nor failure text. A Luna test subagent ran the
focused and complete regressions. It reported focused Voice Gateway and ASR
provider tests passing 32/32 in 0.51 seconds, the resource-limited complete
suite passing 251/251 in 5.779 seconds, and `git diff --check` passing. Physical
hot-unplug and actual default-device switching are now the remaining live
recovery checks.

Physical audio hot-plug and default switching are live-verified with a PCM2902
USB microphone. Insertion exposed the stable source
`alsa_input.usb-C-Media_Electronics_Inc._USB_PnP_Sound_Device-00.analog-mono`
and WirePlumber selected it as default. A two-second explicit USB capture had
valid signal (7.46% peak, 3.19% RMS), and fresh DeskHelm snapshots followed
temporary default changes between built-in and USB sources. After physical
unplug, default discovery fell back to the built-in microphone while explicit
USB selection returned `configured PipeWire source is unavailable` with exit
status 1 and no fallback. Replug restored the same stable node and a successful
one-second capture (10.51% peak, 5.12% RMS). The original built-in microphone
was restored as system default using a freshly resolved, non-persisted
PipeWire object ID. PCM was discarded after signal measurement. A Luna test
subagent reported focused PipeWire and audio-selection tests passing 22/22 in
0.575 seconds, `git diff --check` passing, and no residual `pw-cat` process.
The reusable local Alpha startup profile described in the current-state
sections is now implemented and verified. The multi-project working-directory
registry is also complete; the next roadmap item is expanded noisy and
conversational VAD coverage.

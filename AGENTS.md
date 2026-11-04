# Project Constitution

These principles govern all AI-assisted and agent-driven work in this
repository. When another instruction conflicts with this constitution, follow
the higher-priority user or system instruction and document the conflict when
it affects the result.

## 1. Use Visualizations Purposefully

When explaining complex workflows, architecture, dependencies, state changes,
or repeated comparisons, use the smallest effective visualization such as a
table, flowchart, timeline, or tree. Do not add diagrams to simple facts or
one-step operations merely for presentation.

## 2. Be Concise and Evidence-Based

Lead with conclusions and the evidence that materially supports them. Clearly
distinguish verified facts, reasonable inferences, and unknowns. Prefer project
source material, official documentation, and reliable primary sources. Never
present an unverified assumption as fact.

## 3. Stay Aligned With the User's Goal

Every analysis, recommendation, and change must serve the user's current
objective and respect the stated scope, constraints, and priorities. Do not
expand the task, alter the business goal, or add unrequested functionality for
technical completeness. When the goal conflicts with the current
implementation, explain the conflict and propose the smallest viable
resolution.

## 4. Ask Only for Critical Decisions

Do not interrupt the user when repository evidence or a reasonable low-risk
assumption allows progress. Ask for confirmation only when missing information
would materially change the result, expand scope, cause irreversible impact, or
require a key business decision. Briefly disclose consequential assumptions in
the result.

## 5. Use Subagents With Restraint

Use subagents only when work can be cleanly separated, parallel execution
materially improves efficiency, or an independent review or specialist
capability is valuable. Do not split simple work for appearance or assign
duplicate work to multiple agents. The primary agent owns integration, conflict
resolution, validation, and final delivery.

When a test or validation task is delegated to a subagent, use a Luna model for
that subagent. This does not require delegating tests that the primary agent can
run directly.

## 6. Keep Changes Focused

Limit code and documentation changes to the smallest scope required to complete
the current objective. Do not include unrelated refactors, formatting,
dependency upgrades, or directory changes. If broader work is necessary,
explain why and preserve existing interfaces, behavior, and user-owned work.

## 7. Validate Real Outcomes

Do not declare completion because code is present, reads correctly, or appears
finished. Run risk-appropriate builds, tests, execution checks, or output
inspections that verify the result the user actually needs. If validation
cannot be completed, state what remains unverified, why, and the residual risk.

## 8. Protect Existing Code and Data

Preserve existing code, uncommitted changes, configuration, and data. Never
overwrite, delete, revert, or reset user work without clear authorization.
Resolve destructive targets and impact before acting; ask when scope is
unclear. If unexpected changes appear during work, stop and notify the user.

## 9. Report Only Meaningful Progress

Report progress that affects user decisions: important findings, scope changes,
risks, validation results, and blockers. Do not stream mechanical operations,
repeat plans, or send status messages without new information. Final delivery
should emphasize outcomes, changed locations, validation, and necessary next
steps.

## 10. Keep AGENTS.md Current

Update `AGENTS.md` whenever project structure, critical commands, test
procedures, engineering constraints, or durable collaboration rules change.
Record only stable, reusable information that affects future work; do not add
temporary task history or one-off decisions. Check updates against the
repository and related documentation.

## 11. Maintain Handoff.md

After each identifiable phase of work, update the root `Handoff.md` so work can
stop and resume at any time. Record the current objective and status, completed
work, key decisions, changed files, validation results, remaining work, next
steps, risks, blockers, and essential context. Keep it as a concise statement
of current state rather than an append-only activity log. Never include
secrets, tokens, or sensitive credentials. Leave a short final state when a
task is complete.

## 12. Use uv for Python Dependencies

Use `uv` for every Python dependency resolution, environment creation,
synchronization, lock update, and dependency-backed command in this repository.
Do not use `pip`, `venv`, `virtualenv`, Conda, or direct interpreter package
installation. Keep Bridge dependencies minimal. Model-backed voice dependencies
remain in the separate `voice/runtime` uv project and synchronize only into
ignored or external storage; never add them to the Bridge package.

# DeskHelm Repository Guidelines

## Project Identity and Scope

DeskHelm is a local-first control surface for coding agents. The GitHub
repository and product name are `DeskHelm` / `deskhelm`. DeskHelm 0.1.0
removed the pre-release `agent-io` names (ADR 0003, ADR 0043); do not
reintroduce them.

The project is a monorepo with these responsibility boundaries:

```text
Agent runtimes
  -> drivers in adapters/, each inside one session host process
  -> Bridge: session facts, focus, routing, receipts, and approvals
     -> surfaces: web page, dashboard, and the voice host
     -> Physical Surface (future): HID, controls, RGB, displays, and transport
```

DeskHelm integrates existing Agent runtimes. It must not become a new general
LLM or Agent framework, and it must not own an external Agent's canonical
conversation history.

## Project Structure

- `bridge/`: the standard-library packages `deskhelm_protocol` (protocol v2
  models, framing, and paths), `deskhelm_core` (the Bridge), `deskhelm_host`
  (the session-host runtime and `simulate`), `deskhelm_client` (the shared
  client runtime and transcript fold), `deskhelm_web`, `deskhelm_dashboard`,
  `deskhelm_voice_host`, and `deskhelm_cli` (the `deskhelm` command).
- `adapters/`: drivers for `codex/` (app-server adapter, TUI proxy, and Codex
  host), `claude/` (Claude Code stream-json), and `acp/` (generic ACP with the
  Grok flavor).
- `voice/`: the provider-neutral Voice Gateway library `deskhelm_voice`, its
  benchmarks, and the pinned `voice/runtime` uv project.
- `voice/native/whisper-cpp-shim/`: optional external whisper.cpp C-shim source
  and build notes; native libraries and models remain outside Git.
- `protocol/`: the protocol v2 contract. The v1 documents remain only as the
  record that older ADRs link to.
- `configurator/`, `hardware/`, and `firmware/`: future device work.
- `tests/`: cross-component, compatibility, and replay tests.
- `docs/`: research, architecture, product, software, hardware, and ADRs.
- `tools/`: the user launcher, the PTY relay, the web page's browser check,
  and voice diagnostics and benchmark runners.

The root `pyproject.toml` maps every package directory, and `uv sync` installs
the checkout in editable mode, so code, tests, and tools import packages by
name without `PYTHONPATH`. Keep third-party reference files outside version
control under `references/vendor/`. Keep model weights and generated binaries
outside Git.

## Architecture Invariants

ADR 0043 defines the architecture, `protocol/deskhelm-v2.md` the wire
contract, and the component READMEs the details. Phase 5 (hardware and remote
access ADRs) has not started.

### Bridge and Protocol

- The Bridge core in `bridge/deskhelm_core` is one single-threaded actor
  without locks or I/O. It owns session facts, focus, routing, idempotency
  receipts, and approval tickets, stays standard-library only, and imports no
  adapter, voice, or rendering code.
- One Unix socket carries two roles fixed by `hello`: hosts and clients. A
  client has a tier: operator, voice, device, or observer. Privacy is filtered
  per tier on output. Fact `meta` is content-free and may reach every tier;
  `content` reaches only the operator interaction stream. State derives only
  from `meta`.
- Identify sessions by `agent_id + session_id + project_id`. Keep `project_id`
  opaque: the Bridge never maps it to a path, and no frame carries a working
  directory. Display order is presentation, never identity.
- The Bridge generates approval and turn ids; vendor ids stay with the host
  as `provider_ref`. Approval commands name the Bridge approval id.
- Every command names its complete target, an `issued_by` equal to the
  client id, an issue time, an expiry, and an idempotency key, and receives
  exactly one correlated `command_result`. Receipts are scoped by
  `issued_by + idempotency_key`; a retry repeats the complete command, and a
  live receipt is never evicted to admit a new dispatch.
- An approval is consumed when its dispatch was accepted or may have reached
  the runtime. A host answers `undelivered` only when nothing reached the
  vendor, and that approval stays pending. No receipt before the deadline is
  `dispatch_failed`. Approvals and rejections are never retried
  automatically.
- Focus changes only through an explicit `focus` command on an active session
  and clears when that session disconnects or ends. Nothing focuses
  implicitly, and a reopened session is not focused again.
- Bound everything: 1 MiB UTF-8 NDJSON frames, the handshake time,
  connections, sessions, receipts, approvals, outbound queues, and the
  interaction window. A full outbound queue closes that connection, and the
  client resubscribes.
- A console delta carries a session only when its view changed, so
  streaming content never resends the session list (ADR 0044).
- The interaction window (D1) is bounded per session, held in memory only,
  not canonical, and dropped when the session ends. Never promise durable
  history or replay. A message's complete fact carries its whole text, so
  the window keeps a completed message, not its deltas.
- When an approval ends, the core publishes `approval_resolved` with how it
  ended, so every surface says the same; a surface never infers it.
- The voice tier may only submit prompts and answer approvals. The device and
  voice tiers and logs never receive command lines, command output, or diffs.

### Session Hosts and Drivers

- Each session runs in one host process (`bridge/deskhelm_host`) that owns
  the vendor process and the driver; its Bridge connection is the session
  lease. After a Bridge restart the host keeps its vendor process,
  reconnects, and reopens its session with its open requests.
- Every host and surface defaults to `bridge.sock` in the DeskHelm runtime
  directory. A host starts the Bridge only before its first open, with output
  in `bridge.log`, and never restarts one that later stops. A socket held by
  anything other than a v2 Bridge is named and refused.
- A driver declares its capability matrix when the session opens; the Bridge
  enforces it and returns typed `unsupported` results. A driver records a
  turn before the prompt reaches the vendor, reports one terminal outcome per
  turn, honors interrupt, and rebuilds a reopened session from `state()`.
- Parse vendor formats inside `adapters/`. Tool labels are fixed kinds or bare
  tool names; command lines, paths, and tool arguments stay in fact content,
  and a tool's start fact carries its command or path there. A tool's
  complete fact states `completed`, `failed`, or `declined` (refused before
  it ran), and an exit code comes only from the vendor. Do not publish
  reasoning, working directories, image bytes, local image paths, or vendor
  error text.
- Publish each accepted prompt once as a complete user message correlated by
  its command id; one matching vendor echo is not published again (ADR 0042).
- Never place prompts in process arguments. Prefer bounded stdin, suppress
  private stderr from ordinary logs, and terminate the owned process group on
  cancellation or timeout.
- Do not log prompts, source code, tool arguments, raw Agent events, audio, or
  credentials by default.
- Keep runtime fixture provenance explicit: distinguish official
  documentation examples from synthetic boundaries, and never claim a fixture
  was locally captured unless it was.

### Codex

- `deskhelm codex` integrates manually operated Codex only through its own
  `codex app-server`, a TUI approval proxy, and `codex --remote` under the PTY
  relay. Do not inject terminal keystrokes or claim that an unrelated plain
  Codex process can be attached.
- Keep the app-server adapter Alpha-only while its WebSocket transport is
  experimental, with bounded frames, queues, requests, pending approvals,
  pending input, timeouts, and reconnect backoff. Treat transient proxy queue
  saturation as backpressure: pause upstream reads instead of dropping,
  reordering, or disconnecting a healthy turn. Keep DeskHelm approval
  decisions on a distinct bounded priority queue.
- Proxy command, file, and permission approvals on their original JSON-RPC
  response channel. A keyboard answer clears DeskHelm's record; a DeskHelm
  answer suppresses the late TUI response through `serverRequest/resolved`
  until a fixed expiry, and resolution is notified once. Disconnect, expiry,
  or capacity fails closed without inferring a decision, and excess approvals
  stay answerable from the keyboard. A permission approval grants only the
  requested subset for the current turn and is never persisted.
- Map the `waitingOnApproval` status to notification-only attention, never by
  itself to an answerable approval.
- Attach directly to one unambiguous loaded thread, then retry bounded
  `thread/resume` calls until this connection is subscribed. Only a resume
  result, a server request, or turn events on this connection prove the
  subscription; Codex 0.166.0 sends `turn/start` and status changes to every
  connection. A subscribed adapter ignores its copy of an approval the proxy
  carries, and a user item counts once, at `item/completed`. A missing
  rollout is retriable only for a discovered thread. Ignore one positively
  identified ephemeral internal thread, and require an explicit selector among
  several user threads.
- Allow one extra short-lived TUI connection for the `/resume` picker. It
  never replaces the primary TUI's approval channel, and a confirmed resume
  makes the adapter attach to that thread before later prompts are routed.
- A steer rejected because no turn is active sends one `turn/start` with the
  same prompt (ADR 0041); any other rejected steer becomes a system notice.
  Keep token deltas opted out.
- One host per directory: a second is refused, stale directory sockets are
  removed, and the sockets stay directory-scoped (`codex-<digest>-app.sock`,
  `codex-<digest>-tui.sock`). On exit the host closes its session and stops
  its app-server; the Bridge keeps running.
- The relay turns a first raw or Kitty-protocol Ctrl-C into clear-input and
  forwards Ctrl-C only on a second press within a bounded window; timeout or
  other input resets the guard.
- The host pushes the catalog (models, MCP status, and skill names) when its
  thread attaches, after a turn at most once a minute, and after a model
  choice, and the Bridge caches it, so a query never blocks on a host. The
  session view's `catalog_rev` counts pushed catalogs; a client reads the
  catalog again when it changes and never polls.
  `configure` stores a model and reasoning effort for the next `turn/start`
  only after a catalog read from the last 110 seconds; otherwise it is
  undelivered and a fresh read starts. It never writes Codex configuration,
  calls MCP tools, returns skill bodies, or follows a vendor cursor.
  `/compact`, `/plan`, and `/fast` remain deferred.

### Claude and Grok

- `deskhelm agent claude|grok` runs that vendor in the current directory as
  one session host; prompts stay on the vendor's stdin. Agents DeskHelm did
  not start stay off the page.
- Claude runs `-p` with `--permission-prompt-tool stdio`, so a tool that needs
  permission arrives as `can_use_tool`; without it Claude Code 2.1.312 denies
  such tools silently. The driver declines `AskUserQuestion` at once because
  the session takes no input. Claude Code 2.1.312 sends `system/init` only
  with the first prompt, so the driver sends the SDK `initialize` request at
  start and is ready when Claude answers it; the answer names the account
  and is never published.
- Grok approvals select only the option whose ACP kind is `allow_once` or
  `reject_once`. Without one, the decision is undelivered and the request
  stays pending; never fall back to `allow_always` or `reject_always`. A tool
  name or approval summary uses the vendor title only when it is a bare name,
  otherwise the ACP kind, and a tool keeps the label it had at `tool_call`.
  Interrupt sends `session/cancel` as a notification and answers every open
  permission request with the `cancelled` outcome.

### Surfaces

- Surfaces share `bridge/deskhelm_client`: one connection per process, the
  console resumed from its cursor on the same Bridge instance and reset on a
  new one, replayed interaction handed over once, commands never retried, and
  one copy of result wording in `deskhelm_client/texts.py`, which the web
  page receives with each snapshot. Surfaces decide approvals by Bridge
  approval id from the console, so a reconnecting surface still sees pending
  approvals, including one its transcript does not hold.
- The web page (ADR 0036, ADR 0044) is a replaceable client: one process
  holds one operator connection and fans it out to browsers. A page opened
  later gets the Bridge's bounded recent window and pending approvals, which
  is not durable history. It does not create sessions, read transcript
  files, or focus a browsed session. It serves on port 8766 and binds
  `127.0.0.1` unless `--allow-remote` is explicit.
- The web server folds each session's interaction events with
  `deskhelm_client.transcript`, which applies each event once by its
  sequence, and sends a browser a snapshot (again after a Bridge restart),
  then revisioned `sessions` and `patch` events; a page that misses a
  revision starts again from a snapshot. Keep transcript logic in that
  module, not in the page. The page is plain ES modules with no build step,
  framework, or dependency. Agent text is never parsed as HTML: it reaches
  the DOM as text, and only `http`, `https`, and `mailto` URLs become links.
  The page names its command ids, never retries a command, and labels an
  unknown outcome as unknown.
- Every web request needs the token stored with mode 0600 in the runtime
  directory. The printed `?token=` link sets an HttpOnly, SameSite=Strict
  cookie and redirects to a token-free path. A loopback bind accepts only
  `127.0.0.1` or `localhost` Host headers. POST requires `application/json`,
  and a browser Origin must match the request Host.

### Voice

- Keep `voice/` independent of the Bridge. The voice host
  (`bridge/deskhelm_voice_host`) is the composition boundary: it attaches at
  the voice tier, targets the focused session, and derives fixed notices from
  console transitions. Agent response content stays silent.
- Approval phrases (D3): an exact approval or rejection phrase decides the
  focused session's single answerable approval with a one-shot response and
  never becomes a prompt; with no such approval or several, it is refused
  with a fixed notice. Spoken `取消` discards the utterance and never rejects.
  Persistent, ambiguous, multi-question, and secret interactions stay in the
  vendor's own interface.
- The right-Alt listener is a child process that reads
  `/dev/input/deskhelm-ptt-kbd`, keyd's virtual keyboard, without `EVIOCGRAB`
  and holds a non-blocking flock on `ptt.lock` in the runtime directory; a
  second listener fails closed. Right Alt still reaches the compositor.
  DeskHelm does not inject keys or open the physical keyboard (ADR 0039).
- Model-backed voice stays opt-in. Validate the selected devices and model
  artifacts before attaching, and keep model imports lazy. PyTorch, CUDA,
  weights, and provider dependencies stay out of the root package: they are
  pinned in `voice/runtime/pyproject.toml` and its `uv.lock`, which install
  the checkout as an editable path dependency, and synchronize only into
  ignored or external storage.
- Allow one active PTT capture and transcription, bound speech queues, and
  require providers to honor cancellation. Starting PTT cancels only current
  interruptible playback. A PTT release must match its press's activation id
  and complete target.
- Gateway capture prefers `StreamingCaptureProvider`, validates format and
  continuity, and aggregates under fixed chunk, byte, and duration bounds
  until PTT release; batch providers stay compatible. Do not describe batch
  PTT capture as VAD integration.
- Preserve raw and normalized transcripts separately. Lifecycle and failure
  events carry no audio, transcript, prompt, or speech text.
- A new ASR request is the retry boundary: release provider locks on
  failure, never retry recognition implicitly, and check cancellation at each
  provider's documented inference boundary.
- Local audio follows the current PipeWire default source and sink unless a
  stable node name is configured. Re-resolve defaults from fresh inventory
  snapshots. A configured device that is missing fails with a message naming
  its setting, never its device, and never falls back silently. Do not
  persist numeric PipeWire object IDs or add Opus to this path. Discovery is
  read-only, and input and output diagnostics are explicit user actions that
  discard PCM after measuring it and play only short low-volume tones.
- Represent local audio as complete-frame S16LE PCM with an explicit rate and
  channel count. Keep `pw-cat` providers bounded by bytes and duration, own
  their process group, suppress private stderr, and terminate then kill
  within a bounded grace period. A validated command prefix such as
  `host-spawn -no-pty pw-cat` serves Distrobox; native execution uses
  `pw-cat`.
- Model streaming PCM as complete, contiguous chunks of at most 1 MiB with one
  immutable format and absolute frame positions. Each stream opens one VAD
  session with ordered alternating boundaries and an explicit flush. Live VAD
  stays optional, advisory, and disabled by default: it never stops or trims
  capture, skips final ASR, or publishes partial transcripts, and a failure
  emits one fixed event while PTT continues. The voice host does not enable
  it yet.
- Keep WebRTC, Silero, Paraformer/FunASR, SenseVoice/sherpa-onnx, Piper,
  Kokoro, and IndexTTS lazy, pinned with revisions, licenses, sizes, and
  checksums, and outside the root package. Silero resets state per stream,
  Paraformer uses one streaming cache per transcription, and shared models
  are serialized. SenseVoice is final-only and reports no first partial.
  Review FunASR Model License 1.1 and Piper's GPL-3.0-or-later runtime before
  packaging or distribution.
- Piper is the low-latency notification baseline and Kokoro the quality
  candidate; neither is the final production choice. A Kokoro directory uses
  `zf_xiaobei` for Chinese and `af_heart` for English. TTS first audio is the
  first complete provider chunk, not PCM-frame streaming, and cancellation
  during one model inference is not claimed.
- Keep the whisper.cpp fallback behind the DeskHelm ABI in
  `voice/native/whisper-cpp-shim/` with explicit library and model paths, and
  never fall back to it silently from another ASR provider.
- Keep benchmark corpora and observation formats versioned. Bound each NDJSON
  observation to 1 MiB, each result file to 64 MiB, each run to 10,000
  observations, and VAD samples to 100,000 chunks, 256 segments, and 64 MiB
  PCM. Record exact provider and model versions, licenses, and an anonymous
  system profile. External audio comes from versioned manifests with pinned
  revisions, HTTPS sources, SHA-256 checksums, licenses, and composition
  recipes, and stays in ignored storage.
- Live ASR and VAD diagnostics use versioned public phrases, keep PCM and
  recognized text only in memory, suppress provider output, and publish only
  signal, aggregate activity, accuracy, length, timing, and fixed failures.
  Input-level hints never change system gain, and diagnostic VAD never gates
  or alters ASR. Do not interpret a live ASR result until the user confirms
  whether they spoke the prompted phrase.
- Do not commit microphone captures, generated speech, model output, or local
  benchmark results without explicit provenance, consent, and redistribution
  terms.
- Once DeskHelm hardware audio exists, prefer its microphone unless the user
  selected another source; record disconnect fallback in an ADR first.
  ESP32-S3 plus Opus is a researched direction, not a selected MCU or frozen
  transport.

Record significant protocol, transport, concurrency, framework, MCU, or
licensing decisions in an ADR before implementation spreads across components.

## Build, Test, and Development Commands

The root package needs Python 3.11 or newer and has no runtime dependencies.
A repository-wide build system has not been selected; do not add a
placeholder task runner without an ADR.

Setup:

- `uv sync --extra ptt --locked`: synchronize the root environment with the
  optional `evdev` listener. Resynchronize after pulling so the editable
  install sees new packages.
- `cd voice/runtime && uv sync --locked --python 3.12`: synchronize the
  ignored, pinned voice runtime. Run it from that directory so uv resolves
  that project's lock.
- Link `tools/deskhelm-user-launcher` into the user's executable path. It
  preserves the invoking working directory by `exec`, uses the synchronized
  root environment without installing anything, and never injects a socket
  or signals a process. Only `codex --voice` and `voice` run under the
  checkout's encrypted `project-secrets` profile, which injects the
  `DESKHELM_ALPHA_*` voice settings without storing their values. Because
  `project-secrets` forwards output line by line, the Codex host's relay
  renders directly to the controlling terminal and redacts those values
  itself.

Run these from the Agent project directory; every Bridge command takes
`--socket` to name another Bridge:

- `deskhelm codex --check [--voice [--default-source]]`: find Codex and, with
  `--voice`, check the voice models, PipeWire selection, and right-Alt key
  without opening audio or loading weights.
- `deskhelm codex [--codex-sandbox workspace-write]`: run the managed Codex
  TUI here as one session. It needs no project secrets or voice runtime.
  `--tui` stays accepted, and `deskhelm alpha` is a hidden alias.
- `deskhelm codex --yolo`: launch that TUI with Codex's full-access,
  no-approval mode. This bypasses the sandbox and approval boundary; use it
  only when that level of access is intended.
- `deskhelm codex --voice [--default-source]`: also run the voice host for
  this session, with output in `codex-<digest>-voice.log`.
- `deskhelm agent claude|grok`: run that vendor here as one session.
- `deskhelm web [--port 8766] [--allow-remote]`: serve the page. Open the
  printed `http://127.0.0.1:8766/?token=…` link once per browser.
- `deskhelm dashboard`, `deskhelm sessions`, and `deskhelm focus --agent-id
  <id> --session-id <id> --project-id <id>`: watch and steer, list sessions
  with pending approvals, or set the focus that voice speaks to.
- `deskhelm voice [--default-source] [--check]`: attach local voice to the
  running Bridge. Settings default to `DESKHELM_ALPHA_VOICE_SOURCE`,
  `DESKHELM_ALPHA_VOICE_SINK`, `DESKHELM_ALPHA_ASR_MODEL_DIRECTORY`, and
  `DESKHELM_ALPHA_TTS_DIRECTORY`; `--default-sink` also exists.
- `deskhelm simulate`: open a demo session that needs no agent.
- `deskhelm bridge`: run the Bridge in the foreground; hosts normally start
  it. A `--socket` must be absolute. It owns no Agent process and replaces
  the socket file that a SIGTERM leaves behind.
- `deskhelm audio status --list`, `deskhelm audio test-input --seconds 2`,
  and `deskhelm audio test-output`: resolve PipeWire devices without opening
  audio, or explicitly test the selected input or output.
- `<checkout>/.venv/bin/python -m deskhelm_host <claude|codex|grok>`, and
  `-m deskhelm_core`, `-m deskhelm_client`, `-m deskhelm_web`, or
  `-m deskhelm_dashboard`: the module entry points behind those commands.
- `uv run --project <checkout>/voice/runtime --locked --no-sync python -m
  deskhelm_voice_host [--check] [--no-ptt] [--default-source]`: the voice
  host behind `deskhelm voice`. Its right-Alt listener runs with the root
  environment's Python, which needs the `ptt` extra.

Run tests from the repository root:

- `uv run --project voice/runtime --locked --no-sync python -m unittest
  discover -s tests -v`: the full no-hardware suite. Model imports stay lazy.
  The root environment (`uv run --locked python -m unittest discover -s
  tests -v`) runs everything except the ASR provider tests, which need NumPy.
- `uv run --locked python -m unittest tests.test_core tests.test_core_server
  tests.test_host_link tests.test_drivers tests.test_codex_host
  tests.test_replay_transcripts tests.test_client tests.test_transcript
  tests.test_web_surface tests.test_dashboard_v2 tests.test_voice_host
  tests.test_cli_switch tests.test_simulate tests.test_user_launcher -v`: the
  v2 core, hosts, drivers, transcript fold, surfaces, command line, and
  launcher without a vendor CLI, audio device, or model.
- `uv run --locked python -m unittest tests.test_codex_app_server_transport
  tests.test_codex_app_server_adapter tests.test_codex_app_server_fixtures
  tests.test_codex_tui_proxy -v`: the Codex WebSocket, thread attachment,
  approval proxy, and JSON-RPC fixtures without a model request.
- `uv run --locked python -m unittest tests.test_replay_transcripts -v`:
  replay every transcript in `tests/fixtures/replay/` through the real
  drivers.
- `uv run --no-project --with esprima python -I -c "import esprima, pathlib;
  [esprima.parseModule(p.read_text()) for p in
  pathlib.Path('bridge/deskhelm_web/assets/js').rglob('*.js')]"`:
  syntax-check every page module in a throwaway environment; there is no
  Node here. esprima knows ES2017, so write `catch (error)`, not `catch {`.
- `uv run --locked --with playwright python tools/check-web-page.py
  [--chrome /usr/bin/google-chrome] [--screenshots DIR]`: drive the page in
  Chrome against a real Bridge and scripted sessions, and check replies,
  tools, approvals, prompt outcomes, streaming, interrupt, focus, switching,
  and the theme. Playwright downloads no browser; run this after page work.
- `git diff --check`: detect whitespace errors.

Voice benchmark, corpus, and live diagnostic runners are documented with
their privacy limits in `voice/README.md` and `voice/benchmarks/README.md`.
Keep their observations, models, and native libraries outside Git.

Document component-specific commands in that component's `README.md`. Add a
repository-level wrapper only when multiple implemented stacks require it.

## Coding and Naming Conventions

Follow `.editorconfig`: UTF-8, LF line endings, a final newline, two-space
indentation, and no trailing whitespace. Makefiles use tabs. Prefer descriptive
names over abbreviations.

- Directories and files: `kebab-case`.
- ADRs: `docs/decisions/NNNN-short-title.md`.
- Dated research: `docs/research/YYYY-MM-DD-topic.md`.
- Protocol fields and configuration keys: `snake_case` unless an adopted
  ecosystem requires otherwise.
- Tests: name after observable behavior.

## Testing and Compatibility

Add tests beside their component or under `tests/` for cross-component
behavior. Protocol and adapter changes require captured or synthetic fixtures,
including producing runtime versions where applicable. Test malformed input,
unknown events, cancellation, queue limits, and terminal states, not only happy
paths. Adapter changes keep the replay transcripts in `tests/fixtures/replay/`
passing and add a transcript for new vendor behavior. Recording a live vendor
session is an explicit operator action; recorded transcripts are anonymized
and reviewed before they are committed.

Firmware changes should include hardware-in-the-loop notes when necessary.
Record every untested hardware or model assumption explicitly.

## Git and Review

Use focused commits with short imperative subjects, optionally with a
conventional prefix, such as `protocol: define interaction envelope`. Do not
mix unrelated formatting, refactors, generated artifacts, or dependency updates
into a feature commit.

Before committing, run the relevant tests and `git diff --check`. Pull requests
should explain the problem, approach, validation, affected components, risks,
and linked ADRs. Include screenshots, recordings, schematics, or renders for
visible changes.

Do not commit secrets, credentials, generated binaries, model weights, vendor
archives, or files with unclear commercial licensing.

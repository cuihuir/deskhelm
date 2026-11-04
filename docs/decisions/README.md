# Architecture Decision Records

文件命名格式为 `NNNN-short-title.md`。每条 ADR 至少记录背景、候选方案、决定、影响和状态。

Current voice decisions include
[`0013-select-webrtc-and-silero-vad-baselines.md`](0013-select-webrtc-and-silero-vad-baselines.md),
which fixes the first reproducible external-audio VAD candidates without making
a final production selection.

[`0014-use-paraformer-as-initial-streaming-asr-baseline.md`](0014-use-paraformer-as-initial-streaming-asr-baseline.md)
pins the first licensed streaming ASR baseline and records why it is not yet the
sole production default.

[`0015-use-piper-as-initial-notification-tts-baseline.md`](0015-use-piper-as-initial-notification-tts-baseline.md)
selects Piper for low-latency notification experiments while retaining Kokoro
as the quality candidate and deferring the final production choice.

[`0016-explicit-local-audio-selection-and-diagnostics.md`](0016-explicit-local-audio-selection-and-diagnostics.md)
defines default/manual stable-device selection and privacy-safe explicit local
audio diagnostics without prematurely activating production voice models.

[`0017-correlate-external-ptt-press-and-release.md`](0017-correlate-external-ptt-press-and-release.md)
adds targeted external PTT controls and prevents stale or cross-session release
commands from stopping an unrelated capture.

[`0018-opt-in-provisional-local-voice-composition.md`](0018-opt-in-provisional-local-voice-composition.md)
composes PipeWire, Paraformer, and Piper behind an explicit Bridge opt-in while
deferring VAD until the gateway has a real streaming capture path.

[`0019-advisory-live-vad-with-ptt-fallback.md`](0019-advisory-live-vad-with-ptt-fallback.md)
attaches optional live VAD as bounded activity observation while keeping PTT
release authoritative and isolating VAD failure from final ASR.

[`0020-evaluate-sensevoice-as-second-asr-baseline.md`](0020-evaluate-sensevoice-as-second-asr-baseline.md)
selects a compact final-only SenseVoice/sherpa-onnx comparison while recording
its non-streaming boundary and custom model-license risk.

[`0021-bounded-voice-recovery-and-device-rebind.md`](0021-bounded-voice-recovery-and-device-rebind.md)
defines retry boundaries, cancellation points, PipeWire session recovery, and
strict default/manual device rebinding.

[`0022-bounded-multi-phrase-asr-diagnostic.md`](0022-bounded-multi-phrase-asr-diagnostic.md)
defines repeatable phrase selection, fresh captures, per-phrase confirmation,
and privacy-safe batch output for coding-command comparisons.

[`0023-one-by-one-asr-readiness-handshake.md`](0023-one-by-one-asr-readiness-handshake.md)
adds an opt-in bounded stdin handshake before each phrase capture so
chat-driven diagnostics do not race automatic prompt transitions.

[`0024-bounded-asr-timeout.md`](0024-bounded-asr-timeout.md) adds a bounded
Voice Gateway ASR deadline, fixed timeout failure, and explicit retry boundary
without claiming hard cancellation for native provider calls.

[`0025-isolated-whisper-cpp-shim-provider.md`](0025-isolated-whisper-cpp-shim-provider.md)
keeps the whisper.cpp fallback behind a pinned, DeskHelm-specific C ABI and
explicit provider opt-in without adding native dependencies to Bridge.

[`0026-local-voice-alpha-profile.md`](0026-local-voice-alpha-profile.md)
selects the explicit Paraformer/Piper local alpha, defines its reusable
environment-injected startup profile, and publishes fixed privacy-safe voice
failure guidance without blocking text or Agent work.

[`0027-right-alt-ptt-and-ime-scope.md`](0027-right-alt-ptt-and-ime-scope.md)
defines an explicit terminal right-Alt PTT mode and future hardware controller,
while reserving arbitrary application text entry for IBus/Fcitx integration.

[`0028-voice-latency-observability.md`](0028-voice-latency-observability.md)
defines privacy-safe TTS and PipeWire handoff timing without claiming actual
speaker-first-audio measurement.

[`0029-private-voice-agent-turn-timing.md`](0029-private-voice-agent-turn-timing.md)
defers acoustic loopback and defines bounded, local numeric timing for a
Voice-originated Agent turn without exposing interaction content.

[`0030-notification-gated-agent-speech.md`](0030-notification-gated-agent-speech.md)
keeps all Agent response content silent and uses fixed notifications only for
completion, failure, approval, or required user input.

[`0031-operator-configured-project-working-directories.md`](0031-operator-configured-project-working-directories.md)
maps opaque project identities to bounded operator-approved Agent working
directories without accepting filesystem paths from adapters or protocol data.

[`0032-pinned-noisy-conversation-vad-evidence.md`](0032-pinned-noisy-conversation-vad-evidence.md)
adds a backward-compatible annotated conversation/noise manifest, bounded
candidate sweeps, and a privacy-safe live timing diagnostic while keeping VAD
advisory and leaving the production default unselected.

[`0033-bidirectional-codex-app-server-adapter.md`](0033-bidirectional-codex-app-server-adapter.md)
selects the experimental Codex app-server Unix WebSocket protocol for an
Alpha-only bidirectional manual-session adapter with bounded thread discovery,
privacy-safe notifications, and exact-phrase voice approval decisions.

[`0035-bridge-native-tui-dashboard.md`](0035-bridge-native-tui-dashboard.md)
defines the TUI Dashboard as a replaceable Bridge client, separates cursor,
selection, focus, and attention, and defers PTY ownership and remote session
supervision to later versioned capabilities.

[`0036-local-loopback-web-client.md`](0036-local-loopback-web-client.md)
adds a loopback Codex-layout web client that fans one state subscription and
one interaction subscription out to browsers. Selecting a thread does not
focus it, and the page does not create sessions or replay history.

[`0037-session-catalog-and-model-selection.md`](0037-session-catalog-and-model-selection.md)
adds a read-only session catalog for models, MCP status, and skill names, plus
a `configure_session` control that applies on the next new Codex turn.

[`0038-shared-user-bridge-attached-sessions.md`](0038-shared-user-bridge-attached-sessions.md)
puts every default Alpha session on one user bridge. Web and later clients
attach once. Explicit `--socket` stays a private bridge. This slice does not
add a remote app or an IM channel.

[`0039-observe-right-alt-without-grabbing.md`](0039-observe-right-alt-without-grabbing.md)
keeps right-Alt PTT on keyd's virtual keyboard and stops the exclusive grab.
One listener is a flock. The desktop keeps every other key.

[`0040-transparent-agent-supervision.md`](0040-transparent-agent-supervision.md)
puts Codex, Claude, and Grok on one bridge. Session query and the catalog
carry each runtime's capabilities, and every client reads the same fields.

[`0041-vendor-thread-follow.md`](0041-vendor-thread-follow.md)
starts one new Codex turn when steer reports that no turn is active. The
page stays on the existing interaction pipe.

[`0042-one-user-message-per-prompt.md`](0042-one-user-message-per-prompt.md)
publishes each accepted prompt once on the interaction plane. A vendor echo
of that same text is not published again.

[`0043-architecture-v2.md`](0043-architecture-v2.md)
turns the Bridge into a single-threaded control plane, moves each agent into
a session host process and voice into its own host, and gives every surface
one console projection. Phases 0 to 4 shipped in DeskHelm 0.1.0. It
supersedes ADRs 0002, 0007, 0008, and 0031 and amends others; the status line
of each affected ADR says what changed.

[`0044-server-built-transcript-and-module-page.md`](0044-server-built-transcript-and-module-page.md)
folds interaction events into a server-built transcript that any surface can
reuse, sends the page revisioned patches, and rebuilds the page as plain ES
modules with no build step. It also makes the Bridge publish how each
approval ended and keep whole replies, not their deltas, in its window.

[`0045-tool-outcomes-and-request-details.md`](0045-tool-outcomes-and-request-details.md)
makes a tool's completion state whether it completed, failed, or was
declined, and lets a request carry its detail, such as a command line, to the
operator only, so an approval says what it would allow. It drops
compatibility with DeskHelm 0.1.0.

[`0046-answering-agent-questions.md`](0046-answering-agent-questions.md)
lets an input request carry its questions and options, adds an operator-only
`answer` command, and makes Codex's questions, including the
`request_user_input_async` questions it does not wait on, answerable on the
page.

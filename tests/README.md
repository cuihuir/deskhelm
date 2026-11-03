# Tests

Cross-component fixtures, protocol conformance tests, and hardware-in-the-loop test assets.

Protocol compatibility fixtures live under `tests/fixtures/protocol/`. Keep
fixtures as complete wire objects and round-trip them through the corresponding
versioned model.

Current fixture sets cover `InteractionEvent v1`, all `ControlCommand v1`
kinds, `ControlResult v1`, and state/interaction subscription frames.
Adapter session lifecycle fixtures live under `tests/fixtures/protocol/`.
Runtime-specific evidence lives under `tests/fixtures/adapters/`; each set must
record provenance and distinguish official examples from synthetic fixtures.

`FakeAgentProvider` verifies prompt streaming, bounded capacity, session resume,
and cancellation without external runtimes. `tests/helpers/fake_codex_cli.py`
verifies subprocess startup, stdin prompt delivery, JSONL parsing, timeout,
termination, malformed output, and nonzero exit without a Codex login or model
request.
`test_codex_app_server_transport.py` verifies the dependency-free Unix
WebSocket handshake, client masking, bounded frames, and JSON messages.
`test_codex_tui_proxy.py` verifies the bounded server-side WebSocket handshake,
bidirectional forwarding, original-channel voice decisions, keyboard-first
resolution, voice-first duplicate suppression, disconnect cleanup, and
capacity behavior. It also drives a multi-frame notification burst through a
one-entry forwarding queue and verifies ordered delivery, bounded backpressure,
priority voice-decision delivery, and continued bidirectional use after
saturation. Consecutive-approval coverage verifies that a TUI response arriving
after `serverRequest/resolved` remains suppressed and that the same JSON-RPC ID
can later produce a distinct actionable approval.
`test_codex_app_server_adapter.py` uses a fake app-server transport to verify
thread resume, same-thread prompts, terminal correlation, fixed private-content-
It also verifies that proxied TUI approvals become actionable and that keyboard
decisions clear the Control Router record.
Schema-derived synthetic JSON-RPC fixtures for locally inspected Codex CLI
0.149.0 live under `fixtures/adapters/codex-app-server-v2/`.
`test_web_client.py` and the thread-item projections in
`test_codex_app_server_adapter.py` use synthetic examples derived from the
public Codex app-server ThreadItem documentation. They are not locally
captured fixtures.
`test_session_catalog.py` checks the bounded catalog normalizer.
`configure-session.json` round-trips with the other control fixtures. The
adapter catalog tests use a synthetic model, MCP, and skills page derived from
the public app-server schema. Skill paths, tool schemas, and the working
directory stay out of the public result. A model override is checked against
that page and is sent on the next `turn/start` only.

Voice tests use fake capture, ASR, TTS, and playback providers to verify PTT
state, raw/normalized transcript separation, recoverable provider failure,
bounded speech, playback interruption, targeted controls, and the complete
fake Voice-to-Agent-to-speech pipeline without audio hardware or model weights.
`test_pipewire_providers.py` uses `tests/helpers/fake_pw_cat.py` to verify
default and stable-name targeting, explicit frame-aligned PCM, byte/time bounds,
startup and process failures, cancellation, and forced process-group cleanup.
It never opens a live microphone or speaker.
`test_audio_config.py` uses synthetic PipeWire discovery data and fake capture
to verify default/manual selection, stable-name failures, provider composition,
signal-only input reports, bounded test tones, and CLI arguments without
opening live audio.
`test_local_voice_config.py` verifies opt-in CLI parsing, exact device/artifact
preflight, lazy Paraformer plus Piper/Kokoro construction, Kokoro female voice
selection, and bounded PipeWire composition without importing model runtimes or
opening audio.
`test_local_alpha.py` verifies the hardware-independent Alpha core, opt-in
environment-injected voice artifacts, directory-based Piper/Kokoro selection,
fixed app-server/Bridge/PTT targeting,
printed and foreground remote-TUI modes, interactive-terminal and signal
ownership, repeated-interrupt handling, and independent bounded child cleanup.
`test_run_with_pty.py` verifies that the encrypted user-launcher relay gives
all three child streams terminal semantics, preserves child exit status, and
redacts environment values even when they are split across output chunks with
no newline. It also verifies bounded exit when a descendant retains the PTY
after the primary child has stopped.
`test_project_registry.py` verifies legacy single-directory compatibility,
strict opaque project mapping, capacity and directory validation, CLI parsing,
and mutual exclusion with `--agent-workdir`.
`test_voice_benchmark.py` validates the versioned synthetic corpus, bounded
NDJSON observations, fake-provider runners, CER/WER and keyword metrics,
resource fields, privacy-preserving failure records, and unknown-input rejection.
`test_vad_benchmark.py` validates contiguous PCM chunks, independent VAD
sessions, ordered speech-boundary events, segmentation metrics, fixed failure
records, NDJSON round trips, and CLI summaries without audio devices or models.
`test_vad_tools.py` validates manifest v1 compatibility, annotated clip bounds,
deterministic SNR mixing, sweep profile isolation and capacity checks, and the
privacy-safe live diagnostic signal aggregation plus bounded readiness
handshake without opening an audio device.

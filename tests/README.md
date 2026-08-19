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
preflight, lazy Paraformer/Piper construction, and bounded PipeWire composition
without importing model runtimes or opening audio.
`test_voice_benchmark.py` validates the versioned synthetic corpus, bounded
NDJSON observations, fake-provider runners, CER/WER and keyword metrics,
resource fields, privacy-preserving failure records, and unknown-input rejection.
`test_vad_benchmark.py` validates contiguous PCM chunks, independent VAD
sessions, ordered speech-boundary events, segmentation metrics, fixed failure
records, NDJSON round trips, and CLI summaries without audio devices or models.

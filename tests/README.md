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

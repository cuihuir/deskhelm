# Voice Gateway

Provider-neutral push-to-talk and speech-output core for DeskHelm. This package
has no import dependency on Bridge and no runtime audio or model dependency.

## Boundaries

- `VoiceGateway` owns one capture/transcription flow and one playback worker.
- `CaptureProvider`, `AsrProvider`, `TtsProvider`, and `PlaybackProvider` isolate
  device and model implementations.
- `VoiceTarget` always names `agent_id + session_id + project_id`.
- `Transcript` keeps raw and normalized text separate.
- The speech queue is fixed-capacity, priority-aware, and interruptible.
- Lifecycle events contain identifiers and fixed error codes, not audio or
  private text.

The Bridge composition is in
[`bridge/deskhelm_bridge/voice_integration.py`](../bridge/deskhelm_bridge/voice_integration.py).
It converts final normalized transcripts into targeted controls and complete
assistant messages into speech items.

## Current Providers

`fake_providers.py` provides deterministic capture, ASR, TTS, and playback for
tests. `pipewire.py` provides bounded raw-PCM capture and playback through
`pw-cat`. It follows the current PipeWire default source/sink when no target is
set, or accepts a manually selected stable node name. Numeric object IDs are
rejected because they are not durable across PipeWire graph changes.

The PipeWire providers are library boundaries only and are not selected by the
Bridge CLI yet. Capture defaults to 16 kHz mono S16LE, 30 seconds, and 1 MiB;
playback defaults to 120 seconds and 16 MiB. Both own and terminate their
subprocess groups, suppress private stderr, and expose fixed recoverable errors.
Local ASR/VAD and production TTS providers remain pending. Keep model weights
and provider-specific heavyweight dependencies outside the repository and
outside Bridge.

## Benchmarks

[`benchmarks/`](benchmarks/) contains the versioned synthetic utterance corpus
and measurement documentation. `deskhelm_voice.benchmark` provides bounded fake
or production provider runners, NDJSON observations, CER/WER and keyword
scoring, and latency/resource summaries without model dependencies.

```bash
PYTHONPATH=voice python3 -m deskhelm_voice.benchmark score-asr \
  --corpus voice/benchmarks/utterances-v1.json \
  --observations /path/to/asr-observations.ndjson
```

## Tests

```bash
PYTHONPATH=bridge python3 -m unittest \
  tests.test_pipewire_providers tests.test_voice_benchmark \
  tests.test_voice_gateway \
  tests.test_voice_integration -v
```

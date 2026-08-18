# Voice Gateway

Provider-neutral push-to-talk and speech-output core for DeskHelm. This package
has no import dependency on Bridge and no runtime audio or model dependency.

## Boundaries

- `VoiceGateway` owns one capture/transcription flow and one playback worker.
- `CaptureProvider`, `AsrProvider`, `TtsProvider`, and `PlaybackProvider` isolate
  device and model implementations.
- `PcmChunkStream` uses contiguous absolute frame positions, while each
  `VadProvider` run owns an independent session with explicit final flushing.
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
The fake providers include a deterministic streaming VAD session for benchmark
and lifecycle tests. `webrtc_vad.py` and `silero_onnx_vad.py` provide the first
real VAD benchmark adapters with lazy optional imports. `paraformer.py` provides
the first real streaming ASR benchmark adapter, pinned to a verified model
revision and the official 600 ms chunk configuration. These providers are not
selected by the Voice Gateway yet. Paraformer remains a Chinese candidate, not
the sole production ASR for mixed coding commands. Local production TTS remains
pending. Keep model weights and provider-specific heavyweight dependencies
outside the repository and outside Bridge.

## Benchmarks

[`benchmarks/`](benchmarks/) contains the versioned synthetic utterance corpus
and measurement documentation. `deskhelm_voice.benchmark` provides bounded fake
or production provider runners, NDJSON observations, ASR accuracy scoring, VAD
segmentation metrics, and latency/resource summaries without model dependencies.
`vad-external-v1.json` pins six public FSDD clips and seven deterministic
silence-composition scenarios. `asr-external-v1.json` pins an official Chinese
sample, a non-redistributable official English sample with unverified audio
license, and six CC BY-SA 4.0 FSDD clips. Downloaded audio, prepared WAV files,
models, and raw observations remain ignored.

```bash
PYTHONPATH=voice python3 -m deskhelm_voice.benchmark score-asr \
  --corpus voice/benchmarks/utterances-v1.json \
  --observations /path/to/asr-observations.ndjson
```

```bash
PYTHONPATH=voice python3 -m deskhelm_voice.benchmark summarize-vad \
  --observations /path/to/vad-observations.ndjson
```

Prepare and run the external VAD set from an isolated environment:

```bash
PYTHONPATH=voice python tools/prepare-vad-benchmark.py \
  --manifest voice/benchmarks/vad-external-v1.json \
  --artifact-root references/vendor/vad-bench/run-v1

PYTHONPATH=voice python tools/run-vad-benchmark.py \
  --provider webrtc \
  --manifest voice/benchmarks/vad-external-v1.json \
  --prepared references/vendor/vad-bench/run-v1/prepared \
  --observations voice/benchmarks/results/webrtc-v1.ndjson
```

Prepare and run the pinned Paraformer set from an isolated Python 3.12
environment containing FunASR and matched CPU-only PyTorch/torchaudio:

```bash
PYTHONPATH=voice python tools/prepare-asr-benchmark.py \
  --manifest voice/benchmarks/asr-external-v1.json \
  --artifact-root references/vendor/paraformer-bench/run-v1

PYTHONPATH=voice python tools/run-asr-benchmark.py \
  --manifest voice/benchmarks/asr-external-v1.json \
  --prepared references/vendor/paraformer-bench/run-v1/prepared \
  --model-directory /ignored/pinned/model/snapshot \
  --observations voice/benchmarks/results/paraformer-v1.ndjson \
  --summary voice/benchmarks/results/paraformer-v1-summary.json \
  --repetitions 3 --cpu-threads 4
```

## Tests

```bash
PYTHONPATH=bridge python3 -m unittest \
  tests.test_pipewire_providers tests.test_voice_benchmark \
  tests.test_vad_benchmark tests.test_vad_providers tests.test_asr_providers \
  tests.test_voice_gateway \
  tests.test_voice_integration -v
```

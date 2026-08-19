# Voice Gateway

Provider-neutral push-to-talk and speech-output core for DeskHelm. This package
has no import dependency on Bridge and no runtime audio or model dependency.

## Boundaries

- `VoiceGateway` owns one capture/transcription flow and one playback worker.
- `StreamingCaptureProvider` supplies complete-frame `PcmChunk` values with
  contiguous absolute frame positions; legacy batch `CaptureProvider` remains
  compatible.
- The Gateway validates one immutable stream format and aggregates under fixed
  chunk, byte, and duration limits until PTT release before final ASR.
- Each future live `VadProvider` run owns an independent session with explicit
  final flushing.
- `VoiceTarget` always names `agent_id + session_id + project_id`.
- Externally driven PTT release must match both the complete target and the
  activation ID derived from its press command.
- `Transcript` keeps raw and normalized text separate.
- The speech queue is fixed-capacity, priority-aware, and interruptible.
- Lifecycle events contain identifiers and fixed error codes, not audio or
  private text.

The Bridge composition is in
[`bridge/deskhelm_bridge/voice_integration.py`](../bridge/deskhelm_bridge/voice_integration.py).
It converts final normalized transcripts into targeted controls and complete
assistant messages into speech items. It also registers targeted `press_ptt`
and correlated `release_ptt` handlers when a Voice Gateway is composed.

## Current Providers

`fake_providers.py` provides deterministic capture, ASR, TTS, and playback for
tests. `pipewire.py` provides bounded raw-PCM capture and playback through
`pw-cat`. It follows the current PipeWire default source/sink when no target is
set, or accepts a manually selected stable node name. Numeric object IDs are
rejected because they are not durable across PipeWire graph changes.

`PipeWireCaptureProvider.open_stream()` owns the `pw-cat` process and emits
contiguous `PcmChunk` values as complete frames arrive. Its existing `capture()`
method is now a compatibility wrapper over the same stream. The Gateway prefers
`open_stream()` when present, rejects gaps or format changes, and closes the
owned stream on success, cancellation, or failure.

`audio_config.py` adds process-local provider/device selection and bounded
PipeWire discovery. `deskhelm audio status` resolves defaults or manual stable
names without opening audio. The explicit `test-input` command discards PCM
after reporting duration, peak, and RMS; `test-output` plays a short low-volume
tone. These diagnostics do not enable model-backed voice in the Bridge service.

Capture defaults to 16 kHz mono S16LE, 30 seconds, and 1 MiB; playback defaults
to 120 seconds and 16 MiB. Both own and terminate their subprocess groups,
suppress private stderr, and expose fixed recoverable errors.
The fake providers include a deterministic streaming VAD session for benchmark
and lifecycle tests. `webrtc_vad.py` and `silero_onnx_vad.py` provide the first
real VAD benchmark adapters with lazy optional imports. `paraformer.py` provides
the first real streaming ASR benchmark adapter, pinned to a verified model
revision and the official 600 ms chunk configuration. These providers are not
selected by the Voice Gateway yet. Paraformer remains a Chinese candidate, not
the sole production ASR for mixed coding commands. `piper_tts.py` and
`kokoro_tts.py` provide the first lazy, bounded streaming TTS adapters. Piper
Chaowen is the initial low-latency notification baseline; Kokoro remains the
quality candidate. Neither is selected as production TTS. The opt-in local
composition currently uses Piper; Kokoro remains benchmark-only. Keep model
weights and provider-specific heavyweight dependencies outside the repository
and Bridge.

`local_gateway.py` provides the first opt-in application composition. It
preflights PipeWire selection plus required Paraformer/Piper artifact files,
then constructs lazy model providers without opening audio or loading weights.
The Gateway can attach optional advisory WebRTC VAD to the frame-positioned
stream. It publishes only bounded speech start/end frame positions and a fixed
failure code; PTT release still ends capture and the complete bounded recording
is passed to final ASR. VAD endpointing and partial transcripts are not
implemented.

Paraformer returning no text is reported as the fixed safe error
`voice_no_transcript`, distinct from capture, format, runtime, or model failures
reported as `voice_input_failed`. Neither error includes audio or transcript
content.

`LocalAudioConfig.pw_cat_command_prefix` allows an application to execute a
compatible host `pw-cat` when a container's version lacks required raw-PCM
options. Native execution defaults to `pw-cat`; the verified Distrobox path is
`host-spawn -no-pty pw-cat`.

## Unified Local Runtime and Live Diagnostic

`runtime/requirements-local-voice-py312.txt` pins the optional CPU runtime used
to execute Paraformer and Piper together. It includes Piper's Chinese runtime
dependencies `g2pw` and `sentence-stream`. Install it only into ignored or
external storage; do not add these dependencies to Bridge.

`tools/run-local-voice-live.py` is an explicit bounded microphone/speaker
diagnostic. It requires `--live-audio`, captures for 2-15 seconds, discards PCM,
does not print transcript text, synthesizes a fixed public response, and emits
privacy-safe JSON timings. It does not call Codex and does not measure actual
speaker-first-audio because `SPEECH_STARTED` currently precedes synthesis.

The verified container invocation uses:

```bash
PYTHONPATH=voice /ignored/py312/bin/python tools/run-local-voice-live.py \
  --live-audio \
  --asr-model-directory /ignored/paraformer-snapshot \
  --tts-model /ignored/piper/voice.onnx \
  --tts-config /ignored/piper/voice.onnx.json \
  --tts-resource-directory /ignored/piper/resources \
  --pw-cat-command-prefix "host-spawn -no-pty pw-cat" \
  --capture-seconds 4 --cpu-threads 4 \
  --vad-provider webrtc
```

Omit `--vad-provider webrtc` to keep VAD disabled. A VAD miss or runtime
failure never trims the recording or prevents final ASR.

`tools/run-local-asr-diagnostic.py` isolates microphone input and Paraformer
from TTS/playback. It prompts one versioned public corpus utterance, captures a
bounded in-memory recording, suppresses provider output, and reports only:

- duration, peak, RMS, clipped-sample, and near-silence measurements;
- a provisional input-level hint that never changes system gain;
- transcript character count, exact match, CER, keyword accuracy, and latency;
- fixed capture, empty-transcript, or ASR failure codes.

It neither saves PCM nor prints recognized text. Example:

```bash
PYTHONPATH=voice /ignored/py312/bin/python \
  tools/run-local-asr-diagnostic.py \
  --live-audio \
  --asr-model-directory /ignored/paraformer-snapshot \
  --pw-cat-command-prefix "host-spawn -no-pty pw-cat" \
  --utterance-id zh-repeat-01 \
  --capture-seconds 6 --lead-in-seconds 3 --cpu-threads 4
```

See
[`docs/research/2026-08-19-local-voice-runtime-and-live-path.md`](../docs/research/2026-08-19-local-voice-runtime-and-live-path.md)
for measured results and limitations.

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

Prepare and run a pinned TTS candidate from an isolated Python 3.12 environment
containing its optional runtime:

```bash
PYTHONPATH=voice python tools/prepare-tts-benchmark.py \
  --manifest voice/benchmarks/tts-candidates-v1.json \
  --artifact-root references/vendor/tts-bench/run-v1

PYTHONPATH=voice python tools/run-tts-benchmark.py \
  --candidate piper-chaowen-medium \
  --manifest voice/benchmarks/tts-candidates-v1.json \
  --prepared references/vendor/tts-bench/run-v1/prepared \
  --corpus voice/benchmarks/utterances-v1.json \
  --observations voice/benchmarks/results/piper-chaowen-v1.ndjson \
  --summary voice/benchmarks/results/piper-chaowen-v1-summary.json \
  --repetitions 3 --cpu-threads 4
```

## Tests

```bash
PYTHONPATH=bridge python3 -m unittest \
  tests.test_pipewire_providers tests.test_voice_benchmark \
  tests.test_vad_benchmark tests.test_vad_providers tests.test_asr_providers \
  tests.test_tts_providers tests.test_voice_gateway \
  tests.test_voice_integration tests.test_local_voice_config \
  tests.test_local_voice_live_tool -v
```

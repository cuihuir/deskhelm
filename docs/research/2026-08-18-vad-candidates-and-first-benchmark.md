# VAD Candidates and First External-Audio Benchmark

Date: 2026-08-18

Status: First reproducible baseline; not a production selection

## Conclusion

Keep WebRTC VAD and Silero VAD ONNX as the first two baselines. On this small,
quiet, trimmed English-digit set, WebRTC produced the better F1 score and much
lower replay latency. Silero recovered slightly more labeled speech but added
more speech padding and false-positive duration. These observations are useful
for validating the benchmark and adapters, not for choosing the final live VAD.

| Candidate | Precision | Recall | F1 | Detection p50/p95 | Replay p50/p95 | Mean RTF |
|---|---:|---:|---:|---:|---:|---:|
| WebRTC mode 2 | 0.859 | 0.932 | 0.894 | 60 / 60 ms | 0.19 / 0.31 ms | 0.00015 |
| Silero 6.2.1 ONNX | 0.789 | 0.944 | 0.859 | 62 / 78 ms | 2.98 / 4.57 ms | 0.00246 |

The five repetitions contained 35 successful observations and no provider
failures per candidate. Per repetition, WebRTC accumulated about 197 ms false
negative and 444 ms false positive duration; Silero accumulated 162 ms false
negative and 738 ms false positive duration.

## Candidate Evidence

### WebRTC VAD

- The Python wrapper accepts 16-bit mono PCM at 8, 16, 32, or 48 kHz in exact
  10, 20, or 30 ms frames and exposes aggressiveness modes 0 through 3.
- `webrtcvad-wheels` 2.0.14 was released on 2024-09-05. It built successfully
  from source under local Python 3.14.6.
- The wrapper is MIT licensed; the embedded WebRTC VAD code uses a BSD-style
  license. The benchmark records the upstream implementation revision
  `e283ca41df3a84b0e87fb1f5cb9b21580a286b09`.

Sources:

- <https://github.com/wiseman/py-webrtcvad>
- <https://raw.githubusercontent.com/wiseman/py-webrtcvad/master/README.rst>
- <https://raw.githubusercontent.com/wiseman/py-webrtcvad/master/LICENSE>
- <https://pypi.org/project/webrtcvad-wheels/>

### Silero VAD

- Silero VAD 6.2.1 supports 8 kHz and 16 kHz audio and is MIT licensed.
- The direct ONNX adapter uses the tagged model at commit
  `7e30209a3e901f9842f81b225f3e93d8199902b1`, SHA-256
  `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`.
- The adapter reproduces the official 16 kHz streaming input shape: a
  512-sample window plus 64 samples of retained context, with recurrent state
  reset for every stream.
- ONNX Runtime 1.29.0 supports local Python 3.14 and is MIT licensed. The run
  used sequential execution with one inter-op and one intra-op CPU thread.

Sources:

- <https://github.com/snakers4/silero-vad>
- <https://github.com/snakers4/silero-vad/releases/tag/v6.2.1>
- <https://raw.githubusercontent.com/snakers4/silero-vad/master/LICENSE>
- <https://pypi.org/project/onnxruntime/>

## External Run Set

The run set uses six recordings from the Free Spoken Digit Dataset (FSDD), one
from each speaker, pinned to commit
`26eb9aaf76e81b692f806f9140c2d2777410d7a1`. FSDD contains 3,000 mono 8 kHz
recordings and is licensed CC BY-SA 4.0. Exact source URLs and SHA-256 values are
in `voice/benchmarks/vad-external-v1.json`.

The preparation tool verifies every source checksum, converts each clip to
16 kHz mono S16LE through `/usr/bin/ffmpeg`, surrounds it with known generated
silence, pads the result to complete 20 ms chunks, and writes a local index with
prepared-file checksums and exact reference frame intervals. A seventh scenario
joins two speakers with a 240 ms silence gap.

Source:

- <https://github.com/Jakobovski/free-spoken-digit-dataset>
- <https://raw.githubusercontent.com/Jakobovski/free-spoken-digit-dataset/26eb9aaf76e81b692f806f9140c2d2777410d7a1/README.md>

## Reproduction

The isolated environment and all resulting assets belong under ignored paths.
The exact installed versions for this run were `webrtcvad-wheels==2.0.14`,
`onnxruntime==1.29.0`, and Python 3.14.6 on Linux x86-64.

```bash
PYTHONPATH=voice python tools/prepare-vad-benchmark.py \
  --manifest voice/benchmarks/vad-external-v1.json \
  --artifact-root references/vendor/vad-bench/run-v1

PYTHONPATH=voice python tools/run-vad-benchmark.py \
  --provider webrtc \
  --manifest voice/benchmarks/vad-external-v1.json \
  --prepared references/vendor/vad-bench/run-v1/prepared \
  --observations voice/benchmarks/results/webrtc-v1.ndjson \
  --repetitions 5
```

Silero uses the same command with `--provider silero` and the ignored pinned
model path supplied through `--silero-model`.

## Limitations and Next Evidence

- FSDD clips are quiet, short, English, and already trimmed. Treating the full
  clip as speech overstates boundary certainty at its edges.
- The corpus contains no keyboard noise, fan noise, music, reverberation,
  overlapping speech, Chinese speech, or distant microphone conditions.
- Offline replay latency excludes model download, model load, PipeWire capture,
  scheduling, and application routing.
- The next VAD phase should add conversational and noisy labeled samples,
  threshold sweeps, live default/manual microphone latency, and hot-unplug /
  reconnect behavior before selecting a Voice Gateway default.

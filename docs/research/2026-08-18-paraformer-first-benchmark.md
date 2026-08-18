# Paraformer Streaming ASR: First Local Benchmark

Date: 2026-08-18

Status: Initial baseline; not a production default

## Conclusion

Paraformer is a viable Chinese streaming candidate, but this first run rejects
using it as DeskHelm's only ASR. It transcribed the official Chinese sentence
exactly and processed audio comfortably faster than real time on four CPU
threads. The official English sentence retained the characters but merged two
word boundaries, while only two of six isolated English digits were correct.

| Metric | Result |
|---|---:|
| Observations | 24 / 24 successful |
| Mean CER | 0.438 |
| Mean English WER | 0.643 |
| Mean keyword accuracy | 0.438 |
| Final compute p50 / p95 | 44.7 / 467.4 ms |
| Estimated first partial p50 / p95 | 376 / 1,848 ms |
| Mean compute RTF | 0.121 |
| Cold provider/model load | 4.97 s |
| Whole-process peak RSS | 3.09 GiB |

The aggregate accuracy metrics are deliberately harsh: they average one
Chinese sentence, one English sentence, and six single-word digits equally.
They are useful for exposing failure modes, not for claiming population-level
ASR quality.

## Per-Sample Findings

| Sample | Reference | First output | Outcome |
|---|---|---|---|
| Official Chinese | 欢迎大家来体验达摩院推出的语音识别模型 | same | exact |
| Official English | he tried to think how it could be | he tried tothink howit could be | characters retained; WER 0.5 |
| FSDD zero | zero | 第一周是 | incorrect |
| FSDD one | one | 我 | incorrect |
| FSDD two | two | two | exact |
| FSDD three | three | 对 | incorrect |
| FSDD four | four | poor | incorrect |
| FSDD five | five | five | exact |

The Chinese sample first emitted text after 1.8 seconds of audio was available;
including the processing time of that chunk, its estimated first partial was
about 1.85 seconds. Short files emitted only when their final partial chunk was
processed, so their estimate is audio duration plus roughly 42-45 ms compute.
This is an offline pacing estimate, not capture-to-UI latency.

## Verified Candidate Identity

- Model: `funasr/paraformer-zh-streaming`, 220M parameters, Chinese and English,
  16 kHz streaming ASR.
- Immutable tag: `apache-2.0-20260804`.
- Resolved commit: `fd2af606b37d7fb8b3b8a218c5be5b07b53ef6ba`.
- Model checksum:
  `4fdfb48ed4471777c9a511e96a2acae17f77cac9d709cc756634622769192a64`.
- Model license at the immutable tag: Apache-2.0.
- Runtime: FunASR 1.3.21 (MIT), Python 3.12.3, CPU-only PyTorch and torchaudio
  2.11.0, four threads, Linux x86-64.

Primary sources:

- <https://github.com/modelscope/FunASR>
- <https://github.com/modelscope/FunASR/releases/tag/v1.3.21>
- <https://huggingface.co/funasr/paraformer-zh-streaming>
- <https://huggingface.co/funasr/paraformer-zh-streaming/tree/apache-2.0-20260804>
- <https://pypi.org/project/funasr/>

## External Audio Set

`voice/benchmarks/asr-external-v1.json` records references, URLs, immutable
revisions, checksums, speakers, tags, keywords, and licenses.

- The Chinese audio is the model repository's official example and is covered
  by the tagged Apache-2.0 statement. Its reference transcript is also present
  in FunASR's pinned validation data.
- The official English audio URL and reference are pinned by FunASR commit
  `bff70399427fe5a06ea5244d1410dd729efe462e`. The audio's redistribution
  license is not stated, so the manifest records `unverified`; the file stays
  local and must not be redistributed.
- Six FSDD recordings come from commit
  `26eb9aaf76e81b692f806f9140c2d2777410d7a1` under CC BY-SA 4.0.

The preparation tool verifies every source, converts it through ffmpeg to
16 kHz mono S16LE, and writes only ignored WAV/index artifacts. No audio,
weights, environment, or raw observations are committed.

## Reproduction

Use a separate Python 3.12 environment. Install a matched CPU-only PyTorch and
torchaudio pair, then FunASR 1.3.21. Run heavyweight steps under the workstation
resource policy or the pre-limited `ubuntu24-r23` container.

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

## Next Evidence

- Record consented Chinese, mixed Chinese/English, filenames, paths, commands,
  URLs, numbers, negation, repetition, and keyboard-noise utterances from the
  intended microphone path.
- Compare an alternative such as SenseVoiceSmall or sherpa-onnx before choosing
  one production ASR.
- Measure actual PipeWire capture-to-partial and PTT-release-to-final latency.
- Verify hot-unplug, default-device switching, provider timeout, cancellation
  during an active model call, and process restart behavior.

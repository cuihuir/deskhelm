# Piper and Kokoro: First Local TTS Benchmark

Date: 2026-08-18

Status: Initial baseline; not a final production selection

## Conclusion

Piper `zh_CN-chaowen-medium` is the stronger low-latency notification baseline.
Kokoro 82M remains the quality-oriented candidate, but this run did not include
human listening and therefore does not establish that it sounds better. Piper
was about 5.5 times faster by mean real-time factor and used about 40% of
Kokoro's peak memory.

| Metric | Piper Chaowen | Kokoro 82M |
|---|---:|---:|
| Successful observations | 36 / 36 | 36 / 36 |
| Cold load | 1.99 s | 3.36 s |
| Synthesis p50 / p95 | 174 / 592 ms | 1,090 / 3,441 ms |
| First provider chunk p50 / p95 | 174 / 592 ms | 1,090 / 3,441 ms |
| Mean output duration | 6.63 s | 6.44 s |
| Mean RTF | 0.034 | 0.185 |
| Mean process CPU | 1.12 s | 4.83 s |
| Whole-process peak RSS | 1.01 GiB | 2.52 GiB |
| Whole benchmark command | 13.09 s | 54.52 s |

First-chunk latency was almost the same as complete synthesis latency because
most corpus items produced one complete provider chunk. This is provider-level
chunking, not PCM-frame streaming.

## Interruption

- Piper yielded one chunk, accepted cancellation between chunks, and stopped
  about 120 ms after the request.
- Kokoro completed the long-text synthesis before the cancellation request
  could interrupt it. Its current adapter can cancel only between yielded
  pipeline results, not during one model inference.
- Piper also cannot cancel inside one ONNX inference. Both providers can cancel
  while waiting for the serialized shared model.

## Intelligibility Proxy

The first generated WAV for every corpus item was converted to 16 kHz mono and
transcribed by the pinned Paraformer baseline. These values are a
model-dependent proxy, not human quality, naturalness, preference, or MOS.

| Candidate | Mean CER | Chinese-only CER | Mixed CER | Keyword accuracy | English WER |
|---|---:|---:|---:|---:|---:|
| Piper | 0.572 | 0.130 | 0.725 | 0.333 | 1.000 |
| Kokoro | 0.470 | 0.148 | 0.694 | 0.313 | 0.950 |

For the four Chinese-only items, Piper recovered every keyword and Kokoro
recovered 93.8%. Both candidates scored zero mixed-command keywords because
this Paraformer baseline is already known to be weak on English tokens,
symbols, paths, and coding commands. The proxy cannot separate TTS errors from
ASR errors and should not drive the final voice choice.

All 24 WAV files contained non-trivial signal and no one-second interval below
-50 dB. Kokoro had no full-scale samples. Piper reached full scale in every
file; its worst file had about 0.001% samples at or near the S16LE limit. This
small clipping signal requires listening and gain review before production use.

## Verified Candidate Identities

### Piper

- Runtime: `piper-tts` 1.7.0, revision
  `7b8e8f7197a480047677715f00d3d78903b55a2a`, GPL-3.0-or-later.
- Voice: `zh_CN-chaowen-medium`, 22,050 Hz, revision
  `f5a6e9094787fd865d65cb024472f977f9c542b5`.
- Voice model SHA-256:
  `820d64ac16048fbcf38dd0823d37fab5f5e0c2bd71b01ca5a50f553fac19e746`.
- The model card identifies the source dataset as CC0. The more common Huayan
  voice was not used because its model card lists the dataset license as
  unknown.
- Chinese G2P assets and the BERT tokenizer are separately pinned and verified
  so synthesis runs offline. The provider uses Piper 1.7.0 internal classes to
  inject those local paths; this is deliberately version-sensitive.

### Kokoro

- Runtime: `kokoro` 0.9.4, revision
  `dfb907a02bba8152ca444717ca5d78747ccb4bec`, Apache-2.0.
- Model: `hexgrad/Kokoro-82M` v1.0, revision
  `f3ff3571791e39611d31c381e3a41a3af07b4987`, Apache-2.0.
- Model SHA-256:
  `496dba118d1a58f5f3db2efc88dbdc216e0483fc89fe6e47ee1f2c53f18ad1e4`.
- Chinese uses `zf_xiaobei`; English-only text uses `af_heart`. Text containing
  CJK characters selects the Chinese pipeline.

Primary sources:

- <https://github.com/OHF-Voice/piper1-gpl>
- <https://pypi.org/project/piper-tts/>
- <https://huggingface.co/rhasspy/piper-voices>
- <https://github.com/hexgrad/kokoro>
- <https://pypi.org/project/kokoro/>
- <https://huggingface.co/hexgrad/Kokoro-82M>

## Reproduction

Use an isolated Python 3.12 environment with CPU-only PyTorch 2.11.0,
`piper-tts==1.7.0`, `kokoro==0.9.4`, `misaki==0.9.4` with Chinese extras, and
`en-core-web-sm==3.8.0`. The spaCy model is installed explicitly because
Kokoro's English frontend otherwise attempts a runtime installation. Run model
work under the workstation resource policy or the pre-limited
`ubuntu24-r23` container.

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
  --audio-directory references/vendor/tts-bench/run-v1/audio/piper \
  --repetitions 3 --cpu-threads 4
```

Use candidate `kokoro-v1-auto-zh-en` and separate output paths for Kokoro. The
preparation tool downloads only pinned HTTPS artifacts, verifies exact sizes
and checksums, and safely bounds G2PW archive extraction. Offline benchmark runs
set `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`.

## Next Evidence

- Run blinded human listening for intelligibility, naturalness, pronunciation,
  clipping, volume, and notification fatigue on Chinese, English, and mixed
  coding text.
- Measure actual provider-to-PipeWire first playback and stop latency.
- Test timeout, hot unplug, default-sink changes, process restart, and repeated
  cancellation under the application composition boundary.
- Review Piper GPL packaging implications together with the repository's root
  license decision before distributing a bundled runtime.

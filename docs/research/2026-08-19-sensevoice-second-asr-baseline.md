# SenseVoice Second ASR Baseline

Date: 2026-08-19

## Outcome

SenseVoiceSmall through `sherpa-onnx` is the second local ASR baseline. It is a
useful low-memory, low-latency Chinese/multilingual comparison, but it is not a
production selection. Its current adapter is final-only, short isolated English
digits are weak, and the custom FunASR model license needs review.

## Candidate Decision

| Criterion | SenseVoiceSmall + sherpa-onnx | multilingual whisper.cpp |
| --- | --- | --- |
| Target languages | Mandarin, Cantonese, English, Japanese, Korean | Broad multilingual Whisper models |
| Runtime shape | Python wheel over native ONNX CPU runtime | Native C/C++ runtime and CLI/library |
| Selected model cost | 228 MiB INT8 ONNX file | Varies; multilingual base/small are larger choices |
| Streaming claim | Offline; simulated streaming uses VAD segmentation | Windowed/stream examples; standard decoding is chunk/window based |
| License | Apache-2.0 runtime; custom FunASR Model License 1.1 weights | MIT runtime and OpenAI weights |
| Decision | Selected for the second measured baseline | Retained as the next fallback |

SenseVoice was selected because this phase needs a Chinese-focused contrast to
Paraformer with substantially lower runtime and memory cost. Whisper remains
important if SenseVoice's license or command accuracy blocks production use.

## Reproducible Identity

- Runtime: `sherpa-onnx` 1.13.6, tag commit
  `1cb484af5e69d3c7803c1eb0b3b5ab8041e0e911`, Apache-2.0.
- Release asset: GitHub asset ID `288366523`, created 2025-09-01,
  `sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2`.
- Archive size: 163,002,883 bytes.
- Archive SHA-256:
  `7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e`.
- INT8 model size: 239,233,841 bytes; SHA-256
  `c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51`.
- Tokens SHA-256:
  `f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc`.

The release asset provides a GitHub digest, which matched the local download.
The extracted license file delegates to the current FunASR model license.

## Provider Boundary

The DeskHelm adapter accepts only 16 kHz mono S16LE PCM, limits audio duration
to 120 seconds and text to 4,096 characters, loads the recognizer lazily, and
serializes inference. It checks cancellation while waiting for the recognizer,
before decoding, and after decoding. The native offline decode call cannot be
cancelled midway.

`first_partial_latency_ms` is always unavailable. VAD remains an independent
advisory measurement and never trims, gates, or changes the recording passed to
SenseVoice.

## First Public Run

The existing eight-file external ASR set was run three times on Linux x86-64,
Python 3.12.3, CPU with four threads. All 24 observations completed.

| Metric | SenseVoice | Paraformer baseline |
| --- | ---: | ---: |
| Model load | 681.488 ms | 4,966.389 ms |
| Process peak RSS | 412.512 MiB | 3,088.902 MiB |
| Mean CPU time/sample | 238.445 ms | 454.532 ms |
| Mean real-time factor | 0.055813 | 0.120607 |
| Final latency p50 | 21.726 ms | 44.744 ms |
| Final latency p95 | 122.086 ms | 467.359 ms |
| Mean CER | 0.719720 | 0.437500 |
| Mean keyword accuracy | 0.250000 | 0.437500 |

The aggregate accuracy is dominated by six very short isolated English digit
clips. SenseVoice missed five and partially recognized one. On the two longer
official examples it reached 0.052632 Chinese CER and 0.038462 English CER,
with full keyword accuracy on both. Paraformer reached zero CER on both longer
examples, but only half keyword accuracy on the English sample due to its word
boundary behavior.

This set proves runtime viability, not product accuracy. It is too small and
too skewed to select either recognizer for mixed coding commands.

## Licensing Risk

The current SenseVoice repository code is MIT, while the selected model card
and archive delegate model weights to FunASR Model Open Source License Agreement
1.1. That agreement permits use, copying, modification, and sharing with
attribution, but also contains conduct-based termination and automatic future
revision language. Do not package or select this model as the default until the
repository license and model-distribution review are complete.

## Live Comparison

A synchronized microphone run used the same immediate-capture 12-second public
phrase as the Paraformer baseline. The user confirmed after the run that they
spoke the complete phrase during that exact capture. No PCM or recognized text
was saved or printed.

| Metric | SenseVoice | Paraformer confirmed baseline |
| --- | ---: | ---: |
| Duration | 12,030.312 ms | 12,030.312 ms |
| Transcript characters | 19 | 18 |
| Keyword accuracy | 1.000000 | 1.000000 |
| CER | 0.136364 | 0.181818 |
| Cold-process final ASR | 1,328.308 ms | 8,008.233 ms |
| First partial | unavailable | 4,850.187 ms |
| Peak / RMS | 0.696960 / 0.086324 | 0.779144 / 0.091119 |
| Clipped fraction | 0 | 0 |

WebRTC reported two speech segments totaling 4,700 ms for SenseVoice's capture.
That advisory value is not used to establish participation or to gate ASR. The
post-run user confirmation establishes that this result is a spoken comparison.

SenseVoice is more accurate and much faster on this one phrase, with about
6x lower cold-process final latency. This remains one speaker and one command;
it does not outweigh the short-English failure, final-only behavior, custom
model license, or missing recovery evidence.

## Primary Sources

- <https://k2-fsa.github.io/sherpa/onnx/sense-voice/index.html>
- <https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html>
- <https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models>
- <https://github.com/k2-fsa/sherpa-onnx/blob/master/LICENSE>
- <https://huggingface.co/FunAudioLLM/SenseVoiceSmall>
- <https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE>
- <https://github.com/ggml-org/whisper.cpp>
- <https://github.com/openai/whisper>

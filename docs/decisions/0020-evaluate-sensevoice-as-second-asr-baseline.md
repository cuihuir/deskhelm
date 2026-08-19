# ADR 0020: Evaluate SenseVoice as the Second ASR Baseline

- Status: Accepted
- Date: 2026-08-19

## Context

Paraformer is a real Chinese streaming baseline, but its small English/code
sample and the first controlled live Chinese command do not justify selecting
it as DeskHelm's production default. A second local candidate must fit the same
privacy-safe diagnostic contract while reducing runtime cost and improving
Chinese and mixed-language evidence.

The shortlist was multilingual Whisper through `whisper.cpp`, and
SenseVoiceSmall through `sherpa-onnx`. Both have CPU runtimes outside Bridge.
Whisper has a permissive MIT code/model license and broad multilingual support,
but the standard model path is windowed/offline and the useful multilingual
models are a larger comparison target. SenseVoice is optimized for Mandarin,
Cantonese, English, Japanese, and Korean and has a compact INT8 ONNX artifact.

## Decision

Use SenseVoiceSmall through `sherpa-onnx` as the second ASR baseline, not as the
production default. Pin:

- `sherpa-onnx` 1.13.6, Apache-2.0, tag commit
  `1cb484af5e69d3c7803c1eb0b3b5ab8041e0e911`;
- release asset ID `288366523`,
  `sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2`, 163,002,883
  bytes, SHA-256
  `7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e`;
- `model.int8.onnx` and `tokens.txt` from that verified archive.

Run 16 kHz mono S16LE input on CPU with four threads, automatic language
selection, inverse text normalization, greedy decoding, a 120-second default
input limit, a 4,096-character output limit, and serialized access to one lazy
recognizer. Cancellation is checked before lock acquisition, before decoding,
and after decoding. The native offline decode call itself is not interruptible.

SenseVoice is final-only in this adapter. sherpa-onnx's “simulated streaming”
example applies VAD and then offline recognition; DeskHelm will not report that
as a provider partial or allow VAD to gate the controlled comparison.

Keep the wheel, model, prepared audio, and observations under ignored external
storage. The controlled diagnostic continues to suppress runtime output, save
no PCM, print no recognized text, and require post-run confirmation.

## Consequences

- DeskHelm can compare a compact ONNX Chinese/multilingual recognizer against
  the PyTorch Paraformer baseline under one diagnostic contract.
- SenseVoice cannot satisfy partial-transcript or mid-inference cancellation
  requirements without a different provider or process-isolation design.
- The model archive delegates to the FunASR Model Open Source License Agreement
  1.1. Its attribution requirement, conduct-based termination clause, and
  automatic future revisions require legal/product review before distribution
  or production selection. This is weaker licensing certainty than Whisper's
  MIT model terms.
- `whisper.cpp` remains the next fallback comparison if SenseVoice quality or
  licensing is unacceptable.

## Implementation Status

The lazy bounded provider, shared controlled-diagnostic selection, fake-runtime
tests, pinned optional runtime, and local verified model artifact are included
in this phase. Public and synchronized live measurements are recorded in the
dated research report.

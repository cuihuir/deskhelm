# ADR 0014: Use Paraformer as the Initial Streaming ASR Baseline

- Status: Accepted
- Date: 2026-08-18

## Context

DeskHelm needs a local streaming ASR path for PTT partial results and final
transcripts. The first candidate must be tested without adding PyTorch, model
weights, or provider-specific dependencies to Bridge or the core project
requirements. Model licensing also needs an immutable, auditable revision.

## Decision

Use `funasr/paraformer-zh-streaming` as the first streaming ASR baseline, pinned
to Hugging Face tag `apache-2.0-20260804` and resolved commit
`fd2af606b37d7fb8b3b8a218c5be5b07b53ef6ba`. Verify `model.pt` against SHA-256
`4fdfb48ed4471777c9a511e96a2acae17f77cac9d709cc756634622769192a64`.
The tagged model files are Apache-2.0; FunASR 1.3.21 is MIT licensed.

Use the official streaming configuration:

- 16 kHz mono S16LE input;
- chunk size `[0, 10, 5]`, where the central 10 units produce a 600 ms stride;
- encoder look-back 4 and decoder look-back 1;
- one cache dictionary per transcription;
- CPU execution with four PyTorch threads for the first comparison.

The provider remains lazy, serializes access to the shared model, bounds input
duration to 120 seconds by default, checks cancellation between chunks, and
limits final transcript text to 4,096 characters. It exposes the existing batch
`transcribe()` method and a measured streaming method. The benchmark's first
partial estimate equals the amount of audio that must be available at the first
non-empty model increment plus that chunk's offline processing time. It is not
a live PipeWire measurement.

Keep the Python 3.12 model environment, downloaded weights, prepared audio, and
raw observations under ignored storage. Use a matched CPU-only PyTorch /
torchaudio 2.11.0 pair. The external run set contains one Apache-2.0 official
Chinese sample, one official English sample whose audio license remains
unverified, and six CC BY-SA 4.0 FSDD digit samples. Do not redistribute the
unverified English audio.

## Consequences

- DeskHelm now has a real local streaming ASR adapter and reproducible baseline.
- The first run supports continuing Paraformer evaluation for Chinese speech.
- Short English digits and English word segmentation are not strong enough to
  select Paraformer as the only production ASR for mixed coding commands.
- Model loading, about 3 GiB process memory, cancellation during one inference
  call, live microphone latency, and recovery remain application-level risks.
- A broader recorded Chinese/mixed/code corpus and at least one alternative ASR
  candidate are required before choosing the production default.

## Implementation Status

The provider, measured benchmark path, manifest, preparation/runner tools,
tests, and first three-repetition external run are implemented. Aggregate and
per-sample findings are recorded in the dated Paraformer research report.

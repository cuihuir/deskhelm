# ADR 0010: Use a Versioned Provider-Neutral Voice Benchmark Contract

- Status: Accepted
- Date: 2026-08-18

## Context

DeskHelm must compare local ASR, VAD, TTS, and audio-provider candidates before
choosing heavyweight runtimes or model licenses. Ad hoc timing notes are not
enough: results need stable inputs, exact provider/model identity, resource
bounds, comparable accuracy metrics, and explicit licensing evidence.

Benchmark code must remain independent of any particular model stack. It must
also avoid turning private microphone captures, generated speech, provider
exceptions, or machine identifiers into repository artifacts.

## Decision

Use a versioned synthetic utterance corpus and UTF-8 NDJSON observations.
Version 1 has these rules:

- corpus utterance IDs and reference text are immutable within one version;
- Chinese, English, mixed language, paths, commands, URLs, symbols, numbers,
  negation, repetition, and long recovery instructions are represented;
- each observation names its run, provider and model versions, provider and
  model licenses, anonymous system profile, device, utterance, and repetition;
- ASR observations record final latency, optional first-partial latency, audio
  duration, process CPU time, optional peak RSS/VRAM, status, and transcript;
- TTS observations record batch synthesis latency, process CPU time, optional
  peak RSS/VRAM, output byte count, and status, but not generated audio;
- provider failures use fixed error codes and never serialize exception text;
- records are limited to 1 MiB, files to 64 MiB, and runs to 10,000
  observations.

Score ASR with Unicode NFKC/case-folded, whitespace-insensitive CER; compute WER
only for English-labeled utterances; and score required keywords separately so
paths, symbols, version numbers, negation, and project names remain visible.
Report p50/p95 latency, CPU time, real-time factor, and available memory peaks.

Keep raw recordings and local result files outside Git by default. A future
audio corpus may enter version control only with speaker consent, capture
metadata, checksums, and clear redistribution terms.

## Consequences

- Fake and production providers can use the same dependency-free runner and
  result format.
- Provider and model licensing uncertainty is recorded as data rather than
  omitted from comparisons.
- Batch TTS can be compared now; first-audio and interruption latency require a
  later streaming provider contract.
- Recovery scenarios and VAD segmentation will extend this contract in a new
  compatible version or separate observation type rather than changing v1
  records silently.

## Implementation Status

The v1 corpus, bounded observation models, fake-provider runners, CER/WER and
keyword scoring, latency/resource summaries, NDJSON CLI, documentation, and
tests are implemented.

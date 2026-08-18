# ADR 0015: Use Piper as the Initial Notification TTS Baseline

Date: 2026-08-18

Status: Accepted

## Context

DeskHelm needs short, interruptible local speech notifications without adding
model or runtime dependencies to Bridge. Piper and Kokoro are both practical
CPU candidates, but they differ materially in latency, memory use, perceived
quality potential, streaming behavior, and licensing.

The first pinned local comparison used the 12-item DeskHelm Chinese, English,
and mixed-language text corpus with three repetitions per candidate on four CPU
threads. It also recorded provider/model identity, checksums, cold load,
first-provider-chunk latency, output duration, real-time factor, process peak
RSS, and a cancellation probe.

## Decision

Use Piper `zh_CN-chaowen-medium` as the initial low-latency notification TTS
baseline for further Voice Gateway integration experiments. Keep Kokoro 82M as
the quality-oriented comparison candidate. Do not select either as the final
production TTS until human listening, live playback, recovery, and packaging
license review are complete.

- Keep both providers optional, lazy, CPU-bound, and outside Bridge.
- Pin runtime and model revisions, artifact sizes, SHA-256 checksums, and
  licenses in the TTS candidate manifest.
- Keep generated speech, weights, environments, and local results ignored.
- Treat first audio as the first complete chunk yielded by the provider. The
  current providers do not demonstrate PCM-frame streaming within one model
  inference.
- Allow cancellation while waiting for shared inference and between provider
  chunks. Do not claim cancellation during a single Piper or Kokoro model call.
- Do not bundle or distribute Piper until the GPL-3.0-or-later runtime and the
  repository's still-unselected root license have received packaging review.

## Evidence

Piper completed 36/36 observations with mean RTF 0.034, first-chunk p50/p95
174/592 ms, and 1.01 GiB peak RSS. Kokoro completed 36/36 with mean RTF 0.185,
first-chunk p50/p95 1,090/3,441 ms, and 2.52 GiB peak RSS. Piper was about 5.5
times faster by mean RTF and used about 40% of Kokoro's peak memory.

Piper cancelled between chunks about 120 ms after the cancellation request in
the probe. Kokoro completed its first long-text chunk before cancellation could
be applied, so meaningful mid-inference interruption remains unverified.

A Paraformer transcription proxy favored Kokoro on aggregate CER but showed
both candidates strong on the four Chinese-only items and weak on mixed coding
commands. This is model-dependent intelligibility evidence, not human listening
or MOS. Piper output also reached full-scale samples with a very small clipped
fraction; gain and perceived artifacts require listening review.

## Consequences

- The next integration may start with Piper to minimize notification latency
  and CPU/memory pressure.
- Kokoro remains available when naturalness matters more than latency, but its
  first-chunk and interruption behavior need improvement or acceptance.
- TTS selection remains reversible at the provider boundary.
- A final product default requires consented listening tests, actual PipeWire
  playback measurements, hot-unplug/recovery tests, and a distribution plan.

## Alternatives

- Select Kokoro now: rejected because the measured latency and memory cost are
  high for short interactive notifications and quality has not been rated by
  listeners.
- Select Piper as the final default: rejected because the runtime license,
  clipping observation, and perceived quality are unresolved.
- Defer all implementation: rejected because the bounded providers and
  benchmark produce useful integration evidence without committing the product
  to either runtime.

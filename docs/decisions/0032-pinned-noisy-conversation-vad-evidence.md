# ADR 0032: Broaden VAD Evidence With Pinned Conversation and Noise

- Status: Accepted
- Date: 2026-08-26

## Context

The first VAD benchmark uses short, trimmed English digits surrounded by
generated silence. It validates the streaming and observation boundaries, but
it does not exercise conversational turn-taking, overlapping speakers,
continuous environmental noise, low input level, or candidate thresholds.
The live Gateway path is advisory, yet its PipeWire-to-VAD software timing also
lacks an explicit privacy-safe diagnostic.

This evidence gap is too large to select a production VAD or grant one control
over PTT capture.

## Decision

Add a version 2 VAD manifest while preserving version 1 compatibility.

- A speech source may select one bounded clip and carry non-overlapping
  millisecond reference intervals derived from a pinned public annotation.
- A scenario may mix one pinned environmental source across the complete
  sample at a requested signal-to-noise ratio. Preparation computes speech RMS
  only inside reference intervals, repeats the noise deterministically, and
  applies common headroom scaling if required.
- Downloads remain individually bounded to 4 MiB. Prepared PCM remains bounded
  by the existing 64 MiB sample limit and stays under ignored storage.
- Use the AMI `ES2008a` headset mix from 216.000 through 234.000 seconds as the
  first actual meeting-conversation clip. Its five unioned speech intervals are
  derived from the AMI manual segment annotations version 1.6.2. AMI audio and
  annotations are CC BY 4.0 and are pinned by SHA-256.
- Use the ESC-10 rain and clock-tick recordings as the first real noise
  sources. They are pinned to ESC-50 commit
  `33c8ce9eb2cf0b1c2f8bcf322eb349b6be34dbb6`, covered by the ESC-10 CC BY 3.0
  subset license, and pinned by SHA-256.
- Prepare clean, 10 dB SNR, and 0 dB SNR scenarios. Do not commit downloaded or
  prepared audio.

Add bounded threshold sweeps to the existing runner:

- WebRTC modes 0, 1, 2, and 3 retain the existing 20 ms and hysteresis
  configuration.
- Silero uses threshold pairs `0.35/0.20`, `0.50/0.35`, and `0.65/0.50` with
  the existing 100 ms minimum silence and 30 ms padding.
- Each profile has a separate benchmark identity and summary. A sweep may not
  exceed the existing 10,000-observation bound and never selects a winner
  automatically.

Add an explicit `--live-audio` VAD diagnostic that opens one bounded PipeWire
capture and records only:

- first PCM chunk handoff latency;
- chunk inter-arrival and VAD processing percentiles;
- audio duration, byte and chunk counts;
- aggregate peak, RMS, clipping, and near-silence fractions plus a provisional
  input-level hint;
- bounded frame-positioned speech events and derived active duration.

The diagnostic retains PCM only in memory and never prints or saves audio,
transcripts, provider output, wall-clock timestamps, or acoustic-onset claims.
Signal metrics are derived incrementally without retaining complete PCM. Its
timing describes PipeWire process handoff and software processing only.

## Consequences

- Candidate behavior can be compared on actual meeting speech, low input
  level, real continuous noise, and fixed parameter sweeps.
- AMI segment boundaries are human transcript segments, not sample-accurate
  acoustic onset labels. Results must be interpreted as comparative evidence.
- The selected AMI RealMedia mix is compact and reproducible but lower fidelity
  than the full WAV mix; low-level behavior is part of this test condition.
- ESC-10 and AMI attribution must remain documented if prepared audio is ever
  redistributed. DeskHelm continues to keep all audio outside Git.
- Live timing does not measure microphone acoustic latency and does not justify
  endpointing.
- Aggregate signal metrics can distinguish low/clipped input from a likely
  profile mismatch, but the input-level hint is provisional and must never
  change system gain automatically.
- VAD remains optional, disabled by default, advisory, and unable to trim
  capture, stop PTT, suppress final ASR, or publish partial transcripts.

## Alternatives

- Use generated white noise only: rejected because it would not add real
  environmental structure.
- Download the 31 MiB AMI WAV mix: rejected for this bounded first expansion;
  the official approximately 2 MiB RealMedia mix contains the same meeting
  timeline and converts through the existing `ffmpeg` boundary.
- Tune the live Gateway configuration directly: rejected because offline and
  explicit diagnostic evidence must precede a product default change.

## Implementation Status

Manifest v2 parsing, annotated clip preparation, deterministic SNR mixing,
WebRTC and Silero sweeps, the explicit live diagnostic, regression tests, and
the first local runs are implemented. The diagnostic now derives bounded signal
level metadata after confirmed spoken misses. No production VAD is selected.

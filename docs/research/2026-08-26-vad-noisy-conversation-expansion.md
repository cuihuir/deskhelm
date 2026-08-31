# VAD Noisy Conversation Expansion

Date: 2026-08-26

Status: Reproducible second evidence set; no production selection

## Conclusion

The second VAD set materially changes the ranking pressure. On an actual AMI
meeting clip with quiet input and real rain or clock-tick backgrounds, WebRTC
mode 0 produced the best aggregate F1 (`0.849`). The existing live mode 2 was
more precise but lost substantially more speech under 0 dB rain. Silero was
highly precise but missed most labeled speech at all three tested thresholds.

This is sufficient to reject a threshold-free default decision. It is not
sufficient to replace live mode 2 because the corpus contains one meeting clip,
AMI segment labels are not acoustic-onset annotations, and the live spoken
threshold comparison is still pending.

| Profile | Precision | Recall | F1 | Replay p50/p95 | Mean RTF |
|---|---:|---:|---:|---:|---:|
| WebRTC mode 0 | 0.797 | 0.908 | 0.849 | 2.92 / 3.03 ms | 0.000154 |
| WebRTC mode 1 | 0.801 | 0.855 | 0.827 | 2.90 / 3.07 ms | 0.000153 |
| WebRTC mode 2 | 0.816 | 0.799 | 0.807 | 2.87 / 3.12 ms | 0.000154 |
| WebRTC mode 3 | 0.826 | 0.632 | 0.716 | 2.90 / 3.09 ms | 0.000153 |
| Silero 0.35 / 0.20 | 0.936 | 0.236 | 0.377 | 42.15 / 45.32 ms | 0.002228 |
| Silero 0.50 / 0.35 | 0.950 | 0.175 | 0.295 | 41.09 / 51.24 ms | 0.002230 |
| Silero 0.65 / 0.50 | 0.963 | 0.145 | 0.252 | 40.07 / 42.34 ms | 0.002135 |

Each profile completed 15/15 observations: five scenarios and three
repetitions. No provider failure occurred.

## Public Sources and Provenance

The speech source is the official AMI `ES2008a.Mix-Headset.rm` meeting mix.
The selected 18-second interval begins at 216.000 seconds and contains
multi-speaker turn-taking plus overlapping A/C speech. Five unioned reference
regions come from the four per-speaker `ES2008a.*.segments.xml` files in AMI
manual annotations version 1.6.2.

- AMI download and release information:
  <https://groups.inf.ed.ac.uk/ami/download/>
- AMI license: <https://groups.inf.ed.ac.uk/ami/corpus/license.shtml>
- Audio SHA-256:
  `f2d511399891c6693910b5601d9e0017c88b5fd9ae6c11ee7e5c0f664b547f80`
- Annotation ZIP SHA-256:
  `b56e5babb2496b8795deeeda7e71178d7fbc9963f94276cf2a3f4b56ebbc9f9d`
- License: CC BY 4.0

The noise sources are ESC-10 rain `1-17367-A-10.wav` and clock tick
`1-21934-A-38.wav`, pinned to ESC-50 commit
`33c8ce9eb2cf0b1c2f8bcf322eb349b6be34dbb6`.

- Official repository: <https://github.com/karolpiczak/ESC-50>
- Repository license:
  <https://raw.githubusercontent.com/karolpiczak/ESC-50/33c8ce9eb2cf0b1c2f8bcf322eb349b6be34dbb6/LICENSE>
- ESC-10 subset license: CC BY 3.0

Exact URLs, checksums, clip bounds, annotation identity, and reference intervals
are committed in `voice/benchmarks/vad-conversation-noise-v2.json`.

## Preparation

The preparation tool downloads each source under the existing 4 MiB per-file
limit, verifies SHA-256, converts through `ffmpeg` to 16 kHz mono S16LE, and
creates five 18.9-second scenarios:

- clean AMI meeting speech;
- rain at 10 dB and 0 dB SNR;
- clock tick at 10 dB and 0 dB SNR.

Noise RMS is matched against speech RMS inside only the labeled intervals.
Prepared samples required no common attenuation (`mix_scale=1.0`) and had no
mixing overflow. The clean sample measured approximately `-37.0 dB` mean and
`-14.9 dB` peak; 0 dB mixtures measured approximately `-33.4 dB` mean with
peaks below `-13 dB`.

The clean source is deliberately quiet. That is useful low-input evidence, but
it also explains why a high-precision detector can have poor recall. A later
set should add a controlled normalized-gain variant rather than silently
normalizing this source.

## Candidate Observations

WebRTC mode 2 retained the strongest clean-sample F1 (`0.889`), while mode 0
was more robust after noise was introduced. At 0 dB rain, mode 0 recorded F1
`0.825`; mode 2 fell to `0.696`. Mode 3 consistently traded false positives for
large false-negative duration.

Silero's widest tested pair (`0.35/0.20`) reached clean F1 `0.704`, but its
noisy-scenario F1 ranged from `0.209` to `0.298`. Lower thresholds or gain
normalization may improve recall, but the current result does not justify
additional live complexity or a default change.

## Live PipeWire Negative Control

The explicit diagnostic opened the current default built-in source for three
seconds with WebRTC mode 2. The user was not asked to speak, so this is an
ambient negative control rather than speech-quality evidence.

| Metric | Result |
|---|---:|
| Captured audio | 2,963.625 ms / 94,836 bytes |
| First PipeWire chunk | 181.362 ms |
| Chunk interval p50 / p95 | 128.024 / 128.183 ms |
| VAD processing p50 / p95 | 0.107 / 0.138 ms |
| VAD total / RTF | 2.190 ms / 0.000739 |
| Speech events | 0 |

PCM was processed in memory and discarded. No audio, transcript, provider
output, or wall-clock timestamp was persisted. These numbers describe process
handoff and software timing, not acoustic onset latency.

## Confirmed Live Spoken Comparison

The user separately confirmed fully speaking the fixed public
`zh-repeat-01` phrase during both six-second captures. Both runs used the same
current default built-in source and the exact readiness handshake. DeskHelm did
not request or apply a gain or device-setting change between runs.

| Metric | WebRTC mode 0 | WebRTC mode 2 |
|---|---:|---:|
| Captured audio | 5,950.312 ms | 5,971.625 ms |
| First PipeWire chunk | 180.146 ms | 178.516 ms |
| Chunk interval p50 / p95 | 127.994 / 128.145 ms | 128.008 / 128.155 ms |
| VAD processing p50 / p95 | 0.041 / 0.048 ms | 0.045 / 0.050 ms |
| Speech segments | 1 | 0 |
| Detected speech | 140 ms | 0 ms |

Mode 0 was marginally less severe, but both profiles failed to represent the
confirmed spoken phrase. This rejects either live profile as acceptable under
the current source condition and demonstrates that the offline aggregate
ranking cannot select the live default.

These first two VAD-only runs predate aggregate peak/RMS signal output, so they
cannot distinguish a low or clipped input from a detector/profile mismatch.
The diagnostic now derives peak, RMS, clipping, and near-silence fractions
incrementally without retaining complete PCM. The next controlled run uses the
same readiness gate to collect that privacy-safe signal evidence before tuning
thresholds or changing the live mode. PCM was discarded after both earlier
runs.

## Input-Level Root Cause and USB Control

A closer-microphone repeat did not improve the built-in source: the user again
confirmed fully speaking both phrases, while mode 0 detected only 100 ms and
mode 2 detected none. The enhanced mode 0 run then measured the actual input
condition:

| Built-in source metric | Result |
|---|---:|
| Peak fraction | 0.001831 |
| RMS fraction | 0.000114 |
| Near-silence fraction | 0.996492 |
| Clipped fraction | 0 |
| Provisional level hint | `too_quiet` |
| Detected speech | 140 ms |

Read-only PipeWire inspection identified the cause. The current default
built-in source was not muted, but its volume was only 7% (`-70.40 dB`) on the
active internal-microphone port. The connected PCM2902 USB source was available
at 93% (`-1.98 dB`). DeskHelm did not change either source volume.

The user then confirmed fully speaking the same phrase near the explicitly
selected USB source with WebRTC mode 2:

| USB source metric | Result |
|---|---:|
| Peak fraction | 0.328552 |
| RMS fraction | 0.052386 |
| Near-silence fraction | 0.013756 |
| Clipped fraction | 0 |
| Provisional level hint | `within_diagnostic_range` |
| Speech segments / active duration | 3 / 2,800 ms |
| First PipeWire chunk | 163.266 ms |
| VAD processing p50 / p95 | 0.052 / 0.137 ms |

This is a usable live mode 2 result under a healthy input condition. It shows
that the earlier failures were dominated by the default source level rather
than WebRTC mode selection. It does not select a production VAD from one phrase
or authorize automatic gain changes. The local Alpha should use an explicit
healthy source or require the operator to correct the built-in input level.

## Reproduction

All downloaded, prepared, model, observation, and summary files remain under
ignored storage.

```bash
PYTHONPATH=voice uv run --project voice/runtime --locked --no-sync python \
  tools/prepare-vad-benchmark.py \
  --manifest voice/benchmarks/vad-conversation-noise-v2.json \
  --artifact-root references/vendor/vad-bench/run-v2

PYTHONPATH=voice uv run --project voice/runtime --locked --no-sync python \
  tools/run-vad-benchmark.py --provider webrtc --sweep \
  --manifest voice/benchmarks/vad-conversation-noise-v2.json \
  --prepared references/vendor/vad-bench/run-v2/prepared \
  --observations voice/benchmarks/results/webrtc-v2-sweep.ndjson \
  --summary voice/benchmarks/results/webrtc-v2-sweep-summary.json

PYTHONPATH=voice uv run --project voice/runtime --locked --no-sync python \
  tools/run-live-vad-diagnostic.py --live-audio --seconds 3 \
  --provider webrtc --webrtc-mode 2 --await-phrase-ready
```

The readiness mode prints the fixed public `zh-repeat-01` phrase and waits up
to 60 seconds for one exact `ready` line before capture. Interpret the result
only after the user separately confirms whether they spoke during that run.
Current output also includes aggregate signal fractions and a provisional level
hint; it never changes system gain.

Run Silero with the same manifest and prepared set plus the explicitly pinned
ignored ONNX model path.

## Remaining Evidence

- Repeat the public phrase through the source intended for Alpha operation and
  retain explicit stable-name selection while the built-in source remains at
  an unusably low level.
- Add a controlled normalized-gain AMI variant and at least one Chinese
  conversational source before selecting a production default.
- Test VAD runtime failure, PipeWire disconnect, manual-source disappearance,
  and reuse through the explicit diagnostic without granting endpoint control.
- Keep PTT release authoritative and final ASR independent of all VAD outcomes.

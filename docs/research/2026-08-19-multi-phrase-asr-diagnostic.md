# Multi-Phrase ASR Diagnostic

Date: 2026-08-19

## Scope

The batch diagnostic extends the existing public corpus workflow to repeated
Chinese and Chinese/English coding commands. The planned comparison set is:

| ID | Risk exercised |
| --- | --- |
| `zh-negation-01` | safety negation and migration terminology |
| `mixed-command-01` | shell command, environment variable, and flags |
| `mixed-path-01` | repository path and symbol name |
| `mixed-number-01` | numeric values, queue capacity, and negation |

Each provider receives the same ordered phrase IDs in a separate user-spoken
batch. The recordings are not paired audio and must not be treated as such.

## Privacy and Bounds

- Maximum eight phrase IDs per run; duplicate IDs are rejected.
- Every phrase starts a fresh bounded capture session.
- PCM remains in memory only and is discarded after signal/VAD/ASR metrics.
- Recognized text is used for CER and keyword scoring but never printed or saved.
- Each result and the batch parent require post-run user confirmation.
- VAD remains advisory and cannot gate, trim, or endpoint ASR.

## Results

The deterministic batch path and privacy tests pass. A user-confirmed
SenseVoice batch completed all four captures, but the user reported that the
spoken pace did not fully keep up with the phrase transitions. The measurements
are therefore valid as a confirmed live diagnostic, not as a tightly
synchronized provider comparison.

### SenseVoice Batch

Runtime: pinned local Python 3.12 environment, CPU-only, four threads. Source:
the current default USB mono microphone. Each capture used 16 kHz mono S16LE,
an approximately 12-second bound, and advisory WebRTC VAD. No PCM or
recognized text was saved or printed.

| ID | Duration (ms) | Active (ms) | Chars | CER | Keyword accuracy | Final ASR (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `zh-negation-01` | 12008.938 | 3048.938 | 14 | 0.428571 | 0.666667 | 1101.448 |
| `mixed-command-01` | 12030.312 | 1350.312 | 4 | 0.927273 | 0.000000 | 221.098 |
| `mixed-path-01` | 12040.938 | 6120.000 | 43 | 0.812500 | 0.000000 | 225.809 |
| `mixed-number-01` | 12008.938 | 6748.938 | 34 | 1.071429 | 0.000000 | 222.124 |

All four records had `status: ok`, no clipped samples, and a provisional
`within_diagnostic_range` input hint. The result is retained with the
per-phrase confirmation scope, while pace/synchronization remains a confounder
for literal accuracy and keyword misses.

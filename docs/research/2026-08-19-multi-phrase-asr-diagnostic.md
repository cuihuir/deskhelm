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

The deterministic batch path and privacy tests pass. User feedback confirms
that the phrases were spoken in both provider runs, but the prompt transitions
were faster than comfortable natural speech. The measurements are therefore
valid as confirmed live diagnostics, not as tightly synchronized provider
comparisons. The current report treats all four Paraformer phrases as spoken,
per the user's instruction to begin analysis, while retaining the pacing
confounder.

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

### Paraformer Batch

The second provider used the same source, capture bound, phrase order, VAD
mode, and CPU thread count. It also completed all four captures with `ok`
status, no clipped samples, and the same provisional input-level hint.

| ID | Duration (ms) | Active (ms) | Chars | CER | Keyword accuracy | First partial (ms) | Final ASR (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `zh-negation-01` | 12008.938 | 4280.000 | 20 | 0.190476 | 0.666667 | 6048.826 | 5852.653 |
| `mixed-command-01` | 12008.938 | 5108.938 | 15 | 0.818182 | 0.000000 | 7247.261 | 918.138 |
| `mixed-path-01` | 12030.312 | 3930.312 | 17 | 0.895833 | 0.000000 | 1851.867 | 891.291 |
| `mixed-number-01` | 12030.312 | 6800.000 | 28 | 1.000000 | 0.000000 | 1250.730 | 971.636 |

### Comparison

| Metric | SenseVoice | Paraformer |
| --- | ---: | ---: |
| Mean CER across four phrases | 0.809943 | 0.726123 |
| Mean keyword accuracy | 0.166667 | 0.166667 |
| Mean final ASR latency | 442.620 ms | 2,158.430 ms |
| Mean warm-path final latency (phrases 2-4) | 223.010 ms | 927.022 ms |
| Mean advisory speech-active fraction | 0.359097 | 0.418442 |

Observed facts:

- Both providers completed all four bounded captures and preserved the
  privacy contract.
- Paraformer produced the lower mean CER, driven mainly by the negation phrase,
  but neither provider achieved an exact match and both had zero keyword
  accuracy on the three mixed coding phrases.
- SenseVoice was substantially faster after the first phrase and had the
  lower cold-process final latency in this run.
- Both providers reported full-range signal hints without clipping; the signal
  metrics do not explain the mixed-command failures by themselves.

Interpretation limits:

- The two runs contain different human recordings and prompt-transition timing;
  they are not paired-audio experiments.
- The mixed-command results are not sufficient to select a production ASR for
  code-sensitive commands. No provider is selected by this batch.
- A cleaner follow-up should use one explicit readiness handshake per phrase,
  let the user finish before starting the next capture, and retain the same
  phrase order only for coverage rather than statistical pairing.

## Follow-Up Synchronization Mode

ADR 0023 adds an opt-in `--await-phrase-ready` mode to the diagnostic. It
prints one phrase, waits for an exact bounded `ready` line on stdin, and only
then opens that phrase's capture session. Immediate capture remains available
for unattended or direct-terminal use; the two modes are labeled separately in
the privacy-safe output. A live handshake-mode rerun is still pending.

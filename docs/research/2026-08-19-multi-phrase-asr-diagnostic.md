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

Live measurements are pending explicit user readiness and post-run confirmation.
The deterministic batch path and privacy tests pass; no live quality conclusion
is drawn until all four phrases are confirmed per provider.

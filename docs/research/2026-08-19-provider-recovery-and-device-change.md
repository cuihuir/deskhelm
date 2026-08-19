# Provider Recovery and Device Change Evidence

Date: 2026-08-19

## Deterministic Recovery Matrix

| Boundary | Expected behavior | Evidence |
| --- | --- | --- |
| Paraformer model call fails | Lock is released; next request can reuse provider | `test_paraformer_recovers_after_failure_and_cancels_after_chunk` |
| Paraformer cancellation | Cancel before next chunk or after a returned inference result | Same test; no retry inside request |
| SenseVoice decode fails | Lock is released; next request can reuse recognizer | `test_sensevoice_recovers_after_failure_and_cancels_at_boundary` |
| SenseVoice cancellation | Cancel before/after native offline decode; decode itself is not interruptible | Same test |
| PipeWire capture process disconnects | Session fails privately; next capture starts a new process | `test_capture_provider_recovers_after_process_disconnect` |
| Default device changes | Fresh inventory resolves the new default | `test_default_selection_rebinds_after_device_change` |
| Manual device disappears | Fixed unavailable error; no fallback | Same device-change test |

The tests do not save audio, provider output, or transcript text. They use fake
model/process boundaries and therefore do not claim live hot-unplug timing.

## Remaining Live Evidence

- USB microphone unplug/replug during capture.
- Default source change between two diagnostic runs.
- Runtime process restart after model/provider failure.
- SenseVoice hard cancellation during a long native decode.

These require explicit coordination and remain separate from deterministic unit
coverage.

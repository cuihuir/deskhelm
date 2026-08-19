# Controlled Live ASR Diagnostic

Date: 2026-08-19

Status: Verified diagnostic path; ASR quality unresolved

## Conclusion

DeskHelm now has a microphone-only controlled ASR diagnostic that correlates
input signal quality, public-phrase accuracy, and latency without saving PCM or
printing recognized text.

The first real run captured a healthy, unclipped signal but Paraformer returned
`voice_no_transcript`. This makes low input level and clipping less likely as
the primary cause. It does not prove that the full prompted utterance reached
the microphone, because this run did not measure speech-active duration.

## Diagnostic Contract

`tools/run-local-asr-diagnostic.py`:

- requires explicit `--live-audio` microphone consent;
- selects the default source or an exact stable source name;
- prompts a public phrase from the versioned benchmark corpus;
- bounds lead-in and capture duration;
- keeps PCM and recognized text in memory only;
- suppresses ASR provider stdout and stderr;
- reports signal, character count, CER, keyword accuracy, and latency only;
- maps empty recognition to `voice_no_transcript` and other ASR errors to
  `voice_asr_failed` without provider exception text;
- labels input-level guidance as provisional and never changes system gain.

The current signal hints are intentionally conservative:

- `possible_clipping`: peak at or above 0.999, or at least 0.1% near-full-scale
  samples;
- `too_quiet`: RMS below 0.005 and peak below 0.05;
- `low`: RMS below 0.015;
- `within_diagnostic_range`: none of the preceding conditions.

These thresholds support troubleshooting only and are not a production
calibration policy.

## First Real Run

Public corpus utterance: `zh-repeat-01`

Runtime: pinned Paraformer Python 3.12 CPU environment in the resource-limited
`ubuntu24-r23` Distrobox.

Source: `alsa_input.usb-C-Media_Electronics_Inc._USB_PnP_Sound_Device-00.analog-mono`
(`PCM2902 Audio Codec 模拟单声道`).

| Measurement | Result |
|---|---:|
| Captured duration | 6,974.312 ms |
| Captured bytes | 223,178 |
| Format | 16 kHz mono S16LE |
| Peak | 0.506927 |
| RMS | 0.035902 |
| Clipped sample fraction | 0 |
| Near-silence fraction | 0.014383 |
| Provisional input hint | `within_diagnostic_range` |
| Final ASR latency | 11,329.329 ms |
| Result | `voice_no_transcript` |
| Transcript text retained or printed | No |

## Interpretation

Verified facts:

- PipeWire delivered a complete bounded recording from the selected USB input.
- The signal was neither too quiet by the diagnostic threshold nor clipped.
- Paraformer returned no usable text for the controlled run.
- PCM and recognized text were neither saved nor printed.

Reasonable inference:

- Simple input amplitude is unlikely to explain the repeated empty recognition.
  Current Paraformer model behavior, microphone/noise-domain mismatch, effective
  speech content, or streaming parameters remain plausible causes.

Unknowns:

- speech-active duration and whether the entire prompted phrase was present;
- effects of WebRTC/Silero activity thresholds on this live source;
- performance of a second Chinese or multilingual ASR on the same controlled
  procedure;
- recovery after source disconnect, timeout, or default-device change.

## Next Evidence

1. Add privacy-safe speech-active duration and segment counts to the controlled
   diagnostic, without allowing VAD to gate ASR.
2. Repeat the same public phrase with controlled microphone distance and gain.
3. Run at least one alternative ASR through the same diagnostic contract before
   selecting a production model.
4. Test source disconnect, inference timeout, and device-change recovery.

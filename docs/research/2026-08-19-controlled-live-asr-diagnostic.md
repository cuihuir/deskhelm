# Controlled Live ASR Diagnostic

Date: 2026-08-19

Status: Verified diagnostic path and first user-confirmed spoken run

## Conclusion

DeskHelm now has a microphone-only controlled ASR diagnostic that correlates
input signal quality, public-phrase accuracy, and latency without saving PCM or
printing recognized text.

The first run captured an unclipped background signal and Paraformer returned
`voice_no_transcript`. The user later confirmed that they did not speak during
the capture. This is therefore a useful negative control for silence/background
handling, not evidence about Paraformer recognition quality or spoken input
level.

A later user-confirmed spoken run produced a transcript with both expected
keywords present, but character error rate was 0.545455. Paraformer can recover
the core command intent on this source, while its current literal accuracy is
not sufficient for code-sensitive commands or production selection.

The best synchronized run started capture immediately after an explicit
readiness handshake. It detected 6,970.312 ms of advisory activity and reduced
CER to 0.181818 while retaining full keyword accuracy. This is the current live
Paraformer baseline, although exact code-sensitive accuracy remains inadequate.

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
- labels input-level guidance as provisional and never changes system gain;
- marks every result as requiring post-run confirmation that the user actually
  spoke before anyone interprets signal or recognition quality.
- starts capture immediately by default; optional lead-in exists for direct
  terminal use but is unreliable as an unseen chat countdown.

The current signal hints are intentionally conservative:

- `possible_clipping`: peak at or above 0.999, or at least 0.1% near-full-scale
  samples;
- `too_quiet`: RMS below 0.005 and peak below 0.05;
- `low`: RMS below 0.015;
- `within_diagnostic_range`: none of the preceding conditions.

These thresholds support troubleshooting only and are not a production
calibration policy.

## Unspoken Negative-Control Run

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

The user confirmed after the run that no phrase was spoken. The measured peak,
RMS, and near-silence fraction describe the available background/input signal,
not voice quality.

A second unspoken repeat captured 8,019.625 ms with peak 0.111298, RMS 0.037251,
zero clipped samples, and another `voice_no_transcript`. The user also confirmed
that they did not speak during that attempt. Both runs are negative controls.

## Interpretation

Verified facts:

- PipeWire delivered a complete bounded recording from the selected USB input.
- The background/input signal was not clipped.
- Paraformer returned no text when no phrase was spoken.
- PCM and recognized text were neither saved nor printed.
- The user explicitly confirmed that they did not speak during this run.

Reasonable inference:

- `voice_no_transcript` is an appropriate outcome for this unspoken negative
  control. No model-quality conclusion can be drawn from it.

## User-Confirmed Spoken Run

The third attempt used the same public `zh-repeat-01` phrase. The user confirmed
after capture that they spoke it. A short speaker cue was attempted but the user
did not hear it, so audible cues are not considered a reliable synchronization
mechanism.

| Measurement | Result |
|---|---:|
| Captured duration | 8,019.625 ms |
| Captured bytes | 256,628 |
| Peak | 0.914215 |
| RMS | 0.120620 |
| Clipped sample fraction | 0 |
| Near-silence fraction | 0.007902 |
| Provisional input hint | `within_diagnostic_range` |
| Transcript characters | 27 |
| Exact match | No |
| Character error rate | 0.545455 |
| Keyword accuracy | 1.0 |
| First partial latency | 1,850.498 ms |
| Final ASR latency | 5,785.955 ms |
| Transcript text retained or printed | No |

Verified facts:

- The user confirmed speaking the prompted phrase during this capture.
- Paraformer produced non-empty text and matched both expected keywords.
- More than half of normalized reference characters required edit operations.
- The input was strong but did not reach the clipping threshold.

Interpretation:

- The current path can extract the phrase's core actions, but character-level
  accuracy is too low for punctuation, paths, symbols, negation, or exact code
  commands. This supports keeping Paraformer provisional and comparing a second
  ASR under the same privacy-safe contract.

## Advisory Activity and Capture Synchronization

Adding WebRTC VAD aggregate metrics exposed two important controls:

- An unspoken eight-second run still produced five activity segments totaling
  739.625 ms, or 9.2227% of the capture. VAD can mistake background/transients
  for speech and must not prove participation or gate ASR.
- A user-confirmed attempt with a ten-second unseen lead-in produced only
  180 ms of activity and no transcript. The likely timing mismatch means this
  run cannot evaluate the model even though the user did speak around the test.

The workflow therefore changed to an explicit readiness handshake, immediate
capture, a long recording window, and mandatory post-run confirmation. Audible
cues are also unsuitable because the attempted speaker cue was not heard.

## Immediate-Capture Confirmed Run

The user confirmed readiness before launch and confirmed complete speech after
the 12-second immediate-capture run.

| Measurement | Result |
|---|---:|
| Captured duration | 12,030.312 ms |
| Captured bytes | 384,970 |
| Peak | 0.779144 |
| RMS | 0.091119 |
| Clipped sample fraction | 0 |
| Near-silence fraction | 0.007362 |
| VAD status | `ok` |
| Speech segments | 7 |
| Speech-active duration | 6,970.312 ms |
| Speech-active fraction | 0.579396 |
| Transcript characters | 18 |
| Exact match | No |
| Character error rate | 0.181818 |
| Keyword accuracy | 1.0 |
| First partial latency | 4,850.187 ms |
| Final ASR latency | 8,008.233 ms |
| Transcript text retained or printed | No |

Verified facts:

- The user confirmed complete speech within the actual capture window.
- VAD observed substantial activity, but remained independent of ASR.
- Paraformer recovered both expected keywords with materially better CER than
  the earlier spoken run.
- PCM, VAD frame detail, and recognized text were not persisted or printed.

Limitations:

- A 0.181818 CER is still too high for exact paths, symbols, numbers, negation,
  or commands with irreversible effects.
- The 0.579396 activity fraction is detector output, not ground-truth labeling;
  the unspoken false positives show it cannot select endpoint policy alone.
- First partial remains about 4.85 seconds from available audio plus processing,
  and final ASR remains about 8 seconds after capture.

Unknowns:

- effects of WebRTC/Silero activity thresholds on this live source;
- performance of a second Chinese or multilingual ASR on the same controlled
  procedure;
- recovery after source disconnect, timeout, or default-device change.

## Next Evidence

1. Repeat several short Chinese and mixed commands with readiness and post-run
   confirmation.
2. Run at least one alternative ASR through the same diagnostic contract before
   selecting a production model.
3. Test source disconnect, inference timeout, and device-change recovery.

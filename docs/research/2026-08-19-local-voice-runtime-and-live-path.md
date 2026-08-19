# Unified Local Voice Runtime and Live Path

Date: 2026-08-19

Status: Verified provisional path; not a production provider selection

## Conclusion

A single ignored Python 3.12 environment now runs the pinned Paraformer ASR and
Piper TTS candidates together. A real four-second PipeWire capture completed
the batch PTT path through final transcription, fixed-response synthesis, and
playback on the current computer sink.

This validates provider composition and real device access. It does not
validate VAD, partial transcripts, Codex response generation, actual
speaker-first-audio latency, hot-plug recovery, or production recognition
quality.

## Runtime

The environment is stored outside version control at
`references/vendor/local-voice-runtime/py312` and is described by
`voice/runtime/requirements-local-voice-py312.txt`.

| Component | Version |
|---|---:|
| Python | 3.12.3 |
| FunASR | 1.3.21 |
| PyTorch / torchaudio CPU | 2.11.0 |
| NumPy | 2.5.2 |
| Piper | 1.7.0 |
| ONNX Runtime | 1.29.0 |
| unicode-rbnf | 2.4.0 |
| g2pw | 0.1.1 |
| sentence-stream | 1.3.0 |

Piper Chinese synthesis did not work with only the previously documented core
packages. Runtime execution established that `g2pw==0.1.1` and
`sentence-stream==1.3.0` are also required. Both are pinned in the runtime
requirements file.

The runtime was exercised inside the resource-limited `ubuntu24-r23`
Distrobox container with CPUs 4-23, an 18-CPU quota, 18 GiB memory, 20 GiB
memory plus swap, and 2,048 PIDs.

## Offline Verification

The combined environment reran each candidate without changing model
artifacts:

| Path | Result | Key observations |
|---|---:|---|
| Paraformer | 8/8 | 8.87 s cold load, 0.225 mean RTF, 3.09 GiB process peak RSS |
| Piper Chaowen | 12/12 | 2.38 s cold load, 0.037 mean RTF, 182/613 ms first-chunk p50/p95 |

These results confirm coexistence in one environment. Accuracy conclusions do
not change: Paraformer's small public set still has 0.438 mean CER, 0.643
English WER, and 0.438 keyword accuracy, so it is not selected as DeskHelm's
sole ASR.

Raw observations and summaries remain ignored under
`voice/benchmarks/results/`.

## PipeWire Boundary

The container's PipeWire 1.0.5 `pw-cat` does not provide the `--raw` option
required by DeskHelm's explicit PCM provider. The Fedora host has PipeWire
1.6.8 and does provide it. The provider command prefix is therefore
configurable; this container uses:

```text
host-spawn -no-pty pw-cat
```

`-no-pty` is necessary because playback launched through a pseudo-terminal did
not terminate reliably after stdin reached EOF. Native host execution keeps the
default `pw-cat` prefix.

Discovery from the container resolved:

- Source: `alsa_input.usb-C-Media_Electronics_Inc._USB_PnP_Sound_Device-00.analog-mono`
- Sink: `alsa_output.pci-0000_00_1f.3.analog-stereo`

A separate two-second microphone diagnostic captured 1,975.875 ms and discarded
the PCM after calculating a peak of 1.0 and RMS of approximately 0.394. The
full-scale peak indicates probable clipping or excessive input gain. DeskHelm
did not change system gain or volume. A 100 ms, 1% output tone also completed.

## Live Full-Chain Result

`tools/run-local-voice-live.py` requires `--live-audio`, captures for a bounded
2-15 seconds, and prints only privacy-safe event and timing metadata. It neither
saves nor prints microphone PCM or transcript text. The spoken response is a
fixed public diagnostic sentence rather than recognized or Agent-generated
content.

The verified four-second run produced:

```text
ptt_started -> transcribing -> transcript_ready -> speech_started -> speech_completed
```

| Measurement | Result |
|---|---:|
| Status | ok |
| Release to transcribing | 12.930 ms |
| Release to final transcript | 7,186.167 ms |
| Transcript to `speech_started` event | 0.142 ms |
| `speech_started` to playback complete | 4,108.078 ms |
| Total run | 15,294.695 ms |
| Transcript size retained in output | 1 character count only |

The one-character result is not evidence of acceptable recognition quality. It
may reflect the live utterance, input level, or clipping and needs a controlled,
consented command set before ASR selection.

`speech_started` currently occurs before TTS synthesis, so the table does not
measure actual first audio at the speaker. This limitation is emitted by the
tool and must not be relabeled as first-speaker-audio latency.

## Remaining Evidence

1. Run consented Chinese and mixed coding commands with controlled input gain,
   then compare at least one alternative ASR.
2. Measure actual first speaker audio and live interruption at the audio
   boundary rather than inferring it from lifecycle events.
3. Add disconnect, default-device change, timeout, and provider-failure recovery
   runs without persisting private audio or text.
4. Migrate capture to frame-positioned streaming PCM before attaching WebRTC or
   Silero VAD and publishing partial transcripts.
5. Exercise the real `PTT -> ASR -> Codex -> TTS` composition separately; this
   diagnostic intentionally substituted a fixed public response for Codex.

## Streaming Capture Follow-Up

Later on 2026-08-19, PipeWire and Voice Gateway capture migrated to the accepted
frame-positioned chunk boundary. A privacy-safe four-second signal diagnostic
through the new stream captured 128,286 bytes, 4,008.938 ms of 16 kHz mono
S16LE, peak 0.399292, and RMS 0.061689. The PCM was discarded.

Two complete live attempts reached `transcribing` but did not produce a final
transcript or playback. Release-to-transcribing was 7.197 ms and 14.177 ms; the
runs ended after 11,623.340 ms and 9,448.426 ms respectively. The user confirmed
speaking during the second attempt. No PCM or transcript text was retained.

The healthy signal metadata makes a capture-boundary failure less likely, but
does not by itself prove why Paraformer returned no usable result. The provider
now maps an explicit empty recognition to `voice_no_transcript`, while other
capture/runtime/model failures remain `voice_input_failed`; this distinction is
unit-tested but still needs a subsequent live confirmation. The result reinforces
the existing decision not to select Paraformer as the sole production ASR.

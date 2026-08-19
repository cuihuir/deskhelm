# Local Audio Selection and Live Diagnostics

Date: 2026-08-19

Status: Initial live preflight; recovery still incomplete

## Conclusion

The application audio boundary successfully resolves and exercises the current
computer-default PipeWire devices without persisting audio. Default and manual
USB microphone tests succeeded at two seconds, and the default internal sink
played a short low-volume tone. A one-second microphone test produced no PCM,
which exposes a startup/readiness boundary that must be addressed before live
PTT latency is characterized.

## Environment

- PipeWire tools: 1.6.8.
- Available audio nodes: three sources and three sinks.
- Default source:
  `alsa_input.usb-C-Media_Electronics_Inc._USB_PnP_Sound_Device-00.analog-mono`
  (`PCM2902 Audio Codec` mono USB input).
- Default sink: `alsa_output.pci-0000_00_1f.3.analog-stereo` (internal analog
  stereo output).

Discovery used bounded `pw-dump --no-colors` plus `wpctl inspect` for the two
defaults. It did not open an audio stream. A nonexistent manual source returned
an explicit error and did not fall back.

## Live Results

| Test | Selection | Result |
|---|---|---|
| Input, 1 second | current default | explicit `produced no audio` failure |
| Input, 2 seconds | current default | 63,434 bytes; 1,982 ms; peak 0.0478; RMS 0.0157 |
| Input, 2 seconds | same stable name manually selected | 63,434 bytes; 1,982 ms; peak 0.1065; RMS 0.0166 |
| Output, 250 ms | current default | 660 Hz tone at 5% level completed |

The signal values only prove that non-empty audio reached the application. They
are not a calibrated microphone-quality or noise-floor measurement. Captured
PCM was held in memory, reduced to duration/peak/RMS, and discarded. No WAV or
raw audio artifact was written.

## Commands

```bash
PYTHONPATH=bridge:voice python3 -m deskhelm_bridge audio status --list
PYTHONPATH=bridge:voice python3 -m deskhelm_bridge audio test-input --seconds 2
PYTHONPATH=bridge:voice python3 -m deskhelm_bridge audio test-input \
  --seconds 2 --source \
  alsa_input.usb-C-Media_Electronics_Inc._USB_PnP_Sound_Device-00.analog-mono
PYTHONPATH=bridge:voice python3 -m deskhelm_bridge audio test-output \
  --seconds 0.25 --level 0.05
```

## Remaining Evidence

- Define whether PTT capture should prewarm, wait for a readiness threshold, or
  treat a short empty capture as a recoverable retryable condition.
- Test hot unplug, default-device switching, unavailable devices during an
  active stream, and reconnection without silently changing a manual target.
- Measure capture-to-first-partial, PTT-release-to-final, playback first audio,
  and interruption through the composed Voice Gateway.
- Repeat with the future DeskHelm keyboard microphone and document preference
  plus disconnect fallback before making it the default.

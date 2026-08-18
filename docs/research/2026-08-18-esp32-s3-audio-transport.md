# ESP32-S3 Audio and Control Transport Research

Date: 2026-08-18

Status: Research input. This does not select the production MCU or freeze a
wire protocol.

## Goal

Evaluate a future DeskHelm keyboard built around ESP32-S3 that carries:

- keyboard and physical-control input;
- device, RGB, display, and Agent state;
- push-to-talk microphone audio over Wi-Fi or Bluetooth LE.

The immediate local proof of concept remains simpler: a USB microphone provides
input through PipeWire, and the computer's configured default speakers provide
playback. It does not need a wireless audio codec.

## Verified ESP32-S3 Capabilities

Espressif documents ESP32-S3 as a dual-core Xtensa LX7 MCU up to 240 MHz with
512 KB internal SRAM, 2.4 GHz 802.11 b/g/n Wi-Fi, and Bluetooth 5 LE. The BLE
radio supports 2 Mbps PHY, coded PHY, and extended advertising. The product
page advertises Bluetooth LE rather than Bluetooth Classic, so a design must not
depend on Classic HFP or A2DP profiles.

Espressif's official `esp_audio_codec` component supports ESP32-S3 and provides
Opus encode/decode with:

- 8, 12, 16, 24, and 48 kHz sample rates;
- mono and dual-channel audio;
- signed 16-bit PCM encoder input;
- 2.5 through 120 ms frame durations;
- VoIP and music application modes;
- complexity levels 0 through 10;
- constant or variable bitrate, FEC, and DTX.

The component's published ESP32-S3 performance example reports approximately
29.4 KB encoder heap and 24.9% CPU for 48 kHz stereo, 90 kbps, complexity 0,
plus a recommendation of roughly 40 KB task stack when all encoders are
supported. This is not a prediction for DeskHelm's lower-rate mono profile and
must be benchmarked with the actual firmware configuration.

## Recommended Future Split

```text
BLE HID
  -> keyboard keys, knobs, buttons, and media controls

BLE GATT or reliable Wi-Fi control channel
  -> device state, configuration, RGB/display projection, PTT lifecycle

Wi-Fi datagrams
  -> time-sensitive Opus microphone frames
```

Do not place audio inside HID reports. Reliable controls and expiring audio have
different retry and ordering requirements.

### Primary Voice Path

Use Wi-Fi with raw Opus packets as the primary wireless microphone transport:

```text
I2S microphone
  -> PCM S16LE, 16 kHz, mono
  -> optional AFE / VAD
  -> Opus encoder
  -> bounded Wi-Fi audio stream
  -> Voice Gateway
  -> Opus decoder
  -> PCM
  -> VAD / ASR
```

Initial profile for measurement:

| Setting | Initial value |
|---|---|
| Opus application | VoIP |
| Sample rate | 16 kHz |
| Channels | Mono |
| PCM input | Signed 16-bit |
| Frame duration | 20 ms / 320 samples |
| Bitrate | 24 kbps CBR |
| Comparison points | 20, 24, and 32 kbps |
| Complexity | 1 to 3, selected by firmware measurement |
| DTX | Off for PTT; evaluate only for open-microphone modes |
| FEC | Enable only for an unreliable datagram path with measured loss |
| Container | None; transmit one framed Opus packet at a time |

At 24 kbps, one 20 ms payload is approximately 60 bytes before transport
headers. Raw 16 kHz mono PCM is approximately 32 KB/s, while the Opus payload is
approximately 3 KB/s.

Each audio packet should eventually carry a protocol version, stream ID,
sequence, capture timestamp, codec profile, frame duration, payload length, and
PTT lifecycle flags. Exact framing requires a transport ADR.

### Bluetooth LE Position

BLE remains the preferred keyboard transport. A custom GATT service can carry
low-rate state and may carry Opus as a fallback, but it will require a DeskHelm
desktop client and will not appear as a standard operating-system microphone.
The design must negotiate a useful ATT MTU or fragment each Opus frame with
bounded reassembly.

Do not assume Bluetooth LE Audio or LC3 merely because the codec library
contains LC3. The ESP32-S3 product capabilities cited here do not establish a
complete LE Audio isochronous microphone profile. That path requires separate
controller, host-stack, operating-system, and interoperability verification.

## Current Local Development Path

The first production-like software provider will use:

```text
USB microphone
  -> PipeWire configured USB input
  -> bounded PCM capture
  -> local VAD / ASR

local TTS
  -> bounded PCM playback
  -> PipeWire configured/default computer speakers
```

This path deliberately avoids Opus. Capture and ASR already require PCM, and
there is no constrained link between the USB microphone, Voice Gateway, and
computer speakers. The implementation should select the configured USB source
by stable PipeWire node name and fail recoverably when it is absent, rather than
silently capture from an unrelated default source. Playback may follow the
configured default sink. Numeric PipeWire object IDs must not be persisted.

The 2026-08-18 PipeWire preflight observed the built-in analog source as the
current default, not a USB microphone. USB source discovery and stable-name
selection therefore remain unverified until the intended microphone is
connected.

## Validation Required Before Hardware Freeze

- Measure ESP32-S3 Opus CPU, heap, stack, and power at 16 kHz mono for
  complexity 1 through 3.
- Compare PCM and Opus at 20, 24, and 32 kbps with the DeskHelm corpus using
  CER and keyword accuracy, especially paths, versions, symbols, numbers, and
  negation.
- Test simultaneous BLE HID and Wi-Fi audio for coexistence latency and packet
  loss on the intended antenna and enclosure.
- Test PTT start/end delivery, sequence gaps, jitter bounds, cancellation, and
  reconnect behavior.
- Verify the exact codec component and third-party license set used by the
  shipping firmware.

## Sources

- Espressif ESP32-S3 product page:
  <https://www.espressif.com/en/products/socs/esp32-s3>
- Espressif `esp_audio_codec` component documentation, version 2.6.2 reviewed
  on 2026-08-18:
  <https://components.espressif.com/components/espressif/esp_audio_codec>

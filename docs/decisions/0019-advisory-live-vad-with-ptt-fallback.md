# ADR 0019: Add Advisory Live VAD With PTT Fallback

Date: 2026-08-19

Status: Accepted

## Context

PipeWire and Voice Gateway now use the frame-positioned streaming capture
boundary. WebRTC and Silero ONNX VAD adapters have reproducible offline evidence,
but neither candidate has enough noisy, conversational, Chinese, or recovery
evidence to own live endpointing.

Attaching VAD introduces behavior that the earlier ADRs did not settle:

- whether detected silence may stop capture before the user releases PTT;
- whether a VAD miss may suppress ASR;
- whether a VAD runtime failure invalidates an otherwise usable recording;
- how frame-positioned activity is exposed without leaking audio;
- how malformed or excessive provider output is bounded.

## Decision

Add optional advisory VAD to the Voice Gateway streaming capture path.

- Keep VAD disabled by default. Local composition may explicitly select one
  provisional provider.
- Use WebRTC VAD mode 2 with the already benchmarked 20 ms, 3-of-5 start, and
  8-of-10 end configuration as the first live option. This is a provisional
  low-cost integration, not a production selection.
- Open one provider-owned VAD session after the first valid PCM chunk fixes the
  stream format. Feed every subsequent chunk in order and call `finish()` once
  at PTT release.
- Keep PTT release as the only capture endpoint. VAD must not set the capture
  stop signal, trim PCM, skip final ASR, or publish partial transcripts in this
  phase.
- Emit privacy-safe `input_speech_started` and `input_speech_ended` lifecycle
  events with absolute audio frame positions. Do not attach PCM, transcript, or
  provider output.
- Limit one capture to 256 VAD lifecycle events. Require valid `VadEvent`
  values, nondecreasing frame positions, alternating start/end events, and
  positions no later than supplied audio. An active region must end during
  processing or final flush.
- Treat VAD startup, processing, validation, flush, or cleanup failure as
  non-terminal.
  Emit one `input_activity_failed` event with fixed code `voice_vad_failed`,
  close the session, and continue the ordinary PTT recording and final ASR.
- Cancellation remains terminal for the whole capture and must not be converted
  into VAD fallback.
- Keep optional VAD dependencies outside Bridge and disabled local voice. Pin
  the selected runtime only in the ignored optional local voice environment.

## Consequences

- Live VAD timing and boundary events can be measured without granting an
  unselected detector control over user input.
- PTT remains a reliable fallback when VAD is absent, misses speech, or fails.
- The Gateway gains provider-neutral session ownership, output validation,
  explicit final flushing, and failure cleanup required by later endpointing.
- Full PCM is still retained in memory until PTT release and passed to final
  ASR; this phase does not reduce latency or memory use.
- Automatic endpointing, speech-only trimming, partial ASR, and VAD-driven PTT
  release require a later ADR backed by live measurements and recovery tests.

## Alternatives

- Let VAD automatically release PTT after silence: rejected because neither
  candidate has sufficient live evidence and a false endpoint could truncate a
  consequential command.
- Skip ASR when VAD reports no speech: rejected because a VAD miss must not
  discard an explicit user PTT recording.
- Fail the complete capture when VAD fails: rejected because advisory metadata
  must not make the established PTT path less reliable.
- Enable Silero first: deferred. It remains a valid neural comparison, but
  WebRTC had lower replay cost and better F1 on the first limited benchmark and
  requires no model artifact.

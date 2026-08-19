# Architecture Decision Records

文件命名格式为 `NNNN-short-title.md`。每条 ADR 至少记录背景、候选方案、决定、影响和状态。

Current voice decisions include
[`0013-select-webrtc-and-silero-vad-baselines.md`](0013-select-webrtc-and-silero-vad-baselines.md),
which fixes the first reproducible external-audio VAD candidates without making
a final production selection.

[`0014-use-paraformer-as-initial-streaming-asr-baseline.md`](0014-use-paraformer-as-initial-streaming-asr-baseline.md)
pins the first licensed streaming ASR baseline and records why it is not yet the
sole production default.

[`0015-use-piper-as-initial-notification-tts-baseline.md`](0015-use-piper-as-initial-notification-tts-baseline.md)
selects Piper for low-latency notification experiments while retaining Kokoro
as the quality candidate and deferring the final production choice.

[`0016-explicit-local-audio-selection-and-diagnostics.md`](0016-explicit-local-audio-selection-and-diagnostics.md)
defines default/manual stable-device selection and privacy-safe explicit local
audio diagnostics without prematurely activating production voice models.

[`0017-correlate-external-ptt-press-and-release.md`](0017-correlate-external-ptt-press-and-release.md)
adds targeted external PTT controls and prevents stale or cross-session release
commands from stopping an unrelated capture.

[`0018-opt-in-provisional-local-voice-composition.md`](0018-opt-in-provisional-local-voice-composition.md)
composes PipeWire, Paraformer, and Piper behind an explicit Bridge opt-in while
deferring VAD until the gateway has a real streaming capture path.

[`0019-advisory-live-vad-with-ptt-fallback.md`](0019-advisory-live-vad-with-ptt-fallback.md)
attaches optional live VAD as bounded activity observation while keeping PTT
release authoritative and isolating VAD failure from final ASR.

[`0020-evaluate-sensevoice-as-second-asr-baseline.md`](0020-evaluate-sensevoice-as-second-asr-baseline.md)
selects a compact final-only SenseVoice/sherpa-onnx comparison while recording
its non-streaming boundary and custom model-license risk.

[`0021-bounded-voice-recovery-and-device-rebind.md`](0021-bounded-voice-recovery-and-device-rebind.md)
defines retry boundaries, cancellation points, PipeWire session recovery, and
strict default/manual device rebinding.

[`0022-bounded-multi-phrase-asr-diagnostic.md`](0022-bounded-multi-phrase-asr-diagnostic.md)
defines repeatable phrase selection, fresh captures, per-phrase confirmation,
and privacy-safe batch output for coding-command comparisons.

[`0023-one-by-one-asr-readiness-handshake.md`](0023-one-by-one-asr-readiness-handshake.md)
adds an opt-in bounded stdin handshake before each phrase capture so
chat-driven diagnostics do not race automatic prompt transitions.

[`0024-bounded-asr-timeout.md`](0024-bounded-asr-timeout.md) adds a bounded
Voice Gateway ASR deadline, fixed timeout failure, and explicit retry boundary
without claiming hard cancellation for native provider calls.

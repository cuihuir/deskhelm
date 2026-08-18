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

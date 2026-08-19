from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Condition, Event, RLock

from .models import (
    CapturedAudio,
    SpeechItem,
    SpeechPriority,
    Transcript,
    VoiceEvent,
    VoiceEventKind,
    VoicePttState,
    VoiceTarget,
)
from .providers import (
    AsrProvider,
    CaptureProvider,
    PcmChunkStream,
    PlaybackProvider,
    StreamingCaptureProvider,
    TtsProvider,
    VoiceCancelled,
    VoiceNoTranscript,
)
from .streaming import PcmStreamFormat


PromptSink = Callable[[VoiceTarget, Transcript], None]
VoiceEventSink = Callable[[VoiceEvent], None]
MAX_CAPTURE_CHUNKS = 10_000


@dataclass(frozen=True, slots=True)
class _QueuedSpeech:
    sequence: int
    item: SpeechItem


@dataclass(slots=True)
class VoiceGateway:
    capture_provider: CaptureProvider | StreamingCaptureProvider
    asr_provider: AsrProvider
    tts_provider: TtsProvider
    playback_provider: PlaybackProvider
    max_capture_seconds: float = 30.0
    max_capture_bytes: int = 1 << 20
    max_speech_items: int = 8
    event_sink: VoiceEventSink | None = None
    _prompt_sink: PromptSink | None = field(default=None, init=False, repr=False)
    _ptt_state: VoicePttState = field(
        default=VoicePttState.IDLE, init=False
    )
    _ptt_target: VoiceTarget | None = field(default=None, init=False)
    _ptt_activation_id: str = field(default="", init=False)
    _ptt_stop: Event | None = field(default=None, init=False, repr=False)
    _ptt_cancel: Event | None = field(default=None, init=False, repr=False)
    _speech_queue: list[_QueuedSpeech] = field(default_factory=list, init=False)
    _speech_sequence: int = field(default=0, init=False)
    _current_speech: SpeechItem | None = field(default=None, init=False)
    _current_speech_cancel: Event | None = field(
        default=None, init=False, repr=False
    )
    _closed: bool = field(default=False, init=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)
    _condition: Condition = field(init=False, repr=False)
    _capture_executor: ThreadPoolExecutor = field(init=False, repr=False)
    _playback_executor: ThreadPoolExecutor = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_capture_seconds, (int, float))
            or isinstance(self.max_capture_seconds, bool)
            or not 0 < self.max_capture_seconds <= 120
        ):
            raise ValueError("max_capture_seconds is invalid")
        if (
            not isinstance(self.max_capture_bytes, int)
            or isinstance(self.max_capture_bytes, bool)
            or not 2 <= self.max_capture_bytes <= 64 << 20
        ):
            raise ValueError("max_capture_bytes is invalid")
        if self.max_speech_items < 1:
            raise ValueError("max_speech_items must be at least 1")
        self._condition = Condition(self._lock)
        self._capture_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="deskhelm-voice-capture"
        )
        self._playback_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="deskhelm-voice-playback"
        )
        self._playback_executor.submit(self._playback_loop)

    def register_prompt_sink(self, sink: PromptSink) -> Callable[[], None]:
        if not callable(sink):
            raise ValueError("prompt sink must be callable")
        with self._lock:
            if self._closed:
                raise RuntimeError("voice gateway is closed")
            if self._prompt_sink is not None:
                raise RuntimeError("voice prompt sink is already registered")
            self._prompt_sink = sink

        def unregister() -> None:
            with self._lock:
                if self._prompt_sink is sink:
                    self._prompt_sink = None

        return unregister

    def press_ptt(self, target: VoiceTarget, activation_id: str = "") -> None:
        if not isinstance(target, VoiceTarget):
            raise ValueError("PTT target is invalid")
        if not isinstance(activation_id, str):
            raise ValueError("PTT activation_id must be a string")
        with self._condition:
            if self._closed:
                raise RuntimeError("voice gateway is closed")
            if self._ptt_state is not VoicePttState.IDLE:
                raise RuntimeError("PTT is already active")
            if self._prompt_sink is None:
                raise RuntimeError("voice prompt sink is unavailable")
            if (
                self._current_speech is not None
                and self._current_speech.interruptible
                and self._current_speech_cancel is not None
            ):
                self._current_speech_cancel.set()
            stop = Event()
            cancel = Event()
            begin = Event()
            self._ptt_state = VoicePttState.CAPTURING
            self._ptt_target = target
            self._ptt_activation_id = activation_id
            self._ptt_stop = stop
            self._ptt_cancel = cancel
            self._condition.notify_all()
            try:
                self._capture_executor.submit(
                    self._capture_after_start, begin, target, stop, cancel
                )
            except BaseException:
                self._clear_ptt_locked()
                raise
        try:
            self._emit(VoiceEvent(VoiceEventKind.PTT_STARTED, target))
        finally:
            begin.set()

    def release_ptt(
        self,
        target: VoiceTarget | None = None,
        activation_id: str = "",
    ) -> bool:
        if target is not None and not isinstance(target, VoiceTarget):
            raise ValueError("PTT target is invalid")
        if not isinstance(activation_id, str):
            raise ValueError("PTT activation_id must be a string")
        if (target is None) != (activation_id == ""):
            raise ValueError(
                "target and activation_id must both be set for targeted release"
            )
        with self._lock:
            if self._ptt_state is VoicePttState.IDLE or self._ptt_stop is None:
                return False
            if target is not None and self._ptt_target != target:
                return False
            if activation_id and self._ptt_activation_id != activation_id:
                return False
            self._ptt_stop.set()
            return True

    def cancel_ptt(self) -> None:
        with self._lock:
            if self._ptt_state is VoicePttState.IDLE:
                return
            if self._ptt_cancel is not None:
                self._ptt_cancel.set()
            if self._ptt_stop is not None:
                self._ptt_stop.set()

    def ptt_state(self) -> VoicePttState:
        with self._lock:
            return self._ptt_state

    def enqueue_speech(self, item: SpeechItem) -> str:
        if not isinstance(item, SpeechItem):
            raise ValueError("speech item is invalid")
        with self._condition:
            if self._closed:
                raise RuntimeError("voice gateway is closed")
            if len(self._speech_queue) >= self.max_speech_items:
                raise RuntimeError("voice speech queue capacity is exhausted")
            queued = _QueuedSpeech(self._speech_sequence, item)
            self._speech_sequence += 1
            self._speech_queue.append(queued)
            self._condition.notify_all()
        return item.speech_id

    def stop_speaking(self, target: VoiceTarget, speech_id: str = "") -> int:
        if not isinstance(target, VoiceTarget):
            raise ValueError("speech target is invalid")
        if not isinstance(speech_id, str):
            raise ValueError("speech_id must be a string")
        stopped = 0
        with self._condition:
            retained: list[_QueuedSpeech] = []
            for queued in self._speech_queue:
                item = queued.item
                matches = item.target == target and (
                    item.speech_id == speech_id
                    if speech_id
                    else item.interruptible
                )
                if matches:
                    stopped += 1
                else:
                    retained.append(queued)
            self._speech_queue = retained

            current = self._current_speech
            if current is not None and current.target == target:
                should_stop = (
                    current.speech_id == speech_id
                    if speech_id
                    else current.interruptible
                )
                if should_stop and self._current_speech_cancel is not None:
                    self._current_speech_cancel.set()
                    stopped += 1
            self._condition.notify_all()
        return stopped

    def queued_speech_count(self) -> int:
        with self._lock:
            return len(self._speech_queue)

    def report_failure(self, target: VoiceTarget, error_code: str) -> None:
        if not isinstance(target, VoiceTarget):
            raise ValueError("voice failure target is invalid")
        if not isinstance(error_code, str) or not error_code.strip():
            raise ValueError("voice failure error_code must not be empty")
        self._emit(
            VoiceEvent(
                VoiceEventKind.FAILURE,
                target,
                error_code=error_code,
            )
        )

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            if self._ptt_cancel is not None:
                self._ptt_cancel.set()
            if self._ptt_stop is not None:
                self._ptt_stop.set()
            if self._current_speech_cancel is not None:
                self._current_speech_cancel.set()
            self._speech_queue.clear()
            self._prompt_sink = None
            self._condition.notify_all()
        self._capture_executor.shutdown(wait=True, cancel_futures=False)
        self._playback_executor.shutdown(wait=True, cancel_futures=False)

    def _capture_and_transcribe(
        self, target: VoiceTarget, stop: Event, cancel: Event
    ) -> None:
        try:
            audio = self._capture_audio(stop, cancel)
            if cancel.is_set():
                raise VoiceCancelled()
            with self._condition:
                if self._ptt_target != target:
                    raise VoiceCancelled()
                self._ptt_state = VoicePttState.TRANSCRIBING
                self._condition.notify_all()
            self._emit(VoiceEvent(VoiceEventKind.TRANSCRIBING, target))
            transcript = self.asr_provider.transcribe(audio, cancel)
            if cancel.is_set():
                raise VoiceCancelled()
            self._emit(
                VoiceEvent(
                    VoiceEventKind.TRANSCRIPT_READY,
                    target,
                    transcript=transcript,
                )
            )
            with self._lock:
                prompt_sink = self._prompt_sink
            if prompt_sink is None:
                raise RuntimeError("voice prompt sink is unavailable")
            prompt_sink(target, transcript)
        except VoiceCancelled:
            self._emit(VoiceEvent(VoiceEventKind.PTT_CANCELLED, target))
        except VoiceNoTranscript:
            self._emit(
                VoiceEvent(
                    VoiceEventKind.FAILURE,
                    target,
                    error_code="voice_no_transcript",
                )
            )
        except Exception:
            self._emit(
                VoiceEvent(
                    VoiceEventKind.FAILURE,
                    target,
                    error_code="voice_input_failed",
                )
            )
        finally:
            with self._condition:
                if self._ptt_target == target:
                    self._clear_ptt_locked()
                self._condition.notify_all()

    def _capture_audio(self, stop: Event, cancel: Event) -> CapturedAudio:
        open_stream = getattr(self.capture_provider, "open_stream", None)
        if callable(open_stream):
            return self._collect_stream(open_stream(), stop, cancel)
        capture = getattr(self.capture_provider, "capture", None)
        if not callable(capture):
            raise RuntimeError("voice capture provider is invalid")
        audio = capture(stop, cancel)
        self._validate_captured_audio(audio)
        return audio

    def _collect_stream(
        self,
        stream: PcmChunkStream,
        stop: Event,
        cancel: Event,
    ) -> CapturedAudio:
        if cancel.is_set():
            raise VoiceCancelled()
        captured = bytearray()
        stream_format: PcmStreamFormat | None = None
        next_frame = 0
        chunk_count = 0
        with stream:
            while True:
                chunk = stream.read(stop, cancel)
                if chunk is None:
                    if not stop.is_set():
                        raise RuntimeError("voice capture ended before PTT release")
                    break
                chunk_count += 1
                if chunk_count > MAX_CAPTURE_CHUNKS:
                    raise RuntimeError("voice capture chunk limit exceeded")
                if stream_format is None:
                    stream_format = chunk.format
                if chunk.format != stream_format:
                    raise RuntimeError("voice capture format changed")
                if chunk.start_frame != next_frame:
                    raise RuntimeError("voice capture stream is discontinuous")
                next_frame = chunk.end_frame
                captured.extend(chunk.data)
                if len(captured) > self.max_capture_bytes:
                    raise RuntimeError("voice capture byte limit exceeded")
                if (
                    next_frame / stream_format.sample_rate_hz
                    > self.max_capture_seconds
                ):
                    raise RuntimeError("voice capture duration limit exceeded")
        if stream_format is None or not captured:
            raise RuntimeError("voice capture produced no audio")
        return CapturedAudio(
            bytes(captured),
            sample_rate_hz=stream_format.sample_rate_hz,
            channels=stream_format.channels,
            sample_format=stream_format.sample_format,
        )

    def _validate_captured_audio(self, audio: object) -> None:
        if not isinstance(audio, CapturedAudio):
            raise RuntimeError("voice capture provider returned invalid audio")
        if len(audio.data) > self.max_capture_bytes:
            raise RuntimeError("voice capture byte limit exceeded")
        if audio.duration_seconds > self.max_capture_seconds:
            raise RuntimeError("voice capture duration limit exceeded")

    def _capture_after_start(
        self,
        begin: Event,
        target: VoiceTarget,
        stop: Event,
        cancel: Event,
    ) -> None:
        begin.wait()
        self._capture_and_transcribe(target, stop, cancel)

    def _playback_loop(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(
                    lambda: self._closed
                    or (
                        self._speech_queue
                        and self._ptt_state is VoicePttState.IDLE
                    )
                )
                if self._closed:
                    return
                index = max(
                    range(len(self._speech_queue)),
                    key=lambda candidate: (
                        self._priority_rank(
                            self._speech_queue[candidate].item.priority
                        ),
                        -self._speech_queue[candidate].sequence,
                    ),
                )
                queued = self._speech_queue.pop(index)
                item = queued.item
                cancel = Event()
                self._current_speech = item
                self._current_speech_cancel = cancel
            self._emit(
                VoiceEvent(
                    VoiceEventKind.SPEECH_STARTED,
                    item.target,
                    speech_id=item.speech_id,
                )
            )
            try:
                audio = self.tts_provider.synthesize(item.text, cancel)
                if cancel.is_set():
                    raise VoiceCancelled()
                self.playback_provider.play(audio, cancel)
                if cancel.is_set():
                    raise VoiceCancelled()
                event_kind = VoiceEventKind.SPEECH_COMPLETED
                error_code = ""
            except VoiceCancelled:
                event_kind = VoiceEventKind.SPEECH_CANCELLED
                error_code = ""
            except Exception:
                event_kind = VoiceEventKind.FAILURE
                error_code = "voice_output_failed"
            self._emit(
                VoiceEvent(
                    event_kind,
                    item.target,
                    speech_id=item.speech_id,
                    error_code=error_code,
                )
            )
            with self._condition:
                if self._current_speech is item:
                    self._current_speech = None
                    self._current_speech_cancel = None
                self._condition.notify_all()

    def _clear_ptt_locked(self) -> None:
        self._ptt_state = VoicePttState.IDLE
        self._ptt_target = None
        self._ptt_activation_id = ""
        self._ptt_stop = None
        self._ptt_cancel = None

    def _emit(self, event: VoiceEvent) -> None:
        sink = self.event_sink
        if sink is None:
            return
        try:
            sink(event)
        except Exception:
            pass

    @staticmethod
    def _priority_rank(priority: SpeechPriority) -> int:
        return {
            SpeechPriority.LOW: 0,
            SpeechPriority.NORMAL: 1,
            SpeechPriority.HIGH: 2,
        }[priority]

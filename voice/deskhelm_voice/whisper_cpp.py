from __future__ import annotations

from array import array
import ctypes
import math
import re
import sys
from threading import Event, Lock, Thread
from typing import Callable

from .models import CapturedAudio, PcmSampleFormat, Transcript
from .providers import StreamingAsrResult, VoiceCancelled, VoiceNoTranscript


_EXPECTED_ABI = b"deskhelm_whisper_shim_v1"
_MAX_TRANSCRIPT_CHARS = 4096
_MAX_TRANSCRIPT_BYTES = _MAX_TRANSCRIPT_CHARS * 4 + 1
_STATUS_OK = 0
_STATUS_CANCELLED = 1
_STATUS_NO_TRANSCRIPT = 2
_STATUS_NATIVE_ERROR = 3
_STATUS_OUTPUT_TOO_SMALL = 4
_LANGUAGE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z-]{1,15}$")


class WhisperCppAsrProvider:
    """Explicit, lazy ASR provider for an externally built whisper.cpp shim."""

    def __init__(
        self,
        library_path: str,
        model_path: str,
        *,
        cpu_threads: int = 4,
        language: str = "auto",
        max_audio_seconds: float = 120.0,
        library_loader: Callable[[str], object] | None = None,
    ) -> None:
        _validate_path(library_path, "whisper.cpp shim library path")
        _validate_path(model_path, "whisper.cpp model path")
        if (
            not isinstance(cpu_threads, int)
            or isinstance(cpu_threads, bool)
            or not 1 <= cpu_threads <= 32
        ):
            raise ValueError("whisper.cpp CPU thread count is invalid")
        if (
            not isinstance(language, str)
            or not language
            or len(language) > 16
            or any(ord(char) < 0x20 for char in language)
            or (language != "auto" and _LANGUAGE_PATTERN.fullmatch(language) is None)
        ):
            raise ValueError("whisper.cpp language is invalid")
        if (
            not isinstance(max_audio_seconds, (int, float))
            or isinstance(max_audio_seconds, bool)
            or not math.isfinite(max_audio_seconds)
            or not 0 < max_audio_seconds <= 3600
        ):
            raise ValueError("whisper.cpp audio duration limit is invalid")
        self._library_path = library_path
        self._model_path = model_path
        self._cpu_threads = cpu_threads
        self._language = language
        self._max_audio_seconds = float(max_audio_seconds)
        self._library_loader = library_loader or ctypes.CDLL
        self._library: object | None = None
        self._context: ctypes.c_void_p | None = None
        self._abi: Callable[..., object] | None = None
        self._create: Callable[..., object] | None = None
        self._free: Callable[..., object] | None = None
        self._transcribe: Callable[..., object] | None = None
        self._last_error: Callable[..., object] | None = None
        self._model_lock = Lock()
        self._inference_lock = Lock()

    def load(self) -> None:
        with self._model_lock:
            if self._context is not None:
                return
            try:
                library = self._library_loader(self._library_path)
            except Exception as error:
                raise RuntimeError("whisper.cpp shim is unavailable") from error
            abi = _bind(library, "deskhelm_whisper_shim_abi", [], ctypes.c_char_p)
            create = _bind(
                library,
                "deskhelm_whisper_create",
                [ctypes.c_char_p, ctypes.c_int],
                ctypes.c_void_p,
            )
            free = _bind(
                library,
                "deskhelm_whisper_free",
                [ctypes.c_void_p],
                None,
            )
            transcribe = _bind(
                library,
                "deskhelm_whisper_transcribe",
                [
                    ctypes.c_void_p,
                    ctypes.POINTER(ctypes.c_float),
                    ctypes.c_size_t,
                    ctypes.c_char_p,
                    ctypes.POINTER(ctypes.c_int),
                    ctypes.POINTER(ctypes.c_char),
                    ctypes.c_size_t,
                ],
                ctypes.c_int,
            )
            last_error = _bind(
                library,
                "deskhelm_whisper_last_error",
                [ctypes.c_void_p],
                ctypes.c_char_p,
            )
            reported_abi = abi()
            if isinstance(reported_abi, str):
                reported_abi = reported_abi.encode("ascii", "replace")
            if reported_abi != _EXPECTED_ABI:
                raise RuntimeError("whisper.cpp shim ABI is incompatible")
            model_path = self._model_path.encode("utf-8")
            context = create(model_path, self._cpu_threads)
            if not context:
                raise RuntimeError("whisper.cpp model is unavailable")
            self._library = library
            self._context = context
            self._abi = abi
            self._create = create
            self._free = free
            self._transcribe = transcribe
            self._last_error = last_error

    def close(self) -> None:
        """Release the native context after in-flight requests have finished."""
        with self._inference_lock:
            with self._model_lock:
                if self._context is not None and self._free is not None:
                    self._free(self._context)
                self._context = None
                self._library = None
                self._abi = None
                self._create = None
                self._free = None
                self._transcribe = None
                self._last_error = None

    def transcribe(self, audio: CapturedAudio, cancel: Event) -> Transcript:
        return self.transcribe_streaming(audio, cancel).transcript

    def transcribe_streaming(
        self,
        audio: CapturedAudio,
        cancel: Event,
    ) -> StreamingAsrResult:
        if (
            audio.sample_format is not PcmSampleFormat.S16LE
            or audio.sample_rate_hz != 16_000
            or audio.channels != 1
        ):
            raise ValueError("whisper.cpp requires 16 kHz mono S16LE PCM")
        if cancel.is_set():
            raise VoiceCancelled()
        if audio.duration_seconds > self._max_audio_seconds:
            raise ValueError("whisper.cpp audio exceeds duration limit")
        samples = _pcm_to_float(audio.data, cancel)
        while not self._inference_lock.acquire(timeout=0.05):
            if cancel.is_set():
                raise VoiceCancelled()
        try:
            if cancel.is_set():
                raise VoiceCancelled()
            self.load()
            if self._context is None or self._transcribe is None:
                raise RuntimeError("whisper.cpp model is unavailable")
            abort_flag = ctypes.c_int(0)
            stop_watcher = Event()
            watcher = Thread(
                target=_watch_cancel,
                args=(cancel, stop_watcher, abort_flag),
                daemon=True,
            )
            watcher.start()
            output = ctypes.create_string_buffer(_MAX_TRANSCRIPT_BYTES)
            try:
                status = int(
                    self._transcribe(
                        self._context,
                        samples,
                        len(audio.data) // audio.sample_format.bytes_per_sample,
                        None
                        if self._language == "auto"
                        else self._language.encode("ascii"),
                        ctypes.byref(abort_flag),
                        output,
                        _MAX_TRANSCRIPT_BYTES,
                    )
                )
            finally:
                stop_watcher.set()
                watcher.join(timeout=0.2)
            if cancel.is_set() or abort_flag.value:
                raise VoiceCancelled()
            if status == _STATUS_CANCELLED:
                raise VoiceCancelled()
            if status == _STATUS_NO_TRANSCRIPT:
                raise VoiceNoTranscript()
            if status == _STATUS_OUTPUT_TOO_SMALL:
                raise RuntimeError("whisper.cpp transcript exceeds size limit")
            if status != _STATUS_OK:
                raise RuntimeError("whisper.cpp transcription failed")
            try:
                text = output.value.decode("utf-8").strip()
            except UnicodeDecodeError as error:
                raise RuntimeError("whisper.cpp returned invalid text") from error
            if not text:
                raise VoiceNoTranscript()
            if len(text) > _MAX_TRANSCRIPT_CHARS:
                raise RuntimeError("whisper.cpp transcript exceeds size limit")
            return StreamingAsrResult(
                Transcript(raw_text=text, normalized_text=text),
                None,
            )
        finally:
            self._inference_lock.release()


def _bind(
    library: object,
    name: str,
    argtypes: list[object],
    restype: object,
) -> Callable[..., object]:
    try:
        function = getattr(library, name)
    except AttributeError as error:
        raise RuntimeError("whisper.cpp shim is incomplete") from error
    function.argtypes = argtypes
    function.restype = restype
    return function


def _validate_path(value: object, name: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 4096
        or any(ord(char) < 0x20 for char in value)
    ):
        raise ValueError(f"{name} is invalid")


def _pcm_to_float(data: bytes, cancel: Event) -> ctypes.Array:
    samples = array("h")
    samples.frombytes(data)
    if samples.itemsize != 2:
        raise RuntimeError("whisper.cpp PCM conversion is unavailable")
    if sys.byteorder != "little":
        samples.byteswap()
    converted = (ctypes.c_float * len(samples))()
    for index, sample in enumerate(samples):
        if index % 4096 == 0 and cancel.is_set():
            raise VoiceCancelled()
        converted[index] = sample / 32768.0
    return converted


def _watch_cancel(cancel: Event, stop: Event, abort_flag: ctypes.c_int) -> None:
    while not stop.wait(0.02):
        if cancel.is_set():
            abort_flag.value = 1
            return

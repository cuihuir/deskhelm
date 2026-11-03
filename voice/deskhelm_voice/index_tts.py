from __future__ import annotations

from pathlib import Path
import os
import signal
import subprocess
import tempfile
import time
import wave
from threading import Event

from .models import SynthesizedAudio
from .piper_tts import _validate_text
from .providers import VoiceCancelled


class IndexTtsProvider:
    """Run the optional IndexTTS-2.5 CLI behind the provider boundary."""

    def __init__(
        self,
        model_directory: str,
        voice_path: str,
        *,
        executable: str = "indextts2",
        max_text_chars: int = 4096,
        max_output_bytes: int = 64 << 20,
        timeout_seconds: float = 120.0,
        temp_directory: str | None = None,
    ) -> None:
        if not isinstance(model_directory, str) or not model_directory:
            raise ValueError("IndexTTS model directory is invalid")
        if not isinstance(voice_path, str) or not voice_path:
            raise ValueError("IndexTTS voice path is invalid")
        if not isinstance(executable, str) or not executable:
            raise ValueError("IndexTTS executable is invalid")
        if not 1 <= max_text_chars <= 65_536:
            raise ValueError("IndexTTS text limit is invalid")
        if not 2 <= max_output_bytes <= 1 << 30:
            raise ValueError("IndexTTS output limit is invalid")
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("IndexTTS timeout is invalid")
        if temp_directory is not None:
            if not isinstance(temp_directory, str) or not temp_directory:
                raise ValueError("IndexTTS temporary directory is invalid")
            temp_path = Path(temp_directory)
            if not temp_path.is_dir() or not os.access(temp_path, os.W_OK):
                raise ValueError("IndexTTS temporary directory is unavailable")
        else:
            runtime_directory = os.environ.get("XDG_RUNTIME_DIR", "").strip()
            temp_directory = (
                runtime_directory
                if runtime_directory and Path(runtime_directory).is_dir()
                else None
            )
        self._model_directory = model_directory
        self._voice_path = voice_path
        self._executable = executable
        self._max_text_chars = max_text_chars
        self._max_output_bytes = max_output_bytes
        self._timeout_seconds = float(timeout_seconds)
        self._temp_directory = temp_directory

    def synthesize(self, text: str, cancel: Event) -> SynthesizedAudio:
        _validate_text(text, self._max_text_chars, "IndexTTS")
        if cancel.is_set():
            raise VoiceCancelled()
        output_path = None
        process = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self._temp_directory,
                suffix=".wav",
                delete=False,
            ) as output:
                output_path = Path(output.name)
            process = subprocess.Popen(
                [
                    self._executable,
                    "synth",
                    "--model-dir",
                    self._model_directory,
                    "--voice",
                    self._voice_path,
                    "--stdin",
                    "--output",
                    str(output_path),
                    "--force",
                    "--fp16",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            assert process.stdin is not None
            process.stdin.write(text.encode("utf-8"))
            process.stdin.close()
            deadline = time.monotonic() + self._timeout_seconds
            while process.poll() is None:
                if cancel.is_set():
                    _stop(process)
                    raise VoiceCancelled()
                if time.monotonic() >= deadline:
                    _stop(process)
                    raise RuntimeError("IndexTTS synthesis timed out")
                time.sleep(0.05)
            if process.returncode != 0:
                raise RuntimeError("IndexTTS synthesis failed")
            return _read_wav(output_path, self._max_output_bytes)
        except FileNotFoundError as error:
            raise RuntimeError("IndexTTS runtime is unavailable") from error
        finally:
            if process is not None and process.poll() is None:
                _stop(process)
            if output_path is not None:
                try:
                    output_path.unlink()
                except FileNotFoundError:
                    pass


def _read_wav(path: Path, max_output_bytes: int) -> SynthesizedAudio:
    try:
        with wave.open(str(path), "rb") as source:
            if source.getnchannels() != 1 or source.getsampwidth() != 2:
                raise ValueError("IndexTTS returned unsupported audio format")
            sample_rate = source.getframerate()
            data = source.readframes(source.getnframes())
    except (OSError, EOFError, wave.Error) as error:
        raise RuntimeError("IndexTTS returned invalid audio") from error
    if not data:
        raise RuntimeError("IndexTTS returned no audio")
    if len(data) > max_output_bytes:
        raise ValueError("IndexTTS output exceeds size limit")
    return SynthesizedAudio(data, sample_rate)


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGINT)
        process.wait(timeout=5.0)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5.0)

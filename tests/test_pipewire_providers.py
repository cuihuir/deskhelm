from pathlib import Path
from queue import Queue
import sys
import tempfile
from threading import Event, Thread
import time
import unittest

from voice.deskhelm_voice import (
    CapturedAudio,
    PcmSampleFormat,
    PipeWireCaptureProvider,
    PipeWirePlaybackProvider,
    SynthesizedAudio,
    VoiceCancelled,
)


ROOT = Path(__file__).resolve().parents[1]
FAKE_PW_CAT = ROOT / "tests" / "helpers" / "fake_pw_cat.py"


class PcmAudioModelTests(unittest.TestCase):
    def test_audio_requires_complete_pcm_frames_and_reports_duration(self) -> None:
        audio = CapturedAudio(
            b"\x00\x00" * 160,
            sample_rate_hz=16000,
            channels=1,
        )

        self.assertEqual(audio.sample_format, PcmSampleFormat.S16LE)
        self.assertEqual(audio.duration_seconds, 0.01)
        with self.assertRaisesRegex(ValueError, "complete PCM frames"):
            CapturedAudio(b"odd", sample_rate_hz=16000)
        with self.assertRaisesRegex(ValueError, "sample format"):
            SynthesizedAudio(
                b"\x00\x00",
                sample_rate_hz=24000,
                sample_format="s16le",
            )


class PipeWireProviderTests(unittest.TestCase):
    def test_capture_uses_default_device_and_returns_explicit_pcm(self) -> None:
        provider = PipeWireCaptureProvider(
            command_prefix=self._prefix("capture-once"),
        )

        audio = provider.capture(Event(), Event())

        self.assertEqual(len(audio.data), 640)
        self.assertEqual(audio.sample_rate_hz, 16000)
        self.assertEqual(audio.channels, 1)
        self.assertEqual(audio.sample_format, PcmSampleFormat.S16LE)
        self.assertNotIn("--target", provider.command())

    def test_capture_stream_emits_contiguous_complete_frames(self) -> None:
        provider = PipeWireCaptureProvider(
            command_prefix=self._prefix("capture-split-frame"),
        )

        with provider.open_stream() as stream:
            first = stream.read(Event(), Event())
            second = stream.read(Event(), Event())
            finished = stream.read(Event(), Event())

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        assert first is not None and second is not None
        self.assertEqual(first.start_frame, 0)
        self.assertEqual(first.end_frame, 1)
        self.assertEqual(second.start_frame, 1)
        self.assertEqual(second.end_frame, 2)
        self.assertEqual(first.format, second.format)
        self.assertIsNone(finished)

    def test_capture_manual_target_and_stop_terminate_owned_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory) / "ready"
            provider = PipeWireCaptureProvider(
                command_prefix=self._prefix(
                    "capture-hold", "deskhelm-microphone", ready
                ),
                source_name="deskhelm-microphone",
                terminate_grace_seconds=0.2,
            )
            stop = Event()
            result: Queue[object] = Queue()
            thread = Thread(
                target=lambda: self._capture_result(provider, stop, Event(), result)
            )
            thread.start()
            self._wait_for_path(ready)
            stop.set()
            thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        captured = result.get_nowait()
        if isinstance(captured, BaseException):
            raise captured
        self.assertEqual(len(captured.data), 640)
        command = provider.command()
        self.assertEqual(
            command[command.index("--target") + 1], "deskhelm-microphone"
        )

    def test_capture_cancellation_and_bounds_are_recoverable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory) / "ready"
            provider = PipeWireCaptureProvider(
                command_prefix=self._prefix(
                    "capture-hold", "__default__", ready
                ),
                terminate_grace_seconds=0.2,
            )
            cancel = Event()
            result: Queue[object] = Queue()
            thread = Thread(
                target=lambda: self._capture_result(
                    provider, Event(), cancel, result
                )
            )
            thread.start()
            self._wait_for_path(ready)
            cancel.set()
            thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertIsInstance(result.get_nowait(), VoiceCancelled)

        overflow = PipeWireCaptureProvider(
            command_prefix=self._prefix("capture-overflow"),
            max_capture_bytes=128,
        )
        with self.assertRaisesRegex(RuntimeError, "byte limit"):
            overflow.capture(Event(), Event())

    def test_capture_process_failure_does_not_expose_stderr(self) -> None:
        provider = PipeWireCaptureProvider(
            command_prefix=self._prefix("capture-failure")
        )

        with self.assertRaisesRegex(RuntimeError, "capture process failed") as caught:
            provider.capture(Event(), Event())

        self.assertNotIn("private", str(caught.exception))

    def test_capture_provider_recovers_after_process_disconnect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "first-failure"
            provider = PipeWireCaptureProvider(
                command_prefix=self._prefix(
                    "capture-failure-once", "__default__", marker
                )
            )
            with self.assertRaisesRegex(RuntimeError, "capture process failed"):
                provider.capture(Event(), Event())
            audio = provider.capture(Event(), Event())

        self.assertEqual(len(audio.data), 640)

    def test_capture_rejects_invalid_pcm_and_startup_failure(self) -> None:
        invalid_pcm = PipeWireCaptureProvider(
            command_prefix=self._prefix("capture-odd-frame")
        )
        with self.assertRaisesRegex(RuntimeError, "invalid PCM frames"):
            invalid_pcm.capture(Event(), Event())

        unavailable = PipeWireCaptureProvider(
            command_prefix=("/deskhelm/missing/pw-cat",)
        )
        with self.assertRaisesRegex(RuntimeError, "could not start"):
            unavailable.capture(Event(), Event())

        cancel = Event()
        cancel.set()
        with self.assertRaises(VoiceCancelled):
            unavailable.capture(Event(), cancel)

    def test_capture_stop_forces_kill_when_process_ignores_terminate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory) / "ready"
            provider = PipeWireCaptureProvider(
                command_prefix=self._prefix(
                    "capture-ignore-term", "__default__", ready
                ),
                poll_seconds=0.01,
                terminate_grace_seconds=0.05,
            )
            stop = Event()
            result: Queue[object] = Queue()
            thread = Thread(
                target=lambda: self._capture_result(
                    provider, stop, Event(), result
                )
            )
            thread.start()
            self._wait_for_path(ready)
            stop.set()
            thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        captured = result.get_nowait()
        if isinstance(captured, BaseException):
            raise captured
        self.assertEqual(len(captured.data), 640)

    def test_playback_uses_pcm_format_and_manual_sink(self) -> None:
        audio = self._speech_audio()
        provider = PipeWirePlaybackProvider(
            command_prefix=self._prefix(
                "playback-success", "deskhelm-speakers"
            ),
            sink_name="deskhelm-speakers",
        )

        provider.play(audio, Event())

        command = provider.command(audio)
        self.assertEqual(command[command.index("--format") + 1], "s16")
        self.assertEqual(
            command[command.index("--target") + 1], "deskhelm-speakers"
        )

        default_provider = PipeWirePlaybackProvider(
            command_prefix=self._prefix("playback-success")
        )
        self.assertNotIn("--target", default_provider.command(audio))

    def test_playback_cancellation_terminates_owned_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory) / "ready"
            provider = PipeWirePlaybackProvider(
                command_prefix=self._prefix(
                    "playback-hold", "__default__", ready
                ),
                terminate_grace_seconds=0.2,
            )
            cancel = Event()
            result: Queue[object] = Queue()
            thread = Thread(
                target=lambda: self._playback_result(
                    provider, self._speech_audio(), cancel, result
                )
            )
            thread.start()
            self._wait_for_path(ready)
            cancel.set()
            thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertIsInstance(result.get_nowait(), VoiceCancelled)

    def test_playback_rejects_bounds_and_numeric_device_ids(self) -> None:
        audio = self._speech_audio()
        provider = PipeWirePlaybackProvider(max_playback_bytes=2)
        with self.assertRaisesRegex(RuntimeError, "byte limit"):
            provider.play(audio, Event())
        with self.assertRaisesRegex(ValueError, "stable node name"):
            PipeWireCaptureProvider(source_name="43")
        with self.assertRaisesRegex(ValueError, "stable node name"):
            PipeWirePlaybackProvider(sink_name="44")

    def test_playback_reports_process_and_startup_failures_privately(self) -> None:
        audio = self._speech_audio()
        failed = PipeWirePlaybackProvider(
            command_prefix=self._prefix("playback-failure")
        )
        with self.assertRaisesRegex(RuntimeError, "process failed") as caught:
            failed.play(audio, Event())
        self.assertNotIn("private", str(caught.exception))

        unavailable = PipeWirePlaybackProvider(
            command_prefix=("/deskhelm/missing/pw-cat",)
        )
        with self.assertRaisesRegex(RuntimeError, "could not start"):
            unavailable.play(audio, Event())

    def test_playback_checks_duration_and_cancellation_before_launch(self) -> None:
        audio = self._speech_audio()
        provider = PipeWirePlaybackProvider(
            command_prefix=("/deskhelm/missing/pw-cat",),
            max_playback_seconds=0.001,
        )
        with self.assertRaisesRegex(RuntimeError, "duration limit"):
            provider.play(audio, Event())

        cancel = Event()
        cancel.set()
        with self.assertRaises(VoiceCancelled):
            PipeWirePlaybackProvider(
                command_prefix=("/deskhelm/missing/pw-cat",)
            ).play(audio, cancel)

    def test_playback_cancel_forces_kill_when_process_ignores_terminate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ready = Path(directory) / "ready"
            provider = PipeWirePlaybackProvider(
                command_prefix=self._prefix(
                    "playback-ignore-term", "__default__", ready
                ),
                poll_seconds=0.01,
                terminate_grace_seconds=0.05,
            )
            cancel = Event()
            result: Queue[object] = Queue()
            thread = Thread(
                target=lambda: self._playback_result(
                    provider, self._speech_audio(), cancel, result
                )
            )
            thread.start()
            self._wait_for_path(ready)
            cancel.set()
            thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertIsInstance(result.get_nowait(), VoiceCancelled)

    @staticmethod
    def _prefix(
        mode: str,
        expected_target: str = "__default__",
        ready_path: Path | str = "-",
    ) -> tuple[str, ...]:
        return (
            sys.executable,
            str(FAKE_PW_CAT),
            mode,
            expected_target,
            str(ready_path),
        )

    @staticmethod
    def _speech_audio() -> SynthesizedAudio:
        return SynthesizedAudio(
            b"\x00\x00" * 480,
            sample_rate_hz=24000,
        )

    @staticmethod
    def _capture_result(provider, stop, cancel, result: Queue[object]) -> None:
        try:
            result.put(provider.capture(stop, cancel))
        except BaseException as error:
            result.put(error)

    @staticmethod
    def _playback_result(provider, audio, cancel, result: Queue[object]) -> None:
        try:
            provider.play(audio, cancel)
            result.put(None)
        except BaseException as error:
            result.put(error)

    @staticmethod
    def _wait_for_path(path: Path) -> None:
        deadline = time.monotonic() + 2
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not path.exists():
            raise AssertionError("fake pw-cat did not become ready")


if __name__ == "__main__":
    unittest.main()

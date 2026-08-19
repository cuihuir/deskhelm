import json
from threading import Event
import unittest

from deskhelm_bridge.cli import build_parser
from voice.deskhelm_voice.audio_config import (
    AudioNodeKind,
    LocalAudioConfig,
    create_test_tone,
    discover_pipewire_audio,
    measure_audio_signal,
    test_audio_input as run_audio_input_test,
)
from voice.deskhelm_voice.models import CapturedAudio, PcmSampleFormat


class _CaptureProvider:
    def __init__(self, audio: CapturedAudio) -> None:
        self.audio = audio
        self.stop_seen = False

    def capture(self, stop: Event, cancel: Event) -> CapturedAudio:
        self.stop_seen = stop.wait(timeout=1)
        return self.audio


class AudioConfigTests(unittest.TestCase):
    def test_discovery_resolves_defaults_and_manual_stable_names(self) -> None:
        inventory = discover_pipewire_audio(command_runner=self._runner())

        self.assertEqual(len(inventory.sources), 2)
        self.assertEqual(len(inventory.sinks), 1)
        self.assertEqual(inventory.sources[1].kind, AudioNodeKind.SOURCE)
        defaults = LocalAudioConfig().resolve(inventory)
        self.assertTrue(defaults.source_uses_default)
        self.assertEqual(defaults.source.name, "source.usb")
        manual = LocalAudioConfig(
            source_name="source.internal",
            sink_name="sink.internal",
        ).resolve(inventory)
        self.assertFalse(manual.source_uses_default)
        self.assertEqual(manual.source.name, "source.internal")

        with self.assertRaisesRegex(ValueError, "source is unavailable"):
            LocalAudioConfig(source_name="source.missing").resolve(inventory)
        with self.assertRaisesRegex(ValueError, "stable node name"):
            LocalAudioConfig(source_name="83")
        with self.assertRaisesRegex(ValueError, "name is invalid"):
            LocalAudioConfig(source_name="source\x1b[31m")

    def test_config_creates_pipewire_providers_with_selected_targets(self) -> None:
        config = LocalAudioConfig(
            source_name="source.usb",
            sink_name="sink.internal",
            latency="30ms",
        )

        capture = config.create_capture_provider(
            max_capture_seconds=4,
            max_capture_bytes=64_000,
        )
        playback = config.create_playback_provider()

        self.assertIn("source.usb", capture.command())
        self.assertIn("30ms", capture.command())
        tone = create_test_tone()
        self.assertIn("sink.internal", playback.command(tone))

    def test_input_test_discards_pcm_and_reports_signal_only(self) -> None:
        audio = CapturedAudio(
            b"\x00\x00\x00\x40\x00\xc0\xff\x7f",
            sample_rate_hz=4,
            channels=1,
            sample_format=PcmSampleFormat.S16LE,
        )
        provider = _CaptureProvider(audio)

        report = run_audio_input_test(provider, seconds=0.1)

        self.assertTrue(provider.stop_seen)
        self.assertEqual(report.bytes_captured, 8)
        self.assertEqual(report.duration_ms, 1000)
        self.assertAlmostEqual(report.peak_fraction, 32767 / 32768)
        self.assertGreater(report.rms_fraction, 0)
        self.assertNotIn("data", report.__dataclass_fields__)
        self.assertEqual(measure_audio_signal(audio), report)

    def test_test_tone_and_cli_arguments_are_bounded_and_explicit(self) -> None:
        tone = create_test_tone(
            seconds=0.1,
            frequency_hz=440,
            level=0.1,
            sample_rate_hz=8_000,
        )
        self.assertEqual(tone.sample_rate_hz, 8_000)
        self.assertEqual(tone.duration_seconds, 0.1)
        with self.assertRaisesRegex(ValueError, "level"):
            create_test_tone(level=0.5)
        with self.assertRaisesRegex(ValueError, "duration"):
            create_test_tone(seconds=float("nan"))

        args = build_parser().parse_args(
            [
                "audio",
                "status",
                "--source",
                "source.usb",
                "--sink",
                "sink.internal",
                "--json",
            ]
        )
        self.assertEqual(args.command, "audio")
        self.assertEqual(args.audio_command, "status")
        self.assertEqual(args.source, "source.usb")
        self.assertTrue(args.json)

    def test_discovery_rejects_invalid_or_missing_default_data(self) -> None:
        def invalid_json(command: tuple[str, ...]) -> str:
            return "not-json" if command[0] == "pw-dump" else ""

        with self.assertRaisesRegex(RuntimeError, "invalid data"):
            discover_pipewire_audio(command_runner=invalid_json)

        def oversized(_command: tuple[str, ...]) -> str:
            return "x" * ((1 << 20) + 1)

        with self.assertRaisesRegex(RuntimeError, "too large"):
            discover_pipewire_audio(command_runner=oversized)

    @staticmethod
    def _runner():
        dump = json.dumps(
            [
                {
                    "info": {
                        "props": {
                            "media.class": "Audio/Source",
                            "node.name": "source.internal",
                            "node.description": "Internal Microphone",
                        }
                    }
                },
                {
                    "info": {
                        "props": {
                            "media.class": "Audio/Source/Virtual",
                            "node.name": "source.usb",
                            "node.description": "USB Microphone",
                        }
                    }
                },
                {
                    "info": {
                        "props": {
                            "media.class": "Audio/Sink",
                            "node.name": "sink.internal",
                            "node.description": "Internal Speakers",
                        }
                    }
                },
                {
                    "info": {
                        "props": {
                            "media.class": "Video/Source",
                            "node.name": "camera",
                        }
                    }
                },
            ]
        )

        def run(command: tuple[str, ...]) -> str:
            if command[0] == "pw-dump":
                return dump
            if command[-1] == "@DEFAULT_AUDIO_SOURCE@":
                return '* node.name = "source.usb"\n'
            if command[-1] == "@DEFAULT_AUDIO_SINK@":
                return '* node.name = "sink.internal"\n'
            raise AssertionError(command)

        return run


if __name__ == "__main__":
    unittest.main()

from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from deskhelm_bridge.cli import (
    _compose_local_voice,
    _print_voice_turn_timing,
    build_parser,
    main,
)
from deskhelm_bridge.interaction import InteractionKind
from deskhelm_bridge.turn_timing import VoiceAgentTurnTiming
try:
    from deskhelm_voice import (
        AudioNode,
        AudioNodeKind,
        LocalAsrProviderKind,
        LocalAudioConfig,
        LocalTtsProviderKind,
        LocalVadProviderKind,
        LocalVoiceConfig,
        KokoroTtsProvider,
        ParaformerStreamingAsrProvider,
        PipeWireAudioInventory,
        PipeWireCaptureProvider,
        PipeWirePlaybackProvider,
        PiperTtsProvider,
        WebRtcVadProvider,
    )
except ModuleNotFoundError:
    from voice.deskhelm_voice import (
        AudioNode,
        AudioNodeKind,
        LocalAsrProviderKind,
        LocalAudioConfig,
        LocalTtsProviderKind,
        LocalVadProviderKind,
        LocalVoiceConfig,
        KokoroTtsProvider,
        ParaformerStreamingAsrProvider,
        PipeWireAudioInventory,
        PipeWireCaptureProvider,
        PipeWirePlaybackProvider,
        PiperTtsProvider,
        WebRtcVadProvider,
    )


class LocalVoiceConfigTests(unittest.TestCase):
    def test_composes_lazy_provisional_providers_after_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._artifacts(Path(directory))
            config = LocalVoiceConfig(
                audio=LocalAudioConfig(
                    source_name="source.usb",
                    sink_name="sink.internal",
                    pw_cat_command_prefix=("host-spawn", "-no-pty", "pw-cat"),
                ),
                asr_provider=LocalAsrProviderKind.PARAFORMER,
                asr_model_directory=paths["asr"],
                tts_provider=LocalTtsProviderKind.PIPER,
                tts_model_path=paths["tts_model"],
                tts_config_path=paths["tts_config"],
                tts_resource_directory=paths["tts_resources"],
                cpu_threads=3,
                max_asr_seconds=7.5,
            )

            composition = config.compose(self._inventory())
            try:
                gateway = composition.gateway
                self.assertIsInstance(
                    gateway.capture_provider, PipeWireCaptureProvider
                )
                self.assertIsInstance(
                    gateway.asr_provider, ParaformerStreamingAsrProvider
                )
                self.assertIsInstance(gateway.tts_provider, PiperTtsProvider)
                self.assertIsInstance(
                    gateway.playback_provider, PipeWirePlaybackProvider
                )
                self.assertEqual(
                    gateway.capture_provider.command()[:3],
                    ("host-spawn", "-no-pty", "pw-cat"),
                )
                self.assertEqual(composition.audio_selection.source.name, "source.usb")
                self.assertIsNone(gateway.asr_provider._model)
                self.assertIsNone(gateway.tts_provider._voice)
                self.assertEqual(gateway.max_asr_seconds, 7.5)
                self.assertIsNone(gateway.vad_provider)
            finally:
                composition.gateway.close()

    def test_preflight_rejects_missing_devices_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = self._artifacts(Path(directory))
            config = LocalVoiceConfig(
                audio=LocalAudioConfig(source_name="source.missing"),
                asr_provider=LocalAsrProviderKind.PARAFORMER,
                asr_model_directory=paths["asr"],
                tts_provider=LocalTtsProviderKind.PIPER,
                tts_model_path=paths["tts_model"],
                tts_config_path=paths["tts_config"],
                tts_resource_directory=paths["tts_resources"],
            )
            with self.assertRaisesRegex(ValueError, "source is unavailable"):
                config.compose(self._inventory())

            paths["asr"].joinpath("model.pt").unlink()
            config = LocalVoiceConfig(
                audio=LocalAudioConfig(),
                asr_provider=LocalAsrProviderKind.PARAFORMER,
                asr_model_directory=paths["asr"],
                tts_provider=LocalTtsProviderKind.PIPER,
                tts_model_path=paths["tts_model"],
                tts_config_path=paths["tts_config"],
                tts_resource_directory=paths["tts_resources"],
            )
            with self.assertRaisesRegex(ValueError, "model.pt is unavailable"):
                config.compose(self._inventory())

    def test_composes_kokoro_female_voice_from_explicit_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._artifacts(root)
            kokoro = root / "kokoro"
            kokoro.mkdir()
            for name in (
                "kokoro-v1_0.pth",
                "config.json",
                "zf_xiaobei.pt",
                "af_heart.pt",
            ):
                (kokoro / name).write_bytes(b"test")
            config = LocalVoiceConfig(
                audio=LocalAudioConfig(),
                asr_provider=LocalAsrProviderKind.PARAFORMER,
                asr_model_directory=paths["asr"],
                tts_provider=LocalTtsProviderKind.KOKORO,
                tts_model_path=kokoro / "kokoro-v1_0.pth",
                tts_config_path=kokoro / "config.json",
                tts_resource_directory=kokoro,
            )

            composition = config.compose(self._inventory())
            try:
                self.assertIsInstance(
                    composition.gateway.tts_provider,
                    KokoroTtsProvider,
                )
                self.assertIsNone(composition.gateway.tts_provider._runtime)
            finally:
                composition.gateway.close()

    def test_bridge_cli_requires_explicit_paths_and_stays_disabled_by_default(self) -> None:
        parser = build_parser()
        disabled = parser.parse_args(["bridge", "--plain"])
        self.assertEqual(disabled.voice_provider, "none")

        local = parser.parse_args(["bridge", "--voice-provider", "local"])
        with self.assertRaisesRegex(ValueError, "voice-asr-model-directory"):
            _compose_local_voice(local, inventory=self._inventory())

        with tempfile.TemporaryDirectory() as directory:
            paths = self._artifacts(Path(directory))
            local = parser.parse_args(
                [
                    "bridge",
                    "--voice-provider",
                    "local",
                    "--voice-source",
                    "source.usb",
                    "--voice-asr-model-directory",
                    str(paths["asr"]),
                    "--voice-tts-model",
                    str(paths["tts_model"]),
                    "--voice-tts-config",
                    str(paths["tts_config"]),
                    "--voice-tts-resource-directory",
                    str(paths["tts_resources"]),
                    "--voice-pw-cat-command-prefix",
                    "host-spawn -no-pty pw-cat",
                    "--voice-vad-provider",
                    "webrtc",
                    "--voice-max-asr-seconds",
                    "9",
                ]
            )
            composition = _compose_local_voice(
                local,
                inventory=self._inventory(),
            )
            composition.gateway.close()
            self.assertEqual(local.voice_asr_provider, "paraformer")
            self.assertEqual(local.voice_tts_provider, "piper")
            self.assertEqual(local.voice_vad_provider, "webrtc")
            self.assertEqual(local.voice_max_asr_seconds, 9.0)
            self.assertIsInstance(
                composition.gateway.vad_provider,
                WebRtcVadProvider,
            )
            self.assertEqual(
                local.voice_pw_cat_command_prefix,
                "host-spawn -no-pty pw-cat",
            )

    def test_local_vad_configuration_is_explicit_and_validated(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["bridge", "--plain"])
        self.assertEqual(args.voice_vad_provider, "none")

        with tempfile.TemporaryDirectory() as directory:
            paths = self._artifacts(Path(directory))
            with self.assertRaisesRegex(ValueError, "VAD provider"):
                LocalVoiceConfig(
                    audio=LocalAudioConfig(),
                    asr_provider=LocalAsrProviderKind.PARAFORMER,
                    asr_model_directory=paths["asr"],
                    tts_provider=LocalTtsProviderKind.PIPER,
                    tts_model_path=paths["tts_model"],
                    tts_config_path=paths["tts_config"],
                    tts_resource_directory=paths["tts_resources"],
                    vad_provider="webrtc",
                )

    def test_bridge_main_passes_opt_in_gateway_and_closes_it(self) -> None:
        gateway = Mock()
        composition = SimpleNamespace(gateway=gateway)
        with (
            patch(
                "deskhelm_bridge.cli._compose_local_voice",
                return_value=composition,
            ) as compose,
            patch("deskhelm_bridge.cli.run_bridge", return_value=0) as run,
        ):
            result = main(["bridge", "--plain", "--voice-provider", "local"])

        self.assertEqual(result, 0)
        compose.assert_called_once()
        self.assertIs(run.call_args.kwargs["voice_gateway"], gateway)
        self.assertIsNone(run.call_args.kwargs["voice_turn_timing_sink"])
        gateway.close.assert_called_once_with()

    def test_bridge_turn_timing_is_explicit_and_requires_both_providers(self) -> None:
        parser = build_parser()
        self.assertFalse(parser.parse_args(["bridge"]).voice_turn_timing)

        with patch("deskhelm_bridge.cli.run_bridge") as run:
            result = main(["bridge", "--voice-turn-timing"])
        self.assertEqual(result, 1)
        run.assert_not_called()

        gateway = Mock()
        composition = SimpleNamespace(gateway=gateway)
        with (
            patch(
                "deskhelm_bridge.cli._compose_local_voice",
                return_value=composition,
            ),
            patch("deskhelm_bridge.cli.run_bridge", return_value=0) as run,
        ):
            result = main(
                [
                    "bridge",
                    "--plain",
                    "--agent-provider",
                    "codex",
                    "--voice-provider",
                    "local",
                    "--voice-turn-timing",
                ]
            )

        self.assertEqual(result, 0)
        self.assertIsNotNone(run.call_args.kwargs["voice_turn_timing_sink"])

    def test_turn_timing_output_contains_no_interaction_content(self) -> None:
        output = io.StringIO()
        timing = VoiceAgentTurnTiming(
            agent_id="codex",
            session_id="session-42",
            project_id="deskhelm",
            terminal_kind=InteractionKind.TASK_COMPLETED,
            release_to_transcript_ms=7.0,
            release_to_notification_ready_ms=16.0,
            release_to_agent_terminal_ms=19.5,
            release_to_speech_started_ms=17.0,
            release_to_tts_audio_ready_ms=19.0,
            release_to_playback_started_ms=20.0,
            dispatch_to_notification_ready_ms=9.0,
            dispatch_to_agent_terminal_ms=12.5,
            dispatch_to_speech_started_ms=10.0,
            dispatch_to_tts_audio_ready_ms=12.0,
            dispatch_to_playback_started_ms=13.0,
            notification_ready_to_speech_started_ms=1.0,
            speech_started_to_tts_audio_ready_ms=2.0,
            tts_audio_ready_to_playback_started_ms=1.0,
        )

        with redirect_stderr(output):
            _print_voice_turn_timing(timing)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["type"], "voice_agent_turn_timing")
        self.assertEqual(payload["dispatch_to_agent_terminal_ms"], 12.5)
        self.assertEqual(payload["dispatch_to_notification_ready_ms"], 9.0)
        self.assertEqual(payload["speech_started_to_tts_audio_ready_ms"], 2.0)
        self.assertNotIn("text", payload)
        self.assertNotIn("turn_id", payload)

    @staticmethod
    def _inventory() -> PipeWireAudioInventory:
        source = AudioNode(
            AudioNodeKind.SOURCE,
            "source.usb",
            "USB Microphone",
            "Audio/Source",
        )
        sink = AudioNode(
            AudioNodeKind.SINK,
            "sink.internal",
            "Internal Speakers",
            "Audio/Sink",
        )
        return PipeWireAudioInventory(
            sources=(source,),
            sinks=(sink,),
            default_source_name=source.name,
            default_sink_name=sink.name,
        )

    @staticmethod
    def _artifacts(root: Path) -> dict[str, Path]:
        asr = root / "paraformer"
        asr.mkdir()
        for name in ("model.pt", "config.yaml", "tokens.json", "am.mvn", "seg_dict"):
            (asr / name).write_bytes(b"test")
        resources = root / "piper"
        (resources / "g2pW").mkdir(parents=True)
        for name in (
            "g2pW/g2pw.onnx",
            "tokenizer.json",
            "tokenizer_config.json",
            "vocab.txt",
        ):
            (resources / name).write_bytes(b"test")
        model = resources / "voice.onnx"
        config = resources / "voice.onnx.json"
        model.write_bytes(b"test")
        config.write_text("{}", encoding="utf-8")
        return {
            "asr": asr,
            "tts_model": model,
            "tts_config": config,
            "tts_resources": resources,
        }


if __name__ == "__main__":
    unittest.main()

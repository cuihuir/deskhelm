import argparse
import importlib.util
from pathlib import Path
import sys
import unittest

from voice.deskhelm_voice import Transcript, VoiceEvent, VoiceEventKind, VoiceTarget


TOOL_PATH = Path("tools/run-local-voice-live.py")
SPEC = importlib.util.spec_from_file_location("run_local_voice_live", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
TOOL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = TOOL
SPEC.loader.exec_module(TOOL)


class LocalVoiceLiveToolTests(unittest.TestCase):
    def test_live_audio_requires_explicit_confirmation_and_bounds(self) -> None:
        args = argparse.Namespace(
            live_audio=False,
            capture_seconds=4.0,
            timeout_seconds=120.0,
            cpu_threads=4,
            response_text="test",
        )
        with self.assertRaisesRegex(ValueError, "live-audio"):
            TOOL._validate_args(args)

        args.live_audio = True
        args.capture_seconds = 1.0
        with self.assertRaisesRegex(ValueError, "capture duration"):
            TOOL._validate_args(args)

    def test_summary_contains_timings_without_transcript_or_pcm(self) -> None:
        target = VoiceTarget("agent", "session", "project")
        transcript = TOOL._TimedEvent(
            VoiceEvent(
                VoiceEventKind.TRANSCRIPT_READY,
                target,
                transcript=Transcript("private raw", "private normalized"),
            ),
            3_000_000_000,
        )
        events = [
            TOOL._TimedEvent(
                VoiceEvent(VoiceEventKind.PTT_STARTED, target),
                1_000_000_000,
            ),
            TOOL._TimedEvent(
                VoiceEvent(VoiceEventKind.TRANSCRIBING, target),
                2_100_000_000,
            ),
            transcript,
            TOOL._TimedEvent(
                VoiceEvent(
                    VoiceEventKind.SPEECH_STARTED,
                    target,
                    speech_id="speech",
                ),
                3_100_000_000,
            ),
            TOOL._TimedEvent(
                VoiceEvent(
                    VoiceEventKind.SPEECH_COMPLETED,
                    target,
                    speech_id="speech",
                ),
                4_000_000_000,
            ),
        ]

        summary = TOOL._summarize(
            events,
            run_started_ns=1_000_000_000,
            release_ns=2_000_000_000,
            transcript_chars=18,
            source_name="source",
            sink_name="sink",
        )

        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["release_to_transcript_ms"], 1000.0)
        self.assertEqual(summary["speech_start_to_complete_ms"], 900.0)
        self.assertEqual(summary["transcript_chars"], 18)
        serialized = TOOL.json.dumps(summary)
        self.assertNotIn("private", serialized)
        self.assertNotIn("audio_payload", serialized)
        self.assertNotIn("audio_data", serialized)


if __name__ == "__main__":
    unittest.main()

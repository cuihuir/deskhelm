from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from helpers.replay import (  # noqa: E402
    Bindings,
    ReplayMismatch,
    load_transcript,
    replay_transcript,
)


TRANSCRIPTS = TESTS / "fixtures" / "replay"


class ReplayTranscriptTests(unittest.TestCase):
    def test_every_transcript_replays_through_its_adapter(self) -> None:
        paths = sorted(TRANSCRIPTS.glob("*/*.ndjson"))
        runtimes = {path.parent.name for path in paths}

        self.assertEqual(runtimes, {"claude", "codex", "grok"})
        for path in paths:
            with self.subTest(transcript=f"{path.parent.name}/{path.name}"):
                transcript = load_transcript(path)
                self.assertEqual(transcript.header["runtime"], path.parent.name)
                replay_transcript(transcript)

    def test_a_diverging_adapter_fails_the_replay(self) -> None:
        transcript = load_transcript(TRANSCRIPTS / "grok" / "approval_allow.ndjson")
        steps = []
        for step in transcript.steps:
            frame = step.get("frame", {})
            if step["type"] == "outbound" and frame.get("id") == 31:
                frame = json.loads(json.dumps(frame).replace("allow-once", "always-allow"))
                step = {**step, "frame": frame}
            steps.append(step)

        with self.assertRaisesRegex(ReplayMismatch, "always-allow"):
            replay_transcript(replace(transcript, steps=tuple(steps)))


class BindingsTests(unittest.TestCase):
    def test_placeholders_capture_once_and_substitute(self) -> None:
        bindings = Bindings()

        bindings.match({"id": "$turn", "params": "<any>"}, {"id": 7, "params": [1]}, "frame")
        substituted = bindings.substitute({"id": "$turn", "result": {}})

        self.assertEqual(substituted, {"id": 7, "result": {}})
        with self.assertRaisesRegex(ReplayMismatch, r"\$turn was 7"):
            bindings.match({"id": "$turn"}, {"id": 8}, "frame")

    def test_missing_keys_and_changed_values_are_reported(self) -> None:
        bindings = Bindings()

        with self.assertRaisesRegex(ReplayMismatch, "missing key 'method'"):
            bindings.match({"method": "session/cancel"}, {"id": 1}, "frame")
        with self.assertRaisesRegex(ReplayMismatch, "frame.params.sessionId"):
            bindings.match(
                {"params": {"sessionId": "a"}}, {"params": {"sessionId": "b"}}, "frame"
            )


if __name__ == "__main__":
    unittest.main()

import unittest

from deskhelm_bridge.interaction import InteractionEvent, InteractionKind, TaskPayload
from deskhelm_bridge.turn_timing import VoiceAgentTurnTimer
from voice.deskhelm_voice import Transcript, VoiceEvent, VoiceEventKind, VoiceTarget


class VoiceAgentTurnTimerTests(unittest.TestCase):
    def test_correlates_release_asr_agent_and_playback_in_one_turn(self) -> None:
        clock = iter(
            (
                1_000_000,
                5_000_000,
                6_000_000,
                10_000_000,
                12_000_000,
                14_000_000,
                15_000_000,
                16_000_000,
                18_000_000,
            )
        )
        results = []
        target = VoiceTarget("codex", "session-42", "deskhelm")
        timer = VoiceAgentTurnTimer(results.append, monotonic_ns=lambda: next(clock))

        timer.mark_release(target)
        timer.observe_voice(
            VoiceEvent(
                VoiceEventKind.TRANSCRIPT_READY,
                target,
                transcript=Transcript("raw", "normalized"),
            )
        )
        timer.begin(target, "turn-1")
        timer.mark_notification_ready(target, "turn-1")
        timer.observe_voice(
            VoiceEvent(VoiceEventKind.SPEECH_STARTED, target, speech_id="turn-1")
        )
        timer.observe_voice(
            VoiceEvent(VoiceEventKind.TTS_AUDIO_READY, target, speech_id="turn-1")
        )
        timer.observe_voice(
            VoiceEvent(VoiceEventKind.PLAYBACK_STARTED, target, speech_id="turn-1")
        )
        timer.observe_voice(
            VoiceEvent(VoiceEventKind.SPEECH_COMPLETED, target, speech_id="turn-1")
        )
        timer.observe_interaction(
            InteractionEvent(
                kind=InteractionKind.TASK_COMPLETED,
                agent_id=target.agent_id,
                session_id=target.session_id,
                project_id=target.project_id,
                source="codex",
                source_version="test",
                sequence=1,
                correlation_id="turn-1",
                payload=TaskPayload(),
            )
        )

        result = results[0]
        self.assertEqual(result.release_to_transcript_ms, 4)
        self.assertEqual(result.release_to_notification_ready_ms, 9)
        self.assertEqual(result.release_to_speech_started_ms, 11)
        self.assertEqual(result.release_to_tts_audio_ready_ms, 13)
        self.assertEqual(result.release_to_playback_started_ms, 14)
        self.assertEqual(result.release_to_agent_terminal_ms, 17)
        self.assertEqual(result.notification_ready_to_speech_started_ms, 2)
        self.assertEqual(result.speech_started_to_tts_audio_ready_ms, 2)
        self.assertEqual(result.tts_audio_ready_to_playback_started_ms, 1)

    def test_emits_numeric_stages_for_the_matching_terminal_turn(self) -> None:
        clock = iter(
            (
                1_000_000,
                2_000_000,
                3_000_000,
                5_000_000,
                6_000_000,
                8_000_000,
                10_000_000,
            )
        )
        results = []
        target = VoiceTarget("codex", "session-42", "deskhelm")
        timer = VoiceAgentTurnTimer(results.append, monotonic_ns=lambda: next(clock))

        timer.begin(target, "turn-1")
        timer.mark_notification_ready(target, "turn-1")
        timer.observe_voice(
            VoiceEvent(VoiceEventKind.SPEECH_STARTED, target, speech_id="turn-1")
        )
        timer.observe_voice(
            VoiceEvent(VoiceEventKind.TTS_AUDIO_READY, target, speech_id="turn-1")
        )
        timer.observe_voice(
            VoiceEvent(VoiceEventKind.PLAYBACK_STARTED, target, speech_id="turn-1")
        )
        timer.observe_voice(
            VoiceEvent(VoiceEventKind.SPEECH_COMPLETED, target, speech_id="turn-1")
        )
        timer.observe_interaction(
            InteractionEvent(
                kind=InteractionKind.TASK_COMPLETED,
                agent_id=target.agent_id,
                session_id=target.session_id,
                project_id=target.project_id,
                source="codex",
                source_version="test",
                sequence=1,
                correlation_id="turn-1",
                payload=TaskPayload(),
            )
        )

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertIs(result.terminal_kind, InteractionKind.TASK_COMPLETED)
        self.assertEqual(result.dispatch_to_notification_ready_ms, 1)
        self.assertEqual(result.dispatch_to_speech_started_ms, 2)
        self.assertEqual(result.dispatch_to_tts_audio_ready_ms, 4)
        self.assertEqual(result.dispatch_to_playback_started_ms, 5)
        self.assertEqual(result.dispatch_to_agent_terminal_ms, 9)

    def test_waits_for_voice_terminal_when_agent_finishes_first(self) -> None:
        clock = iter(
            (1_000_000, 2_000_000, 4_000_000, 6_000_000, 8_000_000)
        )
        results = []
        target = VoiceTarget("codex", "session-42", "deskhelm")
        timer = VoiceAgentTurnTimer(results.append, monotonic_ns=lambda: next(clock))
        timer.begin(target, "turn-1")
        timer.mark_notification_ready(target, "turn-1")
        timer.observe_interaction(
            InteractionEvent(
                kind=InteractionKind.TASK_COMPLETED,
                agent_id=target.agent_id,
                session_id=target.session_id,
                project_id=target.project_id,
                source="codex",
                source_version="test",
                sequence=1,
                correlation_id="turn-1",
                payload=TaskPayload(),
            )
        )
        self.assertEqual(results, [])

        timer.observe_voice(
            VoiceEvent(VoiceEventKind.PLAYBACK_STARTED, target, speech_id="turn-1")
        )
        timer.observe_voice(
            VoiceEvent(VoiceEventKind.SPEECH_COMPLETED, target, speech_id="turn-1")
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].dispatch_to_agent_terminal_ms, 3)
        self.assertEqual(results[0].dispatch_to_playback_started_ms, 5)

    def test_ignores_unmatched_or_missing_terminal_correlation(self) -> None:
        results = []
        target = VoiceTarget("codex", "session-42", "deskhelm")
        timer = VoiceAgentTurnTimer(results.append)
        timer.begin(target, "turn-1")

        timer.observe_interaction(
            InteractionEvent(
                kind=InteractionKind.TASK_FAILED,
                agent_id=target.agent_id,
                session_id=target.session_id,
                project_id=target.project_id,
                source="codex",
                source_version="test",
                sequence=1,
                correlation_id="other-turn",
                payload=TaskPayload(message="failed", error_code="failed"),
            )
        )

        self.assertEqual(results, [])

    def test_completed_turn_without_notification_reports_missing_speech_stages(self) -> None:
        clock = iter((1_000_000, 4_000_000))
        results = []
        target = VoiceTarget("codex", "session-42", "deskhelm")
        timer = VoiceAgentTurnTimer(results.append, monotonic_ns=lambda: next(clock))
        timer.begin(target, "turn-1")

        timer.observe_interaction(
            InteractionEvent(
                kind=InteractionKind.TASK_COMPLETED,
                agent_id=target.agent_id,
                session_id=target.session_id,
                project_id=target.project_id,
                source="codex",
                source_version="test",
                sequence=1,
                correlation_id="turn-1",
                payload=TaskPayload(),
            )
        )

        self.assertEqual(len(results), 1)
        self.assertIsNone(results[0].dispatch_to_notification_ready_ms)
        self.assertIsNone(results[0].dispatch_to_tts_audio_ready_ms)


if __name__ == "__main__":
    unittest.main()

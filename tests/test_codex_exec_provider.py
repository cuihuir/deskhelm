from pathlib import Path
from threading import Event, Timer
import sys
import unittest

from adapters.codex.deskhelm_codex_adapter import (
    CodexExecProvider,
    CodexJsonMapper,
)
from deskhelm_bridge.agent_gateway import AgentRunRequest, AgentRunStatus
from deskhelm_bridge.interaction import InteractionKind
from deskhelm_bridge.session_registry import SessionKey


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "adapters" / "codex-exec-json"
FAKE_CODEX = ROOT / "tests" / "helpers" / "fake_codex_cli.py"


class CodexJsonMapperTests(unittest.TestCase):
    def test_maps_official_documentation_example(self) -> None:
        mapper = CodexJsonMapper()
        events = []
        for line in (FIXTURE_DIR / "official-basic.jsonl").read_bytes().splitlines(
            keepends=True
        ):
            events.extend(mapper.consume(line))

        self.assertEqual(
            mapper.provider_session_id,
            "0199a213-81c0-7800-8aa1-bbab2a035a53",
        )
        self.assertEqual([event.kind for event in events], [
            InteractionKind.TOOL,
            InteractionKind.MESSAGE,
        ])
        self.assertEqual(mapper.terminal_result.status, AgentRunStatus.COMPLETED)

    def test_maps_failure_and_ignores_unknown_events(self) -> None:
        failed = CodexJsonMapper()
        for line in (
            FIXTURE_DIR / "synthetic-turn-failed.jsonl"
        ).read_bytes().splitlines(keepends=True):
            failed.consume(line)
        unknown = CodexJsonMapper()
        for line in (
            FIXTURE_DIR / "synthetic-unknown.jsonl"
        ).read_bytes().splitlines(keepends=True):
            unknown.consume(line)

        self.assertEqual(failed.terminal_result.status, AgentRunStatus.FAILED)
        self.assertEqual(failed.terminal_result.error_code, "codex_error")
        self.assertEqual(unknown.terminal_result.status, AgentRunStatus.COMPLETED)

    def test_rejects_malformed_jsonl(self) -> None:
        mapper = CodexJsonMapper()

        with self.assertRaisesRegex(ValueError, "malformed"):
            mapper.consume((FIXTURE_DIR / "malformed.jsonl").read_bytes())


class CodexExecProviderTests(unittest.TestCase):
    def test_runs_fake_cli_and_keeps_prompt_out_of_process_arguments(self) -> None:
        provider = self._provider()
        request = self._request("private prompt")
        events = []

        result = provider.run(request, events.append, Event())

        self.assertEqual(result.status, AgentRunStatus.COMPLETED)
        self.assertEqual(result.provider_session_id, "provider-session-1")
        self.assertEqual(events[0].payload.text, "initial response")
        self.assertNotIn(request.prompt, provider._command(request))

    def test_resumes_known_provider_session(self) -> None:
        provider = self._provider()
        request = self._request(
            "continue", provider_session_id="provider-session-1"
        )
        events = []

        result = provider.run(request, events.append, Event())

        self.assertEqual(result.status, AgentRunStatus.COMPLETED)
        self.assertEqual(events[0].payload.text, "resumed response")
        self.assertIn("resume", provider._command(request))

    def test_reports_malformed_output_and_nonzero_exit(self) -> None:
        provider = self._provider()

        malformed = provider.run(self._request("malformed"), lambda _: None, Event())
        nonzero = provider.run(self._request("nonzero"), lambda _: None, Event())

        self.assertEqual(malformed.error_code, "invalid_jsonl")
        self.assertEqual(nonzero.error_code, "process_exit")

    def test_timeout_and_cancellation_stop_owned_process(self) -> None:
        timed_provider = self._provider(timeout_seconds=0.1)
        timed_out = timed_provider.run(
            self._request("wait"), lambda _: None, Event()
        )
        cancel = Event()
        timer = Timer(0.1, cancel.set)
        timer.start()
        try:
            cancelled = self._provider(timeout_seconds=2).run(
                self._request("wait"), lambda _: None, cancel
            )
        finally:
            timer.cancel()

        self.assertEqual(timed_out.status, AgentRunStatus.TIMED_OUT)
        self.assertEqual(cancelled.status, AgentRunStatus.CANCELLED)

    @staticmethod
    def _provider(timeout_seconds: float = 2) -> CodexExecProvider:
        return CodexExecProvider(
            command_prefix=(sys.executable, str(FAKE_CODEX)),
            timeout_seconds=timeout_seconds,
        )

    @staticmethod
    def _request(
        prompt: str, *, provider_session_id: str = ""
    ) -> AgentRunRequest:
        return AgentRunRequest(
            session=SessionKey("codex", "session-42", "deskhelm"),
            prompt=prompt,
            working_directory=ROOT,
            provider_session_id=provider_session_id,
        )


if __name__ == "__main__":
    unittest.main()

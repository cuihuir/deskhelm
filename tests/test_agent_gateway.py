from dataclasses import replace
import json
from pathlib import Path
from queue import Queue
import tempfile
import unittest

from deskhelm_bridge.agent_gateway import (
    AgentGateway,
    AgentProviderEvent,
    AgentRunResult,
    AgentRunStatus,
)
from deskhelm_bridge.control import ControlCommand
from deskhelm_bridge.control_result import ControlResultCode
from deskhelm_bridge.control_router import ControlRouter
from deskhelm_bridge.fake_agent_provider import FakeAgentProvider, FakeRunScript
from deskhelm_bridge.interaction import (
    InteractionEvent,
    InteractionKind,
    MessagePayload,
    MessagePhase,
    MessageRole,
)
from deskhelm_bridge.project_registry import ProjectWorkingDirectoryRegistry
from deskhelm_bridge.session_registry import SessionKey, SessionRegistry


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "protocol" / "control-v1"


class AgentGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = SessionKey("codex", "session-42", "deskhelm")
        self.registry = SessionRegistry(slot_count=2)
        self.registry.register(self.session, preferred_slot=0, now_ms=1786935000000)
        self.router = ControlRouter(self.registry)
        self.events: Queue[InteractionEvent] = Queue()
        self.gateways: list[AgentGateway] = []

    def tearDown(self) -> None:
        for gateway in self.gateways:
            gateway.close()

    def test_prompt_streams_response_and_reuses_provider_session(self) -> None:
        message = AgentProviderEvent(
            kind=InteractionKind.MESSAGE,
            correlation_id="message-1",
            payload=MessagePayload(
                role=MessageRole.ASSISTANT,
                phase=MessagePhase.COMPLETE,
                text="first response",
            ),
        )
        provider = FakeAgentProvider(
            [
                FakeRunScript(
                    events=(message,),
                    result=AgentRunResult(
                        AgentRunStatus.COMPLETED,
                        provider_session_id="provider-session-1",
                    ),
                ),
                FakeRunScript(result=AgentRunResult(AgentRunStatus.COMPLETED)),
            ]
        )
        gateway = self._gateway(provider)

        command = self._command("submit-prompt.json")
        result = self.router.route(command, now_ms=command.issued_at)
        first = self.events.get(timeout=1)
        completed = self.events.get(timeout=1)

        self.assertEqual(result.code, ControlResultCode.DISPATCHED)
        self.assertEqual(first.kind, InteractionKind.MESSAGE)
        self.assertEqual(first.sequence, 0)
        self.assertEqual(completed.kind, InteractionKind.TASK_COMPLETED)
        self.assertEqual(completed.sequence, 1)
        self.assertEqual(completed.correlation_id, command.command_id)

        second_command = replace(
            command,
            command_id="command-prompt-2",
            idempotency_key="prompt-session-42-2",
        )
        second_result = self.router.route(
            second_command, now_ms=command.issued_at + 1
        )
        second_completed = self.events.get(timeout=1)

        self.assertEqual(second_result.code, ControlResultCode.DISPATCHED)
        self.assertEqual(second_completed.sequence, 2)
        self.assertEqual(
            provider.requests[1].provider_session_id, "provider-session-1"
        )

    def test_interrupt_cancels_active_run_and_emits_terminal_event(self) -> None:
        provider = FakeAgentProvider([FakeRunScript(wait_for_cancel=True)])
        gateway = self._gateway(provider)
        prompt = self._command("submit-prompt.json")

        prompt_result = self.router.route(prompt, now_ms=prompt.issued_at)
        self.assertTrue(provider.started.wait(timeout=1))
        interrupt = self._command("interrupt.json")
        interrupt_result = self.router.route(
            interrupt, now_ms=interrupt.issued_at
        )
        terminal = self.events.get(timeout=1)

        self.assertEqual(prompt_result.code, ControlResultCode.DISPATCHED)
        self.assertEqual(interrupt_result.code, ControlResultCode.DISPATCHED)
        self.assertEqual(terminal.kind, InteractionKind.TASK_FAILED)
        self.assertEqual(terminal.payload.error_code, "cancelled")
        self.assertEqual(terminal.correlation_id, prompt.command_id)
        self.assertEqual(gateway.active_run_count(), 0)

    def test_failed_run_releases_session_for_next_prompt(self) -> None:
        provider = FakeAgentProvider(
            [
                FakeRunScript(
                    result=AgentRunResult(
                        AgentRunStatus.FAILED,
                        message="first run failed",
                        error_code="synthetic_failure",
                    )
                ),
                FakeRunScript(result=AgentRunResult(AgentRunStatus.COMPLETED)),
            ]
        )
        gateway = self._gateway(provider)
        first_command = self._command("submit-prompt.json")

        first_result = self.router.route(
            first_command, now_ms=first_command.issued_at
        )
        first_terminal = self.events.get(timeout=1)
        self.assertEqual(first_result.code, ControlResultCode.DISPATCHED)
        self.assertEqual(first_terminal.kind, InteractionKind.TASK_FAILED)
        self.assertEqual(first_terminal.payload.error_code, "synthetic_failure")
        self.assertEqual(gateway.active_run_count(), 0)

        second_command = replace(
            first_command,
            command_id="command-prompt-after-failure",
            idempotency_key="prompt-session-42-after-failure",
        )
        second_result = self.router.route(
            second_command, now_ms=first_command.issued_at + 1
        )
        second_terminal = self.events.get(timeout=1)

        self.assertEqual(second_result.code, ControlResultCode.DISPATCHED)
        self.assertEqual(second_terminal.kind, InteractionKind.TASK_COMPLETED)
        self.assertEqual(second_terminal.correlation_id, second_command.command_id)
        self.assertEqual(len(provider.requests), 2)

    def test_active_run_capacity_rejects_new_work_without_unbounded_queue(self) -> None:
        other = SessionKey("codex", "session-other", "deskhelm")
        self.registry.register(other, preferred_slot=1, now_ms=1786935000000)
        provider = FakeAgentProvider([FakeRunScript(wait_for_cancel=True)])
        self._gateway(provider, max_active_runs=1)
        first = self._command("submit-prompt.json")
        self.router.route(first, now_ms=first.issued_at)
        self.assertTrue(provider.started.wait(timeout=1))
        second = replace(
            first,
            command_id="command-capacity-2",
            session_id="session-other",
            idempotency_key="prompt-capacity-2",
        )

        result = self.router.route(second, now_ms=first.issued_at + 1)

        self.assertEqual(result.code, ControlResultCode.DISPATCH_FAILED)

    def test_routes_each_project_to_its_configured_working_directory(self) -> None:
        other = SessionKey("codex", "session-other", "project-b")
        self.registry.register(other, preferred_slot=1, now_ms=1786935000000)
        provider = FakeAgentProvider(
            [
                FakeRunScript(result=AgentRunResult(AgentRunStatus.COMPLETED)),
                FakeRunScript(result=AgentRunResult(AgentRunStatus.COMPLETED)),
            ]
        )
        with (
            tempfile.TemporaryDirectory() as first_directory,
            tempfile.TemporaryDirectory() as second_directory,
        ):
            registry = ProjectWorkingDirectoryRegistry.configured(
                {
                    "deskhelm": Path(first_directory),
                    "project-b": Path(second_directory),
                }
            )
            self._gateway(provider, working_directories=registry)
            first = self._command("submit-prompt.json")
            second = replace(
                first,
                command_id="command-project-b",
                session_id=other.session_id,
                project_id=other.project_id,
                idempotency_key="prompt-project-b",
            )

            self.router.route(first, now_ms=first.issued_at)
            self.events.get(timeout=1)
            self.router.route(second, now_ms=first.issued_at + 1)
            self.events.get(timeout=1)

            self.assertEqual(
                provider.requests[0].working_directory,
                Path(first_directory).resolve(),
            )
            self.assertEqual(
                provider.requests[1].working_directory,
                Path(second_directory).resolve(),
            )

    def test_unmapped_project_rejects_without_starting_provider(self) -> None:
        unmapped = SessionKey("codex", "session-unmapped", "project-unmapped")
        self.registry.register(unmapped, preferred_slot=1, now_ms=1786935000000)
        provider = FakeAgentProvider([])
        with tempfile.TemporaryDirectory() as directory:
            registry = ProjectWorkingDirectoryRegistry.configured(
                {"deskhelm": Path(directory)}
            )
            gateway = self._gateway(provider, working_directories=registry)
            command = replace(
                self._command("submit-prompt.json"),
                command_id="command-unmapped",
                session_id=unmapped.session_id,
                project_id=unmapped.project_id,
                idempotency_key="prompt-unmapped",
            )

            result = self.router.route(command, now_ms=command.issued_at)

            self.assertEqual(result.code, ControlResultCode.DISPATCH_FAILED)
            self.assertEqual(provider.requests, [])
            self.assertEqual(gateway.active_run_count(), 0)

    def _gateway(
        self,
        provider: FakeAgentProvider,
        *,
        max_active_runs: int = 2,
        working_directories: ProjectWorkingDirectoryRegistry | None = None,
    ) -> AgentGateway:
        gateway = AgentGateway(
            provider=provider,
            publish_interaction=self.events.put,
            working_directory=ROOT if working_directories is None else None,
            working_directory_registry=working_directories,
            max_active_runs=max_active_runs,
            max_session_records=4,
        )
        gateway.register_handlers(self.router)
        self.gateways.append(gateway)
        return gateway

    @staticmethod
    def _command(name: str) -> ControlCommand:
        return ControlCommand.from_dict(
            json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
        )


if __name__ == "__main__":
    unittest.main()

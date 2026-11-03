import io
import json
from pathlib import Path
from threading import Event, Semaphore, Thread
import unittest

from deskhelm_bridge.adapter_registry import AdapterRegistry
from deskhelm_bridge.control import ControlCommand, ControlKind
from deskhelm_bridge.control_result import ControlResult, ControlResultCode
from deskhelm_bridge.control_router import ControlRouter
from deskhelm_bridge.interaction import InteractionEvent
from deskhelm_bridge.interaction_subscription import InteractionHub
from deskhelm_bridge.server import _BridgeRuntime
from deskhelm_bridge.session_registry import SessionKey, SessionRegistry
from deskhelm_bridge.state_store import StateStore


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "protocol"


class BridgeControlConcurrencyTests(unittest.TestCase):
    def test_approval_request_during_a_prompt_does_not_deadlock(self) -> None:
        registry = SessionRegistry(slot_count=2)
        registry.register(
            SessionKey("codex", "session-42", "deskhelm"), now_ms=1786935000000
        )
        router = ControlRouter(session_registry=registry)
        runtime = _BridgeRuntime(
            state_store=StateStore(slot_count=2),
            session_registry=registry,
            interaction_hub=InteractionHub(),
            control_router=router,
            adapter_registry=AdapterRegistry(session_registry=registry),
            stream=io.StringIO(),
            max_events=None,
            stream_id="concurrency-test",
            max_connections=4,
            max_subscribers=2,
            subscriber_queue_frames=8,
            subscriber_permits=Semaphore(2),
        )
        approval = _interaction("approval-request.json")
        echo = _interaction("message-delta.json")
        command = _command("submit-prompt.json")
        approval_recorded = Event()
        results: list[ControlResult] = []

        def publish_approval() -> None:
            runtime.process_interaction(approval)
            approval_recorded.set()

        def submit(_: ControlCommand) -> None:
            # Before the fix the router held its lock here, while the
            # publisher held the event lock and waited for the router lock.
            Thread(target=publish_approval, daemon=True).start()
            approval_recorded.wait(timeout=2)
            runtime.process_interaction(echo)

        def route() -> None:
            results.append(router.route(command, now_ms=command.issued_at))

        router.register_handler(ControlKind.SUBMIT_PROMPT, submit)
        worker = Thread(target=route, daemon=True)
        worker.start()
        worker.join(timeout=5)

        self.assertFalse(worker.is_alive(), "control routing deadlocked")
        self.assertTrue(approval_recorded.is_set())
        self.assertEqual(results[0].code, ControlResultCode.DISPATCHED)


def _command(name: str) -> ControlCommand:
    value = json.loads((FIXTURE_DIR / "control-v1" / name).read_text(encoding="utf-8"))
    return ControlCommand.from_dict(value)


def _interaction(name: str) -> InteractionEvent:
    value = json.loads(
        (FIXTURE_DIR / "interaction-v1" / name).read_text(encoding="utf-8")
    )
    return InteractionEvent.from_dict(value)


if __name__ == "__main__":
    unittest.main()

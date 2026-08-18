from dataclasses import replace
import json
from pathlib import Path
import unittest

from deskhelm_bridge.control import ControlCommand, ControlKind
from deskhelm_bridge.control_result import (
    ControlResultCode,
    ControlResultStatus,
)
from deskhelm_bridge.control_router import ControlRouter
from deskhelm_bridge.interaction import InteractionEvent
from deskhelm_bridge.session_registry import SessionKey, SessionRegistry


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "protocol"


class ControlRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = SessionRegistry(slot_count=3)
        self.session = SessionKey(
            agent_id="codex", session_id="session-42", project_id="deskhelm"
        )
        self.registry.register(self.session, now_ms=1786935000000)
        self.router = ControlRouter(
            session_registry=self.registry,
            max_idempotency_entries=4,
            idempotency_retention_ms=100,
        )

    def test_focuses_active_target_and_deduplicates_exact_retry(self) -> None:
        command = self._command("focus.json")

        first = self.router.route(command, now_ms=command.issued_at)
        duplicate = self.router.route(command, now_ms=command.issued_at + 1)

        self.assertEqual(first.status, ControlResultStatus.ACCEPTED)
        self.assertEqual(first.code, ControlResultCode.FOCUSED)
        self.assertEqual(self.registry.focused_session(), self.session)
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(duplicate.code, ControlResultCode.FOCUSED)
        self.assertEqual(self.router.idempotency_size(), 1)

    def test_rejects_idempotency_key_reuse_with_changed_content(self) -> None:
        command = self._command("focus.json")
        self.router.route(command, now_ms=command.issued_at)
        changed = replace(command, command_id="different-command")

        result = self.router.route(changed, now_ms=command.issued_at + 1)

        self.assertEqual(result.code, ControlResultCode.IDEMPOTENCY_CONFLICT)
        self.assertFalse(result.duplicate)

    def test_rejects_expired_command_and_controller_identity_mismatch(self) -> None:
        command = self._command("focus.json")

        expired = self.router.route(command, now_ms=command.expires_at)
        mismatched = self.router.route(
            command,
            expected_issued_by="another-controller",
            now_ms=command.issued_at,
        )

        self.assertEqual(expired.code, ControlResultCode.EXPIRED)
        self.assertEqual(mismatched.code, ControlResultCode.ISSUER_MISMATCH)
        self.assertEqual(self.router.idempotency_size(), 0)

    def test_requires_registered_active_target(self) -> None:
        command = self._command("focus.json")
        missing = replace(command, session_id="missing", idempotency_key="missing")

        missing_result = self.router.route(missing, now_ms=command.issued_at)
        self.registry.disconnect(self.session, now_ms=command.issued_at)
        inactive_result = self.router.route(command, now_ms=command.issued_at)

        self.assertEqual(missing_result.code, ControlResultCode.TARGET_NOT_FOUND)
        self.assertEqual(inactive_result.code, ControlResultCode.TARGET_INACTIVE)

    def test_dispatches_to_registered_handler_and_caches_failure(self) -> None:
        command = self._command("submit-prompt.json")
        dispatched: list[ControlCommand] = []
        unregister = self.router.register_handler(
            ControlKind.SUBMIT_PROMPT, dispatched.append
        )
        try:
            result = self.router.route(command, now_ms=command.issued_at)
        finally:
            unregister()

        self.assertEqual(result.code, ControlResultCode.DISPATCHED)
        self.assertEqual(dispatched, [command])

        failure_command = replace(
            command,
            command_id="command-prompt-failure",
            idempotency_key="prompt-failure",
        )

        def fail(_: ControlCommand) -> None:
            raise RuntimeError("private handler detail")

        self.router.register_handler(ControlKind.SUBMIT_PROMPT, fail)
        failure = self.router.route(failure_command, now_ms=command.issued_at + 1)
        duplicate = self.router.route(failure_command, now_ms=command.issued_at + 2)

        self.assertEqual(failure.code, ControlResultCode.DISPATCH_FAILED)
        self.assertEqual(duplicate.code, ControlResultCode.DISPATCH_FAILED)
        self.assertTrue(duplicate.duplicate)

    def test_rejects_missing_handler_without_consuming_idempotency_capacity(self) -> None:
        command = self._command("interrupt.json")

        result = self.router.route(command, now_ms=command.issued_at)

        self.assertEqual(result.code, ControlResultCode.HANDLER_UNAVAILABLE)
        self.assertEqual(self.router.idempotency_size(), 0)

    def test_rejects_new_dispatch_when_idempotency_table_is_full(self) -> None:
        router = ControlRouter(
            session_registry=self.registry,
            max_idempotency_entries=1,
            idempotency_retention_ms=100,
        )
        first = self._command("focus.json")
        second = replace(
            first,
            command_id="command-focus-2",
            idempotency_key="focus-session-42-2",
        )

        router.route(first, now_ms=first.issued_at)
        result = router.route(second, now_ms=first.issued_at + 1)

        self.assertEqual(result.code, ControlResultCode.IDEMPOTENCY_CAPACITY)
        self.assertEqual(router.idempotency_size(), 1)

    def test_expired_idempotency_record_releases_capacity(self) -> None:
        router = ControlRouter(
            session_registry=self.registry,
            max_idempotency_entries=1,
            idempotency_retention_ms=100,
        )
        first = replace(
            self._command("focus.json"), issued_at=100, expires_at=200
        )
        second = replace(
            first,
            command_id="command-focus-after-retention",
            issued_at=210,
            expires_at=310,
            idempotency_key="focus-after-retention",
        )

        router.route(first, now_ms=100)
        result = router.route(second, now_ms=210)

        self.assertEqual(result.code, ControlResultCode.FOCUSED)
        self.assertEqual(router.idempotency_size(), 1)

    def test_validates_pending_approval_and_prevents_second_decision(self) -> None:
        event = self._interaction("approval-request.json")
        self.router.observe_interaction(event)
        dispatched: list[ControlCommand] = []
        self.router.register_handler(ControlKind.APPROVE, dispatched.append)
        command = self._command("approve.json")

        result = self.router.route(command, now_ms=command.issued_at)
        duplicate = self.router.route(command, now_ms=command.issued_at + 1)

        self.assertEqual(result.code, ControlResultCode.DISPATCHED)
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(dispatched, [command])
        self.assertEqual(self.router.pending_approval_count(), 0)

        reject = replace(
            command,
            kind=ControlKind.REJECT,
            command_id="command-reject-same-request",
            idempotency_key="approval-9-reject",
        )
        second_decision = self.router.route(reject, now_ms=command.issued_at + 2)
        self.assertEqual(
            second_decision.code, ControlResultCode.APPROVAL_ALREADY_DECIDED
        )

    def test_rejects_mismatched_approval_metadata(self) -> None:
        event = self._interaction("approval-request.json")
        self.router.observe_interaction(event)
        self.router.register_handler(ControlKind.APPROVE, lambda _: None)
        command = self._command("approve.json")

        wrong_summary = replace(
            command,
            payload=replace(command.payload, summary="Different summary"),
            idempotency_key="wrong-summary",
        )
        result = self.router.route(wrong_summary, now_ms=command.issued_at)

        self.assertEqual(result.code, ControlResultCode.APPROVAL_SUMMARY_MISMATCH)

        wrong_expiry = replace(
            command,
            expires_at=command.expires_at + 100,
            payload=replace(
                command.payload,
                request_expires_at=command.expires_at + 100,
            ),
            idempotency_key="wrong-expiry",
        )
        expiry_result = self.router.route(
            wrong_expiry, now_ms=command.issued_at
        )
        self.assertEqual(
            expiry_result.code, ControlResultCode.APPROVAL_EXPIRY_MISMATCH
        )

        other_session = SessionKey(
            agent_id="codex", session_id="other", project_id="deskhelm"
        )
        self.registry.register(other_session, preferred_slot=1, now_ms=command.issued_at)
        wrong_target = replace(
            command,
            session_id="other",
            idempotency_key="wrong-target",
        )
        target_result = self.router.route(wrong_target, now_ms=command.issued_at)
        self.assertEqual(
            target_result.code, ControlResultCode.APPROVAL_TARGET_MISMATCH
        )

    def test_failed_approval_dispatch_still_consumes_request(self) -> None:
        event = self._interaction("approval-request.json")
        self.router.observe_interaction(event)

        def fail(_: ControlCommand) -> None:
            raise RuntimeError("ambiguous downstream outcome")

        self.router.register_handler(ControlKind.APPROVE, fail)
        command = self._command("approve.json")

        failure = self.router.route(command, now_ms=command.issued_at)
        retry = replace(
            command,
            command_id="command-approve-manual-retry",
            idempotency_key="approval-9-manual-retry",
        )
        retry_result = self.router.route(retry, now_ms=command.issued_at + 1)
        self.router.observe_interaction(event)

        self.assertEqual(failure.code, ControlResultCode.DISPATCH_FAILED)
        self.assertEqual(
            retry_result.code, ControlResultCode.APPROVAL_ALREADY_DECIDED
        )
        self.assertEqual(self.router.pending_approval_count(), 0)

    def test_approval_tracking_is_bounded(self) -> None:
        router = ControlRouter(
            session_registry=self.registry,
            max_approval_records=1,
        )
        first = self._interaction("approval-request.json")
        second = replace(
            first,
            event_id="second-approval-event",
            correlation_id="approval-second",
            payload=replace(first.payload, request_id="approval-second"),
        )

        router.observe_interaction(first)
        router.observe_interaction(second)

        self.assertEqual(router.pending_approval_count(), 1)

    @staticmethod
    def _command(name: str) -> ControlCommand:
        value = json.loads(
            (FIXTURE_DIR / "control-v1" / name).read_text(encoding="utf-8")
        )
        return ControlCommand.from_dict(value)

    @staticmethod
    def _interaction(name: str) -> InteractionEvent:
        value = json.loads(
            (FIXTURE_DIR / "interaction-v1" / name).read_text(encoding="utf-8")
        )
        return InteractionEvent.from_dict(value)


if __name__ == "__main__":
    unittest.main()

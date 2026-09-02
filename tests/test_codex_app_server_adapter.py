from __future__ import annotations

from pathlib import Path
from queue import Empty, Queue
from threading import Event, Lock
import time
import unittest

from deskhelm_bridge.control import (
    ApprovalDecisionPayload,
    ControlCommand,
    ControlKind,
    SubmitPromptPayload,
)
from deskhelm_bridge.control_result import ControlResultCode
from deskhelm_bridge.control_router import ControlRouter
from deskhelm_bridge.interaction import (
    ApprovalRequestPayload,
    AttentionPayload,
    AttentionReason,
    InteractionEvent,
    InteractionKind,
    TaskPayload,
    UserInputPayload,
)
from deskhelm_bridge.session_registry import SessionKey, SessionRegistry
try:
    from deskhelm_codex_adapter import (
        CodexAppServerAdapter,
        ProxiedApprovalRequest,
    )
except ModuleNotFoundError as error:
    if error.name != "deskhelm_codex_adapter":
        raise
    from adapters.codex.deskhelm_codex_adapter import (
        CodexAppServerAdapter,
        ProxiedApprovalRequest,
    )


class _FakeAppServerTransport:
    def __init__(self) -> None:
        self.incoming: Queue[object] = Queue()
        self.sent: list[dict] = []
        self.connected = Event()
        self.connect_count = 0
        self.loaded_threads = ["thread-1"]
        self.thread_ephemeral = {"thread-1": False}
        self.resume_missing_rollout = False
        self.resume_error_message = ""
        self.resume_turn_id = ""
        self.resume_status: dict | None = None
        self._lock = Lock()

    def connect(self) -> None:
        self.connect_count += 1
        self.connected.set()

    def send_message(self, message: dict) -> None:
        with self._lock:
            self.sent.append(message)
        method = message.get("method")
        request_id = message.get("id")
        if method == "initialize":
            self.incoming.put({"id": request_id, "result": {}})
        elif method == "thread/loaded/list":
            limit = message.get("params", {}).get("limit")
            data = self.loaded_threads
            if isinstance(limit, int):
                page = data[:limit]
                next_cursor = "more" if len(data) > limit else None
            else:
                page = data
                next_cursor = None
            self.incoming.put(
                {
                    "id": request_id,
                    "result": {"data": page, "nextCursor": next_cursor},
                }
            )
        elif method == "thread/read":
            thread_id = message["params"]["threadId"]
            self.incoming.put(
                {
                    "id": request_id,
                    "result": {
                        "thread": {
                            "id": thread_id,
                            "ephemeral": self.thread_ephemeral[thread_id],
                            "turns": [],
                        }
                    },
                }
            )
        elif method == "thread/resume":
            thread_id = message["params"]["threadId"]
            if self.resume_missing_rollout:
                self.incoming.put(
                    {
                        "id": request_id,
                        "error": {
                            "code": -32600,
                            "message": (
                                "no rollout found for thread id " + thread_id
                            ),
                        },
                    }
                )
            elif self.resume_error_message:
                self.incoming.put(
                    {
                        "id": request_id,
                        "error": {
                            "code": -32600,
                            "message": self.resume_error_message,
                        },
                    }
                )
            else:
                turns = []
                if self.resume_turn_id:
                    turns.append(
                        {
                            "id": self.resume_turn_id,
                            "items": [],
                            "status": "inProgress",
                        }
                    )
                thread = {
                    "id": thread_id,
                    "turns": turns,
                }
                if self.resume_status is not None:
                    thread["status"] = self.resume_status
                self.incoming.put(
                    {
                        "id": request_id,
                        "result": {
                            "thread": thread,
                            "initialTurnsPage": {"data": turns},
                        },
                    }
                )
        elif method == "turn/start":
            self.incoming.put(
                {
                    "id": request_id,
                    "result": {
                        "turn": {
                            "id": "turn-deskhelm",
                            "items": [],
                            "status": "inProgress",
                        }
                    },
                }
            )
        elif method in {"turn/steer", "turn/interrupt"}:
            self.incoming.put({"id": request_id, "result": {}})

    def receive_message(self, timeout_seconds: float) -> dict | None:
        try:
            value = self.incoming.get(timeout=timeout_seconds)
        except Empty:
            return None
        if isinstance(value, BaseException):
            raise value
        if not isinstance(value, dict):
            raise TypeError("fake app-server message must be an object")
        return value

    def close(self) -> None:
        pass

    def inject(self, message: object) -> None:
        self.incoming.put(message)

    def messages(self, predicate) -> list[dict]:
        with self._lock:
            return [message for message in self.sent if predicate(message)]


class _FakeTuiProxy:
    def __init__(self) -> None:
        self.on_request = None
        self.on_resolved = None
        self.on_thread_resumed = None
        self.pending: set[str] = set()
        self.decisions: list[tuple[str, str]] = []
        self.closed = False

    def start(self, on_request, on_resolved, on_thread_resumed) -> None:
        self.on_request = on_request
        self.on_resolved = on_resolved
        self.on_thread_resumed = on_thread_resumed

    def inject(self, request: ProxiedApprovalRequest) -> None:
        self.pending.add(request.token)
        self.on_request(request)

    def resolve_approval(self, token: str, decision: str) -> None:
        if token not in self.pending:
            raise RuntimeError("proxy approval is no longer pending")
        self.pending.remove(token)
        self.decisions.append((token, decision))

    def resolve_from_keyboard(self, token: str) -> None:
        self.pending.remove(token)
        self.on_resolved(token)

    def confirm_resolved(self, token: str) -> None:
        self.on_resolved(token)

    def resume_thread(self, thread_id: str) -> None:
        self.on_thread_resumed(thread_id)

    def close(self) -> None:
        self.closed = True
        for token in tuple(self.pending):
            self.resolve_from_keyboard(token)


class CodexAppServerAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = SessionKey("codex", "manual-1", "deskhelm")
        self.registry = SessionRegistry(slot_count=1)
        self.registry.register(self.session, preferred_slot=0)
        self.router = ControlRouter(self.registry)
        self.transport = _FakeAppServerTransport()
        self.events: Queue[InteractionEvent] = Queue()
        self.adapter = CodexAppServerAdapter(
            socket_path=Path("/tmp/deskhelm-codex-test.sock"),
            session=self.session,
            source_version="codex-cli 0.149.0",
            transport_factory=lambda: self.transport,
            request_timeout_seconds=1,
            discovery_interval_seconds=0.01,
            reconnect_initial_seconds=0.01,
            reconnect_max_seconds=0.02,
        )
        self.adapter.start(self._publish, self.router)
        self._wait_for(lambda: self.adapter.attached_thread_id() == "thread-1")

    def tearDown(self) -> None:
        self.adapter.close()

    def test_prompt_continues_attached_thread_and_correlates_completion(self) -> None:
        command = self._prompt("continue with the tests", "prompt-1")

        result = self.router.route(command, now_ms=command.issued_at)
        self._wait_for(
            lambda: bool(
                self.transport.messages(
                    lambda message: message.get("method") == "turn/start"
                )
            )
        )
        self.transport.inject(
            {
                "method": "turn/started",
                "params": {
                    "threadId": "thread-1",
                    "turn": {
                        "id": "turn-deskhelm",
                        "items": [],
                        "status": "inProgress",
                    },
                },
            }
        )
        self.transport.inject(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {
                        "id": "turn-deskhelm",
                        "items": [],
                        "status": "completed",
                    },
                },
            }
        )
        terminal = self.events.get(timeout=1)

        self.assertEqual(result.code, ControlResultCode.DISPATCHED)
        self.assertIs(terminal.kind, InteractionKind.TASK_COMPLETED)
        self.assertEqual(terminal.correlation_id, command.command_id)
        turn_start = self.transport.messages(
            lambda message: message.get("method") == "turn/start"
        )[0]
        self.assertEqual(turn_start["params"]["threadId"], "thread-1")
        self.assertEqual(
            turn_start["params"]["input"],
            [{"type": "text", "text": "continue with the tests"}],
        )

    def test_second_prompt_starts_a_new_turn_after_first_completion(self) -> None:
        first = self._prompt("first voice turn", "first-turn")
        first_result = self.router.route(first, now_ms=first.issued_at)
        self._wait_for(
            lambda: len(
                self.transport.messages(
                    lambda message: message.get("method") == "turn/start"
                )
            )
            == 1
        )
        self.transport.inject(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {
                        "id": "turn-deskhelm",
                        "items": [],
                        "status": "completed",
                    },
                },
            }
        )
        first_terminal = self.events.get(timeout=1)

        second = self._prompt("second voice turn", "second-turn")
        second_result = self.router.route(second, now_ms=second.issued_at)
        self._wait_for(
            lambda: len(
                self.transport.messages(
                    lambda message: message.get("method") == "turn/start"
                )
            )
            == 2
        )
        self.transport.inject(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {
                        "id": "turn-deskhelm",
                        "items": [],
                        "status": "completed",
                    },
                },
            }
        )
        second_terminal = self.events.get(timeout=1)

        self.assertEqual(first_result.code, ControlResultCode.DISPATCHED)
        self.assertEqual(second_result.code, ControlResultCode.DISPATCHED)
        self.assertEqual(first_terminal.correlation_id, first.command_id)
        self.assertEqual(second_terminal.correlation_id, second.command_id)
        self.assertEqual(
            self.transport.messages(
                lambda message: message.get("method") == "turn/steer"
            ),
            [],
        )

    def test_newly_loaded_thread_attaches_before_resume(self) -> None:
        self._replace_adapter(discovery_interval_seconds=1)
        self._wait_for(
            lambda: self.adapter.attached_thread_id() == "thread-1"
        )
        resume_messages = self.transport.messages(
            lambda message: message.get("method") == "thread/resume"
        )

        self.assertEqual(resume_messages, [])
        self.assertEqual(self.adapter.subscribed_thread_id(), "")

    def test_missing_rollout_retains_attachment_until_resume_succeeds(self) -> None:
        transport = _FakeAppServerTransport()
        transport.resume_missing_rollout = True
        transport.resume_turn_id = "turn-manual"
        self._replace_adapter(transport)

        self._wait_for(
            lambda: len(
                transport.messages(
                    lambda message: message.get("method") == "thread/resume"
                )
            )
            >= 2
        )
        self.assertEqual(self.adapter.attached_thread_id(), "thread-1")
        self.assertEqual(self.adapter.subscribed_thread_id(), "")

        transport.resume_missing_rollout = False
        self._wait_for(
            lambda: self.adapter.subscribed_thread_id() == "thread-1"
        )
        transport.inject(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {
                        "id": "turn-manual",
                        "items": [],
                        "status": "completed",
                    },
                },
            }
        )
        terminal = self.events.get(timeout=1)

        self.assertIs(terminal.kind, InteractionKind.TASK_COMPLETED)
        self.assertEqual(terminal.correlation_id, "turn-manual")

    def test_first_prompt_subscribes_thread_without_rollout(self) -> None:
        transport = _FakeAppServerTransport()
        transport.resume_missing_rollout = True
        self._replace_adapter(transport)
        self._wait_for(
            lambda: bool(
                transport.messages(
                    lambda message: message.get("method") == "thread/resume"
                )
            )
        )

        command = self._prompt("continue from voice", "empty-thread")
        result = self.router.route(command, now_ms=command.issued_at)
        self._wait_for(
            lambda: self.adapter.subscribed_thread_id() == "thread-1"
        )
        transport.inject(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {
                        "id": "turn-deskhelm",
                        "items": [],
                        "status": "completed",
                    },
                },
            }
        )
        terminal = self.events.get(timeout=1)

        self.assertEqual(result.code, ControlResultCode.DISPATCHED)
        self.assertIs(terminal.kind, InteractionKind.TASK_COMPLETED)
        self.assertEqual(terminal.correlation_id, command.command_id)

    def test_explicit_thread_missing_rollout_fails_closed(self) -> None:
        transport = _FakeAppServerTransport()
        transport.resume_missing_rollout = True
        self._replace_adapter(transport, thread_id="thread-1")

        self._wait_for(lambda: transport.connect_count >= 2)
        command = self._prompt("must not dispatch", "explicit-missing")
        result = self.router.route(command, now_ms=command.issued_at)

        self.assertEqual(self.adapter.attached_thread_id(), "")
        self.assertEqual(self.adapter.subscribed_thread_id(), "")
        self.assertEqual(result.code, ControlResultCode.DISPATCH_FAILED)

    def test_ephemeral_loaded_thread_is_ignored(self) -> None:
        transport = _FakeAppServerTransport()
        transport.loaded_threads = ["thread-user", "thread-internal"]
        transport.thread_ephemeral = {
            "thread-user": False,
            "thread-internal": True,
        }
        self._replace_adapter(transport)

        self._wait_for(
            lambda: self.adapter.attached_thread_id() == "thread-user"
        )
        reads = transport.messages(
            lambda message: message.get("method") == "thread/read"
        )

        self.assertEqual(len(reads), 2)

    def test_multiple_non_ephemeral_threads_remain_ambiguous(self) -> None:
        transport = _FakeAppServerTransport()
        transport.loaded_threads = ["thread-1", "thread-2"]
        transport.thread_ephemeral = {
            "thread-1": False,
            "thread-2": False,
        }
        self._replace_adapter(transport)

        terminal = self.events.get(timeout=1)

        self.assertIs(terminal.kind, InteractionKind.TASK_FAILED)
        self.assertIsInstance(terminal.payload, TaskPayload)
        self.assertEqual(terminal.payload.error_code, "codex_thread_ambiguous")
        self.assertEqual(self.adapter.attached_thread_id(), "")

    def test_command_approval_is_fixed_summary_and_routes_exact_response(self) -> None:
        self.transport.inject(
            {
                "method": "item/commandExecution/requestApproval",
                "id": "rpc-private-command",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-manual",
                    "itemId": "item-1",
                    "startedAtMs": 1,
                    "command": "private command must not escape",
                    "reason": "private reason must not escape",
                },
            }
        )
        approval = self.events.get(timeout=1)
        self.assertIs(approval.kind, InteractionKind.APPROVAL_REQUEST)
        self.assertIsInstance(approval.payload, ApprovalRequestPayload)
        self.assertEqual(
            approval.payload.summary,
            "Codex command execution approval",
        )
        self.assertNotIn("private", approval.to_json())

        now_ms = int(time.time() * 1000)
        command = ControlCommand(
            kind=ControlKind.APPROVE,
            agent_id=self.session.agent_id,
            session_id=self.session.session_id,
            project_id=self.session.project_id,
            issued_by="test-controller",
            issued_at=now_ms,
            expires_at=approval.payload.expires_at,
            idempotency_key="approve-1",
            payload=ApprovalDecisionPayload(
                request_id=approval.payload.request_id,
                summary=approval.payload.summary,
                request_expires_at=approval.payload.expires_at,
            ),
        )

        result = self.router.route(command, now_ms=now_ms)
        self._wait_for(
            lambda: bool(
                self.transport.messages(
                    lambda message: message.get("id") == "rpc-private-command"
                    and message.get("result") == {"decision": "accept"}
                )
            )
        )

        self.assertEqual(result.code, ControlResultCode.DISPATCHED)
        self.assertEqual(self.router.pending_approval_count(), 0)

    def test_permissions_approval_grants_only_requested_turn_subset(self) -> None:
        permissions = {
            "network": {"enabled": True},
            "fileSystem": {
                "entries": [
                    {
                        "access": "write",
                        "path": {
                            "type": "special",
                            "value": {"kind": "project_roots", "subpath": None},
                        },
                    }
                ]
            },
        }
        self.transport.inject(
            {
                "method": "item/permissions/requestApproval",
                "id": "rpc-private-permissions",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-manual",
                    "itemId": "item-permissions",
                    "startedAtMs": 1,
                    "cwd": "/private/project",
                    "reason": "private permission reason",
                    "permissions": permissions,
                },
            }
        )
        approval = self.events.get(timeout=1)
        self.assertIs(approval.kind, InteractionKind.APPROVAL_REQUEST)
        self.assertEqual(approval.payload.summary, "Codex permissions approval")
        self.assertNotIn("private", approval.to_json())
        now_ms = int(time.time() * 1000)
        command = ControlCommand(
            kind=ControlKind.APPROVE,
            agent_id=self.session.agent_id,
            session_id=self.session.session_id,
            project_id=self.session.project_id,
            issued_by="voice-controller",
            issued_at=now_ms,
            expires_at=approval.payload.expires_at,
            idempotency_key="approve-permissions-1",
            payload=ApprovalDecisionPayload(
                request_id=approval.payload.request_id,
                summary=approval.payload.summary,
                request_expires_at=approval.payload.expires_at,
            ),
        )

        result = self.router.route(command, now_ms=now_ms)
        self._wait_for(
            lambda: bool(
                self.transport.messages(
                    lambda message: message.get("id")
                    == "rpc-private-permissions"
                    and message.get("result")
                    == {"permissions": permissions, "scope": "turn"}
                )
            )
        )

        self.assertEqual(result.code, ControlResultCode.DISPATCHED)
        self.assertEqual(self.router.pending_approval_count(), 0)

    def test_proxied_tui_approval_routes_voice_on_original_channel(self) -> None:
        proxy = _FakeTuiProxy()
        self._replace_adapter(tui_proxy=proxy)
        request = ProxiedApprovalRequest(
            token="proxy-approval-1",
            rpc_id="tui-rpc-private",
            method="item/commandExecution/requestApproval",
            params={
                "threadId": "thread-1",
                "turnId": "turn-manual",
                "itemId": "item-private",
                "command": "private command must not escape",
            },
        )

        proxy.inject(request)
        approval = self.events.get(timeout=1)
        self.assertIs(approval.kind, InteractionKind.APPROVAL_REQUEST)
        self.assertNotIn("private", approval.to_json())
        now_ms = int(time.time() * 1000)
        command = ControlCommand(
            kind=ControlKind.APPROVE,
            agent_id=self.session.agent_id,
            session_id=self.session.session_id,
            project_id=self.session.project_id,
            issued_by="voice-controller",
            issued_at=now_ms,
            expires_at=approval.payload.expires_at,
            idempotency_key="proxy-approve-1",
            payload=ApprovalDecisionPayload(
                request_id=approval.payload.request_id,
                summary=approval.payload.summary,
                request_expires_at=approval.payload.expires_at,
            ),
        )

        result = self.router.route(command, now_ms=now_ms)

        self.assertEqual(result.code, ControlResultCode.DISPATCHED)
        self.assertEqual(proxy.decisions, [(request.token, "accept")])
        self.assertEqual(self.router.pending_approval_count(), 0)

        proxy.confirm_resolved(request.token)

        cleared = self.events.get(timeout=1)
        self.assertEqual(
            cleared.payload,
            AttentionPayload(AttentionReason.APPROVAL, False),
        )

        second_request = ProxiedApprovalRequest(
            token="proxy-approval-2",
            rpc_id="tui-rpc-private-2",
            method="item/commandExecution/requestApproval",
            params={
                "threadId": "thread-1",
                "turnId": "turn-manual",
                "itemId": "item-private-2",
            },
        )
        proxy.inject(second_request)
        second_approval = self.events.get(timeout=1)
        self.assertIs(second_approval.kind, InteractionKind.APPROVAL_REQUEST)
        self.assertNotEqual(
            second_approval.payload.request_id,
            approval.payload.request_id,
        )

    def test_session_picker_resume_retargets_voice_prompts(self) -> None:
        proxy = _FakeTuiProxy()
        self._replace_adapter(tui_proxy=proxy)
        self._wait_for(
            lambda: self.adapter.attached_thread_id() == "thread-1"
        )

        proxy.resume_thread("thread-resumed")
        self._wait_for(
            lambda: self.adapter.subscribed_thread_id() == "thread-resumed"
        )

        result = self.router.route(self._prompt("语音新指令", "resumed"))
        self.assertEqual(result.code, ControlResultCode.DISPATCHED)
        self._wait_for(
            lambda: bool(
                self.transport.messages(
                    lambda message: message.get("method") == "turn/start"
                    and message.get("params", {}).get("threadId")
                    == "thread-resumed"
                )
            )
        )

    def test_keyboard_resolution_clears_proxied_router_approval(self) -> None:
        proxy = _FakeTuiProxy()
        self._replace_adapter(tui_proxy=proxy)
        request = ProxiedApprovalRequest(
            token="proxy-keyboard-1",
            rpc_id=101,
            method="item/fileChange/requestApproval",
            params={
                "threadId": "thread-1",
                "turnId": "turn-manual",
                "itemId": "item-private",
            },
        )
        proxy.inject(request)
        self.events.get(timeout=1)
        self.assertEqual(self.router.pending_approval_count(), 1)

        proxy.resolve_from_keyboard(request.token)

        self.assertEqual(self.router.pending_approval_count(), 0)

    def test_waiting_status_publishes_non_actionable_attention_once(self) -> None:
        waiting_status = {
            "type": "active",
            "activeFlags": ["waitingOnApproval"],
        }
        notification = {
            "method": "thread/status/changed",
            "params": {
                "threadId": "thread-1",
                "status": waiting_status,
            },
        }

        self.transport.inject(notification)
        attention = self.events.get(timeout=1)
        self.transport.inject(notification)

        self.assertIs(attention.kind, InteractionKind.ATTENTION)
        self.assertEqual(
            attention.payload,
            AttentionPayload(AttentionReason.APPROVAL, True),
        )
        self.assertEqual(self.router.pending_approval_count(), 0)
        with self.assertRaises(Empty):
            self.events.get(timeout=0.05)

        self.transport.inject(
            {
                "method": "thread/status/changed",
                "params": {
                    "threadId": "thread-1",
                    "status": {"type": "active", "activeFlags": []},
                },
            }
        )
        cleared = self.events.get(timeout=1)
        self.assertEqual(
            cleared.payload,
            AttentionPayload(AttentionReason.APPROVAL, False),
        )

    def test_resume_reports_existing_waiting_approval(self) -> None:
        transport = _FakeAppServerTransport()
        transport.resume_status = {
            "type": "active",
            "activeFlags": ["waitingOnApproval"],
        }
        self._replace_adapter(transport)

        attention = self.events.get(timeout=1)

        self.assertIs(attention.kind, InteractionKind.ATTENTION)
        self.assertEqual(
            attention.payload,
            AttentionPayload(AttentionReason.APPROVAL, True),
        )
        self.assertEqual(self.router.pending_approval_count(), 0)

    def test_single_non_secret_user_input_uses_next_prompt_response(self) -> None:
        self.transport.inject(
            {
                "method": "turn/started",
                "params": {
                    "threadId": "thread-1",
                    "turn": {
                        "id": "turn-manual",
                        "items": [],
                        "status": "inProgress",
                    },
                },
            }
        )
        self.transport.inject(
            {
                "method": "item/tool/requestUserInput",
                "id": 77,
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-manual",
                    "itemId": "input-1",
                    "isBlocking": True,
                    "autoResolutionMs": None,
                    "questions": [
                        {
                            "id": "choice",
                            "header": "Private header",
                            "question": "Private question",
                            "isSecret": False,
                            "options": None,
                        }
                    ],
                },
            }
        )
        input_event = self.events.get(timeout=1)
        self.assertIs(input_event.kind, InteractionKind.USER_INPUT_REQUIRED)
        self.assertIsInstance(input_event.payload, UserInputPayload)
        self.assertEqual(input_event.payload.prompt, "Codex requested user input")
        self.assertNotIn("Private", input_event.to_json())

        command = self._prompt("use option one", "input-answer")
        result = self.router.route(command, now_ms=command.issued_at)
        self._wait_for(
            lambda: bool(
                self.transport.messages(
                    lambda message: message.get("id") == 77
                    and message.get("result")
                    == {
                        "answers": {
                            "choice": {"answers": ["use option one"]}
                        }
                    }
                )
            )
        )

        self.assertEqual(result.code, ControlResultCode.DISPATCHED)
        self.assertEqual(self.adapter.pending_user_input_count(), 0)

    def test_file_rejection_uses_decline_without_private_summary(self) -> None:
        self.transport.inject(
            {
                "method": "item/fileChange/requestApproval",
                "id": 91,
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-manual",
                    "itemId": "file-1",
                    "startedAtMs": 1,
                    "reason": "private file reason",
                    "grantRoot": "/private/path",
                },
            }
        )
        approval = self.events.get(timeout=1)
        self.assertEqual(approval.payload.summary, "Codex file change approval")
        self.assertNotIn("private", approval.to_json())
        now_ms = int(time.time() * 1000)
        command = ControlCommand(
            kind=ControlKind.REJECT,
            agent_id=self.session.agent_id,
            session_id=self.session.session_id,
            project_id=self.session.project_id,
            issued_by="test-controller",
            issued_at=now_ms,
            expires_at=approval.payload.expires_at,
            idempotency_key="reject-file-1",
            payload=ApprovalDecisionPayload(
                request_id=approval.payload.request_id,
                summary=approval.payload.summary,
                request_expires_at=approval.payload.expires_at,
            ),
        )

        result = self.router.route(command, now_ms=now_ms)
        self._wait_for(
            lambda: bool(
                self.transport.messages(
                    lambda message: message.get("id") == 91
                    and message.get("result") == {"decision": "decline"}
                )
            )
        )

        self.assertEqual(result.code, ControlResultCode.DISPATCHED)

    def test_connection_loss_reinitializes_and_resumes_known_thread(self) -> None:
        initial_connects = self.transport.connect_count

        self.transport.inject(ConnectionError("synthetic disconnect"))

        self._wait_for(
            lambda: self.transport.connect_count > initial_connects
            and self.adapter.attached_thread_id() == "thread-1"
        )
        self.assertGreater(self.transport.connect_count, initial_connects)
        resume_messages = self.transport.messages(
            lambda message: message.get("method") == "thread/resume"
        )
        self.assertGreaterEqual(len(resume_messages), 1)

    def test_disconnect_invalidates_direct_approval_then_recovers_thread(self) -> None:
        self.transport.inject(
            {
                "method": "item/fileChange/requestApproval",
                "id": "disconnect-approval",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-before-disconnect",
                    "itemId": "item-before-disconnect",
                    "startedAtMs": 1,
                    "reason": "private reason",
                    "grantRoot": "/private/path",
                },
            }
        )
        approval = self.events.get(timeout=1)
        self.assertIs(approval.kind, InteractionKind.APPROVAL_REQUEST)
        self.assertEqual(self.router.pending_approval_count(), 1)

        initial_connects = self.transport.connect_count
        self.transport.inject(ConnectionError("synthetic disconnect"))
        self._wait_for(
            lambda: self.transport.connect_count > initial_connects
            and self.adapter.attached_thread_id() == "thread-1"
        )
        self.assertEqual(self.router.pending_approval_count(), 0)

        now_ms = int(time.time() * 1000)
        stale_decision = ControlCommand(
            kind=ControlKind.APPROVE,
            agent_id=self.session.agent_id,
            session_id=self.session.session_id,
            project_id=self.session.project_id,
            issued_by="test-controller",
            issued_at=now_ms,
            expires_at=approval.payload.expires_at,
            idempotency_key="approval-after-disconnect",
            payload=ApprovalDecisionPayload(
                request_id=approval.payload.request_id,
                summary=approval.payload.summary,
                request_expires_at=approval.payload.expires_at,
            ),
        )
        rejected = self.router.route(stale_decision, now_ms=now_ms)
        self.assertEqual(
            rejected.code,
            ControlResultCode.APPROVAL_ALREADY_DECIDED,
        )

        prompt = self._prompt("continue after reconnect", "after-disconnect")
        accepted = self.router.route(prompt, now_ms=prompt.issued_at)
        self._wait_for(
            lambda: bool(
                self.transport.messages(
                    lambda message: message.get("method") == "turn/start"
                    and message.get("params", {}).get("threadId") == "thread-1"
                )
            )
        )
        self.transport.inject(
            {
                "method": "turn/started",
                "params": {
                    "threadId": "thread-1",
                    "turn": {
                        "id": "turn-deskhelm",
                        "items": [],
                        "status": "inProgress",
                    },
                },
            }
        )
        self.transport.inject(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {
                        "id": "turn-deskhelm",
                        "items": [],
                        "status": "completed",
                    },
                },
            }
        )
        deadline = time.monotonic() + 1
        terminal = None
        while time.monotonic() < deadline:
            try:
                event = self.events.get(timeout=0.1)
            except Empty:
                continue
            if event.correlation_id == prompt.command_id and event.kind in {
                InteractionKind.TASK_COMPLETED,
                InteractionKind.TASK_FAILED,
            }:
                terminal = event
                break

        self.assertEqual(accepted.code, ControlResultCode.DISPATCHED)
        self.assertIsNotNone(terminal)
        assert terminal is not None
        self.assertIs(terminal.kind, InteractionKind.TASK_COMPLETED)
        self.assertEqual(terminal.correlation_id, prompt.command_id)

    def test_secret_user_input_stays_in_tui(self) -> None:
        self.transport.inject(
            {
                "method": "item/tool/requestUserInput",
                "id": "secret-request",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-manual",
                    "itemId": "input-secret",
                    "isBlocking": True,
                    "questions": [
                        {
                            "id": "secret",
                            "header": "Secret",
                            "question": "Enter secret",
                            "isSecret": True,
                            "options": None,
                        }
                    ],
                },
            }
        )
        self.events.get(timeout=1)
        command = self._prompt("do not send this", "secret-answer")

        result = self.router.route(command, now_ms=command.issued_at)

        self.assertEqual(result.code, ControlResultCode.DISPATCH_FAILED)
        self.assertEqual(self.adapter.pending_user_input_count(), 1)
        self.assertEqual(
            self.transport.messages(
                lambda message: message.get("id") == "secret-request"
                and "result" in message
            ),
            [],
        )

    def _prompt(self, text: str, suffix: str) -> ControlCommand:
        now_ms = int(time.time() * 1000)
        return ControlCommand(
            command_id=f"command-{suffix}",
            kind=ControlKind.SUBMIT_PROMPT,
            agent_id=self.session.agent_id,
            session_id=self.session.session_id,
            project_id=self.session.project_id,
            issued_by="test-controller",
            issued_at=now_ms,
            expires_at=now_ms + 10_000,
            idempotency_key=f"idempotency-{suffix}",
            payload=SubmitPromptPayload(text),
        )

    def _replace_adapter(
        self,
        transport: _FakeAppServerTransport | None = None,
        *,
        thread_id: str = "",
        discovery_interval_seconds: float = 0.01,
        tui_proxy: _FakeTuiProxy | None = None,
    ) -> None:
        self.adapter.close()
        self.transport = transport or _FakeAppServerTransport()
        self.events = Queue()
        self.adapter = CodexAppServerAdapter(
            socket_path=Path("/tmp/deskhelm-codex-test.sock"),
            session=self.session,
            source_version="codex-cli 0.150.1",
            thread_id=thread_id,
            transport_factory=lambda: self.transport,
            tui_proxy_factory=(
                (lambda: tui_proxy) if tui_proxy is not None else None
            ),
            request_timeout_seconds=1,
            discovery_interval_seconds=discovery_interval_seconds,
            reconnect_initial_seconds=0.01,
            reconnect_max_seconds=0.02,
        )
        self.adapter.start(self._publish, self.router)

    def _publish(self, event: InteractionEvent) -> None:
        self.router.observe_interaction(event)
        self.events.put(event)

    @staticmethod
    def _wait_for(predicate) -> None:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        raise AssertionError("condition was not met")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from pathlib import Path
from queue import Empty, Queue
import sys
from threading import Event, Lock
import json
import time
import unittest

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))
CODEX = TESTS.parent / "adapters" / "codex"
if str(CODEX) not in sys.path:
    sys.path.insert(0, str(CODEX))

from deskhelm_codex_adapter.controls import (  # noqa: E402
    ApprovalDecisionPayload,
    ConfigureSessionPayload,
    ControlCommand,
    ControlKind,
    SessionKey,
    SubmitPromptPayload,
)
from deskhelm_codex_adapter.events import (  # noqa: E402
    ApprovalRequestPayload,
    AttentionPayload,
    AttentionReason,
    InteractionEvent,
    InteractionKind,
    MessagePhase,
    MessageRole,
    TaskPayload,
    ToolPhase,
    ToolStream,
    UserInputPayload,
)
from helpers.control_host import ControlResultCode, FakeControlHost  # noqa: E402
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
try:
    from deskhelm_codex_adapter.app_server import (
        MAX_ITEM_TEXT_CHARS,
        _thread_item_drafts,
    )
except ModuleNotFoundError as error:
    if error.name not in {
        "deskhelm_codex_adapter",
        "deskhelm_codex_adapter.app_server",
    }:
        raise
    from adapters.codex.deskhelm_codex_adapter.app_server import (
        MAX_ITEM_TEXT_CHARS,
        _thread_item_drafts,
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
        self.thread_cwd = "/tmp/deskhelm-catalog"
        self.thread_model = "gpt-6.1-sol"
        self.steer_error = ""
        self.fail_methods: set[str] = set()
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
                            "cwd": self.thread_cwd,
                            "model": self.thread_model,
                            "turns": [],
                        }
                    },
                }
            )
        elif method == "model/list":
            self._catalog_reply(request_id, method, self._model_list_result())
        elif method == "mcpServerStatus/list":
            self._catalog_reply(request_id, method, self._mcp_result())
        elif method == "skills/list":
            self._catalog_reply(request_id, method, self._skills_result())
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
        elif method == "turn/steer":
            if self.steer_error:
                self.incoming.put(
                    {
                        "id": request_id,
                        "error": {
                            "code": -32600,
                            "message": self.steer_error,
                        },
                    }
                )
            else:
                self.incoming.put({"id": request_id, "result": {}})
        elif method == "turn/interrupt":
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

    def _catalog_reply(self, request_id: object, method: str, result: dict) -> None:
        if method in self.fail_methods:
            self.incoming.put(
                {
                    "id": request_id,
                    "error": {"code": -32000, "message": "private catalog error"},
                }
            )
            return
        self.incoming.put({"id": request_id, "result": result})

    def _model_list_result(self) -> dict:
        return {
            "data": [
                {
                    "id": "hidden-model",
                    "model": "hidden-model",
                    "hidden": True,
                    "displayName": "Hidden",
                    "isDefault": False,
                    "defaultReasoningEffort": "low",
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "low", "description": "hidden effort"}
                    ],
                },
                {
                    "id": "gpt-6.1-sol",
                    "model": "gpt-6.1-sol",
                    "hidden": False,
                    "displayName": "Sol",
                    "isDefault": True,
                    "defaultReasoningEffort": "medium",
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "low", "description": "faster"},
                        {"reasoningEffort": "medium", "description": "balanced"},
                    ],
                },
            ],
            "nextCursor": "more",
        }

    def _mcp_result(self) -> dict:
        return {
            "data": [
                {
                    "name": "docs",
                    "runtimeStatus": "connected",
                    "authStatus": "oAuth",
                    "tools": {
                        "search": {
                            "inputSchema": {
                                "type": "object",
                                "secret": "do-not-leak",
                            }
                        },
                        "read": {},
                    },
                    "resources": [{"uri": "file:///secret"}],
                    "resourceTemplates": [],
                    "httpOrigin": "https://example.invalid",
                    "toolsError": "private tool error",
                }
            ],
            "nextCursor": None,
        }

    def _skills_result(self) -> dict:
        return {
            "data": [
                {
                    "cwd": self.thread_cwd,
                    "errors": ["private skill error"],
                    "skills": [
                        {
                            "name": "review",
                            "description": "long description that stays internal",
                            "shortDescription": "Review a change",
                            "path": "/home/secret/skills/review/SKILL.md",
                            "scope": "repo",
                            "enabled": True,
                        }
                    ],
                }
            ]
        }

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
        self.router = FakeControlHost()
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
        terminal = self._take(InteractionKind.TASK_COMPLETED)

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
        self.assertNotIn("model", turn_start["params"])
        self.assertNotIn("effort", turn_start["params"])

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
        first_terminal = self._take(InteractionKind.TASK_COMPLETED)

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
        second_terminal = self._take(InteractionKind.TASK_COMPLETED)

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

    def test_first_prompt_keeps_resuming_until_the_thread_is_subscribed(self) -> None:
        # Starting a turn does not subscribe this connection (Codex 0.166.0),
        # so resume retries go on until the first message creates a rollout.
        transport = _FakeAppServerTransport()
        transport.resume_missing_rollout = True
        self._replace_adapter(transport)

        def resumes() -> int:
            return len(
                transport.messages(
                    lambda message: message.get("method") == "thread/resume"
                )
            )

        self._wait_for(lambda: resumes() > 0)
        command = self._prompt("continue from voice", "empty-thread")
        result = self.router.route(command, now_ms=command.issued_at)
        self._wait_for(
            lambda: bool(
                transport.messages(
                    lambda message: message.get("method") == "turn/start"
                )
            )
        )
        after_prompt = resumes()
        self._wait_for(lambda: resumes() > after_prompt)
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
                        "id": "turn-deskhelm",
                        "items": [],
                        "status": "completed",
                    },
                },
            }
        )
        terminal = self._take(InteractionKind.TASK_COMPLETED)

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
        self.assertEqual(result.code, ControlResultCode.HANDLER_UNAVAILABLE)

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

    def test_with_a_tui_proxy_the_direct_copy_of_an_approval_is_ignored(self) -> None:
        # Codex sends one request to every subscribed connection.
        proxy = _FakeTuiProxy()
        self._replace_adapter(tui_proxy=proxy)
        # A request for a thread not yet attached is refused; that is not
        # the case under test.
        self._wait_for(lambda: self.adapter.attached_thread_id() == "thread-1")
        params = {"threadId": "thread-1", "turnId": "turn-manual", "itemId": "item-1"}
        self.transport.inject(
            {"method": "item/commandExecution/requestApproval", "id": 0, "params": params}
        )
        proxy.inject(
            ProxiedApprovalRequest(
                token="proxy-approval-1",
                rpc_id=0,
                method="item/commandExecution/requestApproval",
                params=params,
            )
        )

        approval = self._take(InteractionKind.APPROVAL_REQUEST)
        self._wait_for(lambda: self.transport.incoming.empty())
        time.sleep(0.05)

        self.assertEqual(self.router.pending_approval_count(), 1)
        self.assertEqual(self.transport.messages(lambda message: message.get("id") == 0), [])
        self.assertFalse(
            any(
                event.kind is InteractionKind.APPROVAL_REQUEST
                for event in self._pending_events()
            )
        )
        self.assertTrue(approval.payload.request_id)

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

    def test_catalog_lists_model_mcp_and_skills_without_private_fields(self) -> None:
        catalog = self.adapter.session_catalog(
            self.session.agent_id,
            self.session.session_id,
            self.session.project_id,
        )

        self.assertTrue(catalog["available"])
        self.assertEqual(catalog["notice"], "")
        self.assertEqual(catalog["model"], "gpt-6.1-sol")
        self.assertEqual(catalog["reasoning_effort"], "")
        self.assertEqual([item["id"] for item in catalog["models"]], ["gpt-6.1-sol"])
        self.assertEqual(catalog["models"][0]["default_effort"], "medium")
        self.assertEqual(catalog["mcp_servers"][0]["name"], "docs")
        self.assertEqual(catalog["mcp_servers"][0]["status"], "connected")
        self.assertEqual(catalog["mcp_servers"][0]["auth"], "oAuth")
        self.assertEqual(catalog["mcp_servers"][0]["tools"], ["search", "read"])
        self.assertEqual(catalog["skills"][0]["name"], "review")
        self.assertEqual(catalog["skills"][0]["description"], "Review a change")
        self.assertEqual(catalog["skills"][0]["scope"], "repo")
        self.assertNotIn("path", catalog["skills"][0])
        self.assertTrue(catalog["truncated"])
        blob = json.dumps(catalog)
        for private in (
            "do-not-leak",
            "SKILL.md",
            "private tool error",
            "private skill error",
            "/tmp/deskhelm-catalog",
            "file:///secret",
            "example.invalid",
            "hidden-model",
        ):
            self.assertNotIn(private, blob)
        self.assertNotIn("cwd", catalog)
        model_request = self.transport.messages(
            lambda message: message.get("method") == "model/list"
        )[-1]
        self.assertEqual(
            model_request["params"],
            {"limit": 40, "includeHidden": False},
        )
        mcp_request = self.transport.messages(
            lambda message: message.get("method") == "mcpServerStatus/list"
        )[-1]
        self.assertEqual(
            mcp_request["params"],
            {
                "limit": 24,
                "detail": "toolsAndAuthOnly",
                "threadId": "thread-1",
            },
        )
        skill_request = self.transport.messages(
            lambda message: message.get("method") == "skills/list"
        )[-1]
        self.assertEqual(skill_request["params"]["cwds"], ["/tmp/deskhelm-catalog"])
        self.assertIs(skill_request["params"]["forceReload"], False)
        other = self.adapter.session_catalog("codex", "other-session", "deskhelm")
        self.assertEqual(other["notice"], "catalog_unavailable")
        self.assertFalse(other["available"])

    def test_skill_lookup_skips_a_control_character_working_directory(self) -> None:
        self.transport.thread_cwd = "bad\npath"

        catalog = self.adapter.session_catalog(
            self.session.agent_id,
            self.session.session_id,
            self.session.project_id,
        )

        self.assertTrue(catalog["available"])
        self.assertEqual(catalog["skills_notice"], "unavailable")
        self.assertEqual(catalog["skills"], [])
        self.assertEqual(catalog["models_notice"], "")
        self.assertFalse(
            self.transport.messages(
                lambda message: message.get("method") == "skills/list"
            )
        )
        self.assertNotIn("bad", json.dumps(catalog))

    def test_catalog_section_failure_does_not_fail_the_turn(self) -> None:
        self.transport.fail_methods.add("skills/list")

        catalog = self.adapter.session_catalog(
            self.session.agent_id,
            self.session.session_id,
            self.session.project_id,
        )

        self.assertTrue(catalog["available"])
        self.assertEqual(catalog["skills_notice"], "unavailable")
        self.assertEqual(catalog["models"][0]["id"], "gpt-6.1-sol")
        self.assertEqual(catalog["mcp_notice"], "")
        self.assertFalse(self._task_failures())

    def test_model_override_applies_on_the_next_new_turn_only(self) -> None:
        stale = self._configure("missing-model", "low", "stale-model")
        stale_result = self.router.route(stale, now_ms=stale.issued_at)
        self.assertEqual(stale_result.code, ControlResultCode.DISPATCH_FAILED)

        catalog = self.adapter.session_catalog(
            self.session.agent_id,
            self.session.session_id,
            self.session.project_id,
        )
        self.assertTrue(catalog["available"])
        unknown = self._configure("not-a-model", "medium", "unknown-model")
        unknown_result = self.router.route(unknown, now_ms=unknown.issued_at)
        self.assertEqual(unknown_result.code, ControlResultCode.DISPATCH_FAILED)

        chosen = self._configure("gpt-6.1-sol", "medium", "chosen-model")
        chosen_result = self.router.route(chosen, now_ms=chosen.issued_at)
        self.assertEqual(chosen_result.code, ControlResultCode.DISPATCHED)
        command = self._prompt("use the selected model", "catalog-turn")
        self.router.route(command, now_ms=command.issued_at)
        self._wait_for(
            lambda: bool(
                self.transport.messages(
                    lambda message: message.get("method") == "turn/start"
                )
            )
        )
        turn_start = self.transport.messages(
            lambda message: message.get("method") == "turn/start"
        )[-1]
        self.assertEqual(turn_start["params"]["model"], "gpt-6.1-sol")
        self.assertEqual(turn_start["params"]["effort"], "medium")
        self._wait_for(lambda: self.transport.incoming.empty())
        time.sleep(0.05)
        steered = self._prompt("keep the current turn", "catalog-steer")
        self.router.route(steered, now_ms=steered.issued_at)
        self._wait_for(
            lambda: bool(
                self.transport.messages(
                    lambda message: message.get("method") == "turn/steer"
                )
            )
        )
        steer = self.transport.messages(
            lambda message: message.get("method") == "turn/steer"
        )[-1]
        self.assertNotIn("model", steer["params"])
        self.assertNotIn("effort", steer["params"])
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
        self._wait_for(lambda: self.transport.incoming.empty())
        time.sleep(0.05)
        cleared = self._configure("", "", "clear-model")
        cleared_result = self.router.route(cleared, now_ms=cleared.issued_at)
        self.assertEqual(cleared_result.code, ControlResultCode.DISPATCHED)
        again = self._prompt("back to the thread model", "catalog-cleared")
        self.router.route(again, now_ms=again.issued_at)
        self._wait_for(
            lambda: len(
                self.transport.messages(
                    lambda message: message.get("method") == "turn/start"
                )
            )
            >= 2
        )
        cleared_start = self.transport.messages(
            lambda message: message.get("method") == "turn/start"
        )[-1]
        self.assertNotIn("model", cleared_start["params"])
        self.assertNotIn("effort", cleared_start["params"])

    def test_steer_with_no_active_turn_starts_one_new_turn(self) -> None:
        self._start_remembered_turn("first prompt", "idle-first")
        self.transport.steer_error = "no active turn to steer"
        self._drain_events()
        again = self._prompt("next prompt", "idle-next")
        self.router.route(again, now_ms=again.issued_at)
        self._wait_for(
            lambda: len(
                self.transport.messages(
                    lambda message: message.get("method") == "turn/start"
                )
            )
            >= 2
        )
        started = self.transport.messages(
            lambda message: message.get("method") == "turn/start"
        )
        self.assertEqual(started[-1]["params"]["input"][0]["text"], "next prompt")
        self.assertEqual(
            len(
                self.transport.messages(
                    lambda message: message.get("method") == "turn/steer"
                )
            ),
            1,
        )
        self._wait_for(lambda: self.transport.incoming.empty())
        time.sleep(0.05)
        self.assertEqual(
            len(
                self.transport.messages(
                    lambda message: message.get("method") == "turn/start"
                )
            ),
            2,
        )
        self.assertFalse(self._task_failures())

    def test_other_steer_errors_do_not_start_a_turn(self) -> None:
        self._start_remembered_turn("first prompt", "review-first")
        self.transport.steer_error = "cannot steer a review turn"
        self._drain_events()
        starts_before = len(
            self.transport.messages(
                lambda message: message.get("method") == "turn/start"
            )
        )
        again = self._prompt("next prompt", "review-next")
        self.router.route(again, now_ms=again.issued_at)
        failed = self._wait_for_task_failure()
        self.assertEqual(failed.payload.error_code, "codex_control_failed")
        self.assertEqual(
            len(
                self.transport.messages(
                    lambda message: message.get("method") == "turn/start"
                )
            ),
            starts_before,
        )

    def test_submitted_prompt_is_one_user_message(self) -> None:
        first = self._prompt("first instruction", "user-first")
        self.router.route(first, now_ms=first.issued_at)
        self._wait_for(
            lambda: bool(
                self.transport.messages(
                    lambda message: message.get("method") == "turn/start"
                )
            )
        )
        self._wait_for(lambda: self.transport.incoming.empty())
        time.sleep(0.05)
        first_message = self._take(InteractionKind.MESSAGE)
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
        self._wait_for(lambda: self.transport.incoming.empty())
        time.sleep(0.05)
        second = self._prompt("second instruction", "user-second")
        self.router.route(second, now_ms=second.issued_at)
        second_message = self._take(InteractionKind.MESSAGE)

        self.assertEqual(first_message.correlation_id, first.command_id)
        self.assertEqual(second_message.correlation_id, second.command_id)
        self.assertIs(first_message.payload.role, MessageRole.USER)
        self.assertEqual(first_message.payload.phase, MessagePhase.COMPLETE)
        self.assertEqual(first_message.payload.text, "first instruction")
        self.assertEqual(second_message.payload.text, "second instruction")
        self.assertNotEqual(
            first_message.correlation_id,
            second_message.correlation_id,
        )

        self.transport.inject(
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thread-1",
                    "item": {
                        "id": "vendor-echo",
                        "type": "userMessage",
                        "content": [{"type": "text", "text": "second instruction"}],
                    },
                },
            }
        )
        self.transport.inject(
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thread-1",
                    "item": {
                        "id": "typed-in-tui",
                        "type": "userMessage",
                        "content": [{"type": "text", "text": "typed in the terminal"}],
                    },
                },
            }
        )
        echoed = self._take(InteractionKind.MESSAGE)
        self._wait_for(lambda: self.transport.incoming.empty())
        time.sleep(0.05)

        self.assertEqual(echoed.payload.text, "typed in the terminal")
        self.assertEqual(echoed.correlation_id, "typed-in-tui")
        self.assertFalse(
            any(
                event.kind is InteractionKind.MESSAGE
                for event in self._pending_events()
            )
        )

    def test_a_started_and_completed_user_item_is_one_message(self) -> None:
        command = self._prompt("echoed twice", "echo-twice")
        self.router.route(command, now_ms=command.issued_at)
        self._take(InteractionKind.MESSAGE)
        for method, item_id, text in (
            ("item/started", "vendor-echo", "echoed twice"),
            ("item/completed", "vendor-echo", "echoed twice"),
            ("item/started", "typed", "typed in the terminal"),
            ("item/completed", "typed", "typed in the terminal"),
        ):
            self.transport.inject(
                {
                    "method": method,
                    "params": {
                        "threadId": "thread-1",
                        "item": {
                            "id": item_id,
                            "type": "userMessage",
                            "content": [{"type": "text", "text": text}],
                        },
                    },
                }
            )
        typed = self._take(InteractionKind.MESSAGE)
        self._wait_for(lambda: self.transport.incoming.empty())
        time.sleep(0.05)

        self.assertEqual((typed.correlation_id, typed.payload.text), ("typed", "typed in the terminal"))
        self.assertFalse(
            any(
                event.kind is InteractionKind.MESSAGE
                for event in self._pending_events()
            )
        )

    def test_idle_steer_publishes_the_prompt_once(self) -> None:
        self._start_remembered_turn("first prompt", "idle-once")
        self._drain_events()
        self.transport.steer_error = "no active turn to steer"
        again = self._prompt("next prompt", "idle-once-next")
        self.router.route(again, now_ms=again.issued_at)
        self._wait_for(
            lambda: len(
                self.transport.messages(
                    lambda message: message.get("method") == "turn/start"
                )
            )
            >= 2
        )
        self._wait_for(lambda: self.transport.incoming.empty())
        time.sleep(0.05)
        messages = [
            event
            for event in self._pending_events()
            if event.kind is InteractionKind.MESSAGE
        ]

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].payload.text, "next prompt")
        self.assertEqual(messages[0].correlation_id, again.command_id)

    def _start_remembered_turn(self, text: str, suffix: str) -> None:
        command = self._prompt(text, suffix)
        self.router.route(command, now_ms=command.issued_at)
        self._wait_for(
            lambda: bool(
                self.transport.messages(
                    lambda message: message.get("method") == "turn/start"
                )
            )
        )
        self._wait_for(lambda: self.transport.incoming.empty())
        time.sleep(0.05)

    def _take(self, kind: InteractionKind) -> InteractionEvent:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            try:
                event = self.events.get(timeout=0.05)
            except Empty:
                continue
            if event.kind is kind:
                return event
        raise AssertionError(f"{kind.value} was not published")

    def _pending_events(self) -> list[InteractionEvent]:
        found: list[InteractionEvent] = []
        while True:
            try:
                found.append(self.events.get_nowait())
            except Empty:
                return found

    def _wait_for_task_failure(self) -> InteractionEvent:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            try:
                event = self.events.get(timeout=0.05)
            except Empty:
                continue
            if (
                event.kind is InteractionKind.TASK_FAILED
                and isinstance(event.payload, TaskPayload)
                and event.payload.error_code == "codex_control_failed"
            ):
                return event
        raise AssertionError("Codex control failure was not published")

    def _configure(self, model: str, effort: str, suffix: str) -> ControlCommand:
        now_ms = int(time.time() * 1000)
        return ControlCommand(
            command_id=f"command-{suffix}",
            kind=ControlKind.CONFIGURE_SESSION,
            agent_id=self.session.agent_id,
            session_id=self.session.session_id,
            project_id=self.session.project_id,
            issued_by="test-controller",
            issued_at=now_ms,
            expires_at=now_ms + 10_000,
            idempotency_key=f"idempotency-{suffix}",
            payload=ConfigureSessionPayload(
                model_set=True,
                model=model,
                effort_set=True,
                reasoning_effort=effort,
            ),
        )

    def _task_failures(self) -> list[InteractionEvent]:
        found: list[InteractionEvent] = []
        while True:
            try:
                event = self.events.get_nowait()
            except Empty:
                return found
            if event.kind is InteractionKind.TASK_FAILED:
                found.append(event)

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

    def test_attached_thread_item_completion_publishes_transcript_only(self) -> None:
        self._drain_events()
        connect_count = self.transport.connect_count
        self.transport.inject({"method": "item/completed", "params": {"item": "bad"}})
        self.transport.inject({
            "method": "item/completed",
            "params": {
                "threadId": "thread-other",
                "item": {
                    "type": "agentMessage",
                    "id": "other-1",
                    "text": "other thread",
                },
            },
        })
        self.transport.inject({
            "method": "item/completed",
            "params": {
                "item": {
                    "type": "reasoning",
                    "id": "reason-1",
                    "summary": "hidden thought",
                    "content": "hidden thought",
                },
            },
        })
        self.transport.inject({
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "item": {
                    "type": "agentMessage",
                    "id": "visible-1",
                    "text": "visible reply",
                },
            },
        })
        collected: list[InteractionEvent] = []
        seen = False
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                collected.append(self.events.get(timeout=0.05))
            except Empty:
                if seen:
                    break
                continue
            seen = any(
                getattr(event.payload, "text", "") == "visible reply"
                for event in collected
            )
        self.assertTrue(seen)
        blob = "".join(event.to_json() for event in collected)
        self.assertIn("visible reply", blob)
        self.assertNotIn("hidden thought", blob)
        self.assertNotIn("other thread", blob)
        self.assertEqual(self.transport.connect_count, connect_count)
        self.assertEqual(self.adapter.attached_thread_id(), "thread-1")

    def _drain_events(self) -> None:
        deadline = time.monotonic() + 0.05
        while time.monotonic() < deadline:
            try:
                self.events.get_nowait()
            except Empty:
                time.sleep(0.01)

    @staticmethod
    def _wait_for(predicate) -> None:
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        raise AssertionError("condition was not met")


def _draft_blob(drafts: list) -> str:
    return json.dumps([
        {
            "kind": kind.value,
            "id": correlation_id,
            "payload": payload.to_dict(),
        }
        for kind, correlation_id, payload in drafts
    ])


class ThreadItemProjectionTests(unittest.TestCase):
    """Synthetic public-schema examples, not a local app-server capture."""

    def test_user_assistant_command_file_and_mcp_items_are_bounded(self) -> None:
        user = _thread_item_drafts("item/completed", {
            "type": "userMessage",
            "id": "user-1",
            "content": [
                {"type": "text", "text": "look"},
                {"type": "localImage", "path": "/secret/shot.png"},
                {"type": "image", "url": "data:image/png;base64,AAAA"},
            ],
        })
        self.assertEqual(user[0][2].role, MessageRole.USER)
        self.assertEqual(user[0][2].phase, MessagePhase.COMPLETE)
        self.assertEqual(user[0][2].text, "look\n[image]\n[image]")
        self.assertNotIn("/secret/shot.png", _draft_blob(user))
        self.assertNotIn("base64", _draft_blob(user))

        started = _thread_item_drafts("item/started", {
            "type": "agentMessage",
            "id": "agent-1",
            "text": "partial",
        })
        completed = _thread_item_drafts("item/completed", {
            "type": "agentMessage",
            "id": "agent-1",
            "text": "final reply",
        })
        self.assertEqual(started[0][2].phase, MessagePhase.DELTA)
        self.assertEqual(completed[0][2].phase, MessagePhase.COMPLETE)
        self.assertEqual(completed[0][2].text, "final reply")

        command = _thread_item_drafts("item/completed", {
            "type": "commandExecution",
            "id": "cmd-1",
            "command": ["pytest", "-q"],
            "cwd": "/secret/workdir",
            "aggregatedOutput": "ok\n",
            "exitCode": 0,
        })
        self.assertEqual(command[0][2].name, "pytest -q")
        self.assertEqual(command[0][2].phase, ToolPhase.OUTPUT)
        self.assertEqual(command[0][2].stream, ToolStream.STDOUT)
        self.assertEqual(command[1][2].phase, ToolPhase.COMPLETE)
        self.assertEqual(command[1][2].exit_code, 0)
        self.assertNotIn("/secret/workdir", _draft_blob(command))
        self.assertEqual(
            _thread_item_drafts("item/completed", {
                "type": "commandExecution",
                "id": "cmd-2",
                "command": "false",
                "exitCode": True,
            })[0][2].exit_code,
            None,
        )

        file_change = _thread_item_drafts("item/completed", {
            "type": "fileChange",
            "id": "file-1",
            "changes": [{"path": "app.py", "kind": "update", "diff": "+added\n-removed\n"}],
            "status": "completed",
        })
        self.assertIn("+added", file_change[0][2].text)
        self.assertIn("app.py", file_change[0][2].name)

        mcp = _thread_item_drafts("item/completed", {
            "type": "mcpToolCall",
            "id": "mcp-1",
            "server": "desk",
            "tool": "read",
            "arguments": {"path": "/secret/file"},
            "error": "tool failed",
        })
        self.assertEqual(mcp[0][2].stream, ToolStream.STDERR)
        self.assertEqual(mcp[0][2].text, "tool failed")
        self.assertNotIn("/secret/file", _draft_blob(mcp))

        search = _thread_item_drafts("item/started", {
            "type": "webSearch",
            "id": "search-1",
            "query": "deskhelm",
        })
        self.assertEqual(search[0][2].name, "web search: deskhelm")
        self.assertEqual(search[0][2].phase, ToolPhase.START)

        review = _thread_item_drafts("item/completed", {
            "type": "exitedReviewMode",
            "id": "review-1",
            "review": "no blocking issues",
        })
        self.assertEqual(review[0][2].text, "no blocking issues")
        self.assertEqual(_thread_item_drafts("item/started", {
            "type": "exitedReviewMode",
            "id": "review-1",
            "review": "no blocking issues",
        }), [])

        long_text = "a" * (MAX_ITEM_TEXT_CHARS + 50)
        clipped = _thread_item_drafts("item/completed", {
            "type": "plan",
            "id": "plan-1",
            "text": long_text,
        })
        self.assertIn("[truncated]", clipped[0][2].text)
        self.assertLessEqual(len(clipped[0][2].text), MAX_ITEM_TEXT_CHARS)

    def test_reasoning_and_unknown_items_publish_nothing(self) -> None:
        self.assertEqual(_thread_item_drafts("item/completed", {
            "type": "reasoning",
            "id": "reason-1",
            "summary": "hidden thought",
            "content": "hidden thought",
        }), [])
        self.assertEqual(_thread_item_drafts("item/completed", {
            "type": "imageView",
            "id": "image-1",
        }), [])
        self.assertEqual(_thread_item_drafts("item/completed", {
            "type": "agentMessage",
            "id": " ",
            "text": "nope",
        }), [])
        self.assertEqual(_thread_item_drafts("item/delta", {
            "type": "agentMessage",
            "id": "agent-1",
            "text": "token",
        }), [])


if __name__ == "__main__":
    unittest.main()

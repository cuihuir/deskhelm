from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from queue import Empty, Full, Queue
import subprocess
from threading import Event, RLock, Thread
import time
from typing import Any

from deskhelm_bridge.control import (
    ApprovalDecisionPayload,
    ControlCommand,
    ControlKind,
    InterruptPayload,
    SubmitPromptPayload,
)
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
from deskhelm_bridge.session_registry import SessionKey

from .app_server_transport import (
    AppServerTransport,
    UnixWebSocketTransport,
)
from .tui_proxy import (
    CodexTuiProxy,
    ProxiedApprovalRequest,
    TuiApprovalProxy,
)


_INITIALIZE = "initialize"
_LOADED_LIST = "loaded_list"
_LOADED_READ = "loaded_read"
_RESUME = "resume"
_TURN_START = "turn_start"
_TURN_STEER = "turn_steer"
_INTERRUPT = "interrupt"

_COMMAND_APPROVAL_METHOD = "item/commandExecution/requestApproval"
_FILE_APPROVAL_METHOD = "item/fileChange/requestApproval"
_PERMISSIONS_APPROVAL_METHOD = "item/permissions/requestApproval"
_USER_INPUT_METHOD = "item/tool/requestUserInput"

_COMMAND_APPROVAL_SUMMARY = "Codex command execution approval"
_FILE_APPROVAL_SUMMARY = "Codex file change approval"
_PERMISSIONS_APPROVAL_SUMMARY = "Codex permissions approval"
_USER_INPUT_PROMPT = "Codex requested user input"

MAX_IDENTIFIER_CHARS = 512
MAX_PROMPT_CHARS = 64 * 1024


@dataclass(frozen=True, slots=True)
class _ClientRequest:
    kind: str
    deadline: float
    command_id: str = ""
    thread_id: str = ""


@dataclass(frozen=True, slots=True)
class _PendingApproval:
    rpc_id: str | int
    rpc_key: tuple[str, str]
    request_id: str
    expires_at: int
    method: str
    proxy_token: str = ""
    requested_permissions: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class _PendingUserInput:
    rpc_id: str | int
    rpc_key: tuple[str, str]
    question_ids: tuple[str, ...]
    contains_secret: bool
    expires_at: int


@dataclass(slots=True)
class CodexAppServerAdapter:
    socket_path: Path
    session: SessionKey
    source_version: str
    client_version: str = "0.1.0.dev0"
    thread_id: str = ""
    approval_timeout_ms: int = 300_000
    user_input_timeout_ms: int = 300_000
    request_timeout_seconds: float = 5.0
    discovery_interval_seconds: float = 0.5
    reconnect_initial_seconds: float = 0.1
    reconnect_max_seconds: float = 2.0
    max_outbound_messages: int = 64
    max_pending_requests: int = 64
    max_pending_approvals: int = 16
    max_pending_user_inputs: int = 8
    transport_factory: Callable[[], AppServerTransport] | None = None
    tui_proxy_socket_path: Path | None = None
    tui_proxy_factory: Callable[[], TuiApprovalProxy] | None = None
    source: str = field(default="codex-app-server", init=False)
    _publish: Callable[[InteractionEvent], None] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _router: ControlRouter | None = field(default=None, init=False, repr=False)
    _unregister: list[Callable[[], None]] = field(
        default_factory=list,
        init=False,
        repr=False,
    )
    _outbound: Queue[dict[str, Any]] = field(init=False, repr=False)
    _stop: Event = field(default_factory=Event, init=False, repr=False)
    _thread: Thread | None = field(default=None, init=False, repr=False)
    _transport: AppServerTransport | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _tui_proxy: TuiApprovalProxy | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)
    _connected: bool = field(default=False, init=False)
    _initialized: bool = field(default=False, init=False)
    _attached_thread_id: str = field(default="", init=False)
    _subscribed_thread_id: str = field(default="", init=False)
    _resume_thread_id: str = field(default="", init=False)
    _active_turn_id: str = field(default="", init=False)
    _active_correlation_id: str = field(default="", init=False)
    _turn_submission_pending: str = field(default="", init=False)
    _waiting_on_approval: bool = field(default=False, init=False)
    _next_request_id: int = field(default=1, init=False)
    _next_discovery_at: float = field(default=0.0, init=False)
    _sequence: int = field(default=0, init=False)
    _ambiguity_reported: bool = field(default=False, init=False)
    _loaded_thread_candidates: tuple[str, ...] = field(
        default=(),
        init=False,
        repr=False,
    )
    _loaded_thread_ephemeral: dict[str, bool] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _loaded_thread_read_failures: set[str] = field(
        default_factory=set,
        init=False,
        repr=False,
    )
    _pending_client_requests: dict[int, _ClientRequest] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _pending_approvals: dict[str, _PendingApproval] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _approvals_by_rpc: dict[tuple[str, str], str] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _pending_user_inputs: dict[tuple[str, str], _PendingUserInput] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.socket_path, Path) or not self.socket_path.is_absolute():
            raise ValueError("app-server socket path must be absolute")
        if not isinstance(self.session, SessionKey):
            raise ValueError("session must be a SessionKey")
        for value, name in (
            (self.source_version, "source_version"),
            (self.client_version, "client_version"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.thread_id:
            _bounded_string(self.thread_id, "thread_id")
        if self.tui_proxy_socket_path is not None:
            if (
                not isinstance(self.tui_proxy_socket_path, Path)
                or not self.tui_proxy_socket_path.is_absolute()
            ):
                raise ValueError("TUI proxy socket path must be absolute")
            if self.tui_proxy_socket_path == self.socket_path:
                raise ValueError("TUI proxy and app-server sockets must differ")
        for value, name in (
            (self.approval_timeout_ms, "approval_timeout_ms"),
            (self.user_input_timeout_ms, "user_input_timeout_ms"),
            (self.max_outbound_messages, "max_outbound_messages"),
            (self.max_pending_requests, "max_pending_requests"),
            (self.max_pending_approvals, "max_pending_approvals"),
            (self.max_pending_user_inputs, "max_pending_user_inputs"),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be at least 1")
        for value, name in (
            (self.request_timeout_seconds, "request_timeout_seconds"),
            (self.discovery_interval_seconds, "discovery_interval_seconds"),
            (self.reconnect_initial_seconds, "reconnect_initial_seconds"),
            (self.reconnect_max_seconds, "reconnect_max_seconds"),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be greater than zero")
        if self.reconnect_initial_seconds > self.reconnect_max_seconds:
            raise ValueError("initial reconnect delay exceeds maximum")
        self._outbound = Queue(maxsize=self.max_outbound_messages)
        self._resume_thread_id = self.thread_id
        if self.transport_factory is None:
            self.transport_factory = lambda: UnixWebSocketTransport(
                self.socket_path
            )

    def start(
        self,
        publish_interaction: Callable[[InteractionEvent], None],
        control_router: ControlRouter,
    ) -> None:
        if not callable(publish_interaction):
            raise ValueError("publish_interaction must be callable")
        with self._lock:
            if self._thread is not None:
                raise RuntimeError("Codex app-server adapter is already started")
            self._publish = publish_interaction
            self._router = control_router
            unregister: list[Callable[[], None]] = []
            proxy: TuiApprovalProxy | None = None
            try:
                unregister.append(
                    control_router.register_handler(
                        ControlKind.SUBMIT_PROMPT,
                        self.submit_prompt,
                    )
                )
                unregister.append(
                    control_router.register_handler(
                        ControlKind.INTERRUPT,
                        self.interrupt,
                    )
                )
                unregister.append(
                    control_router.register_handler(
                        ControlKind.APPROVE,
                        self.approve,
                    )
                )
                unregister.append(
                    control_router.register_handler(
                        ControlKind.REJECT,
                        self.reject,
                    )
                )
                if self.tui_proxy_factory is not None:
                    proxy = self.tui_proxy_factory()
                elif self.tui_proxy_socket_path is not None:
                    proxy = CodexTuiProxy(
                        frontend_socket_path=self.tui_proxy_socket_path,
                        upstream_socket_path=self.socket_path,
                        approval_timeout_ms=self.approval_timeout_ms,
                        max_pending_approvals=self.max_pending_approvals,
                        max_queued_events=self.max_outbound_messages,
                    )
                if proxy is not None:
                    proxy.start(
                        self._handle_proxied_approval,
                        self._handle_proxied_approval_resolved,
                    )
            except BaseException:
                if proxy is not None:
                    proxy.close()
                for callback in reversed(unregister):
                    callback()
                self._publish = None
                self._router = None
                raise
            self._unregister = unregister
            self._tui_proxy = proxy
            self._stop.clear()
            worker = Thread(
                target=self._run,
                name="deskhelm-codex-app-server",
                daemon=True,
            )
            self._thread = worker
            worker.start()

    def close(self) -> None:
        with self._lock:
            worker = self._thread
            if worker is None:
                return
            self._stop.set()
            transport = self._transport
            proxy = self._tui_proxy
        if proxy is not None:
            proxy.close()
        if transport is not None:
            transport.close()
        worker.join(timeout=max(2.0, self.request_timeout_seconds + 1.0))
        with self._lock:
            unregister = tuple(reversed(self._unregister))
            self._unregister.clear()
            self._thread = None
            self._tui_proxy = None
            self._publish = None
            self._router = None
        for callback in unregister:
            callback()

    def submit_prompt(self, command: ControlCommand) -> None:
        self._validate_target(command)
        payload = command.payload
        if not isinstance(payload, SubmitPromptPayload):
            raise ValueError("submit_prompt command has an invalid payload")
        if len(payload.text) > MAX_PROMPT_CHARS:
            raise ValueError("prompt exceeds app-server adapter limit")
        now_ms = int(time.time() * 1000)
        with self._lock:
            thread_id = self._require_attached_thread_locked()
            self._purge_user_inputs_locked(now_ms)
            pending_inputs = tuple(self._pending_user_inputs.values())
            if pending_inputs:
                if len(pending_inputs) != 1:
                    raise RuntimeError(
                        "structured user input requires the Codex TUI"
                    )
                pending = pending_inputs[0]
                if pending.contains_secret or len(pending.question_ids) != 1:
                    raise RuntimeError(
                        "structured user input requires the Codex TUI"
                    )
                self._queue_message_locked(
                    {
                        "id": pending.rpc_id,
                        "result": {
                            "answers": {
                                pending.question_ids[0]: {
                                    "answers": [payload.text]
                                }
                            }
                        },
                    }
                )
                del self._pending_user_inputs[pending.rpc_key]
                if (
                    self._active_turn_id
                    and self._active_correlation_id == self._active_turn_id
                ):
                    self._active_correlation_id = command.command_id
                return

            if self._turn_submission_pending:
                raise RuntimeError("a Codex turn submission is already pending")
            if self._active_turn_id:
                self._queue_request_locked(
                    "turn/steer",
                    {
                        "threadId": thread_id,
                        "input": [{"type": "text", "text": payload.text}],
                        "expectedTurnId": self._active_turn_id,
                    },
                    _TURN_STEER,
                    command_id=command.command_id,
                    thread_id=thread_id,
                )
                if self._active_correlation_id == self._active_turn_id:
                    self._active_correlation_id = command.command_id
                return

            self._queue_request_locked(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": payload.text}],
                },
                _TURN_START,
                command_id=command.command_id,
                thread_id=thread_id,
            )
            self._turn_submission_pending = command.command_id

    def interrupt(self, command: ControlCommand) -> None:
        self._validate_target(command)
        if not isinstance(command.payload, InterruptPayload):
            raise ValueError("interrupt command has an invalid payload")
        with self._lock:
            thread_id = self._require_attached_thread_locked()
            if not self._active_turn_id:
                raise RuntimeError("Codex thread has no active turn")
            self._queue_request_locked(
                "turn/interrupt",
                {
                    "threadId": thread_id,
                    "turnId": self._active_turn_id,
                },
                _INTERRUPT,
                command_id=command.command_id,
                thread_id=thread_id,
            )

    def approve(self, command: ControlCommand) -> None:
        self._decide_approval(command, "accept")

    def reject(self, command: ControlCommand) -> None:
        self._decide_approval(command, "decline")

    def attached_thread_id(self) -> str:
        with self._lock:
            return self._attached_thread_id

    def subscribed_thread_id(self) -> str:
        with self._lock:
            return self._subscribed_thread_id

    def pending_user_input_count(self) -> int:
        with self._lock:
            self._purge_user_inputs_locked(int(time.time() * 1000))
            return len(self._pending_user_inputs)

    def _decide_approval(self, command: ControlCommand, decision: str) -> None:
        self._validate_target(command)
        payload = command.payload
        if not isinstance(payload, ApprovalDecisionPayload):
            raise ValueError("approval command has an invalid payload")
        now_ms = int(time.time() * 1000)
        proxy: TuiApprovalProxy | None = None
        with self._lock:
            pending = self._pending_approvals.get(payload.request_id)
            if pending is None or pending.expires_at <= now_ms:
                raise RuntimeError("Codex approval request is no longer pending")
            del self._pending_approvals[payload.request_id]
            self._approvals_by_rpc.pop(pending.rpc_key, None)
            if pending.proxy_token:
                proxy = self._tui_proxy
                if proxy is None:
                    raise RuntimeError("Codex TUI approval proxy is unavailable")
            else:
                self._queue_message_locked(
                    {
                        "id": pending.rpc_id,
                        "result": _approval_result(pending, decision),
                    }
                )
        if proxy is not None:
            proxy.resolve_approval(pending.proxy_token, decision)

    def _run(self) -> None:
        delay = self.reconnect_initial_seconds
        factory = self.transport_factory
        if factory is None:
            return
        while not self._stop.is_set():
            transport = factory()
            try:
                transport.connect()
                self._connected_transport(transport)
                delay = self.reconnect_initial_seconds
                self._connection_loop(transport)
            except Exception:
                pass
            finally:
                transport.close()
                self._disconnected_transport(transport)
            if self._stop.wait(delay):
                break
            delay = min(self.reconnect_max_seconds, delay * 2)

    def _connected_transport(self, transport: AppServerTransport) -> None:
        with self._lock:
            self._transport = transport
            self._connected = True
            self._initialized = False
            self._next_discovery_at = time.monotonic()
            self._queue_request_locked(
                "initialize",
                {
                    "clientInfo": {
                        "name": "deskhelm",
                        "title": "DeskHelm",
                        "version": self.client_version,
                    },
                    "capabilities": {
                        "experimentalApi": True,
                        "optOutNotificationMethods": [
                            "item/agentMessage/delta",
                            "item/commandExecution/outputDelta",
                            "item/reasoning/textDelta",
                            "item/reasoning/summaryTextDelta",
                        ],
                    },
                },
                _INITIALIZE,
            )

    def _disconnected_transport(self, transport: AppServerTransport) -> None:
        failed_commands: list[str] = []
        resolved_approvals: list[str] = []
        clear_attention = False
        with self._lock:
            if self._transport is transport:
                self._transport = None
            if self._attached_thread_id:
                self._resume_thread_id = self._attached_thread_id
            for request in self._pending_client_requests.values():
                if request.command_id and request.kind in {
                    _TURN_START,
                    _TURN_STEER,
                    _INTERRUPT,
                }:
                    failed_commands.append(request.command_id)
            direct_approvals = tuple(
                request_id
                for request_id, pending in self._pending_approvals.items()
                if not pending.proxy_token
            )
            resolved_approvals.extend(direct_approvals)
            for request_id in direct_approvals:
                pending = self._pending_approvals.pop(request_id)
                self._approvals_by_rpc.pop(pending.rpc_key, None)
            has_proxy_approval = any(
                pending.proxy_token
                for pending in self._pending_approvals.values()
            )
            self._connected = False
            self._initialized = False
            self._attached_thread_id = ""
            self._subscribed_thread_id = ""
            self._active_turn_id = ""
            self._active_correlation_id = ""
            self._turn_submission_pending = ""
            clear_attention = self._waiting_on_approval and not has_proxy_approval
            self._waiting_on_approval = has_proxy_approval
            self._pending_client_requests.clear()
            self._pending_user_inputs.clear()
            self._loaded_thread_candidates = ()
            self._loaded_thread_ephemeral.clear()
            self._loaded_thread_read_failures.clear()
            self._drain_queue_locked()
        if clear_attention:
            self._publish_attention(False)
        router = self._router
        if router is not None:
            for request_id in resolved_approvals:
                router.resolve_approval(request_id)
        for command_id in failed_commands:
            self._publish_terminal(
                InteractionKind.TASK_FAILED,
                "Codex app-server connection was lost",
                "codex_connection_lost",
                command_id,
            )

    def _connection_loop(self, transport: AppServerTransport) -> None:
        while not self._stop.is_set():
            self._schedule_discovery()
            self._flush_outbound(transport)
            message = transport.receive_message(0.1)
            if message is not None:
                self._handle_message(message)
            if self._expire_client_requests():
                raise TimeoutError("Codex app-server request timed out")

    def _schedule_discovery(self) -> None:
        with self._lock:
            now = time.monotonic()
            if (
                not self._initialized
                or now < self._next_discovery_at
                or any(
                    request.kind in {_LOADED_LIST, _LOADED_READ, _RESUME}
                    for request in self._pending_client_requests.values()
                )
            ):
                return
            if (
                self._attached_thread_id
                and self._subscribed_thread_id == self._attached_thread_id
            ):
                return
            if self._turn_submission_pending:
                return
            resume_thread_id = (
                self._resume_thread_id or self._attached_thread_id
            )
            if resume_thread_id:
                self._queue_request_locked(
                    "thread/resume",
                    {
                        "threadId": resume_thread_id,
                        "excludeTurns": True,
                        "initialTurnsPage": {
                            "limit": 1,
                            "sortDirection": "desc",
                            "itemsView": "summary",
                        },
                    },
                    _RESUME,
                    thread_id=resume_thread_id,
                )
            else:
                self._queue_request_locked(
                    "thread/loaded/list",
                    {"limit": 3},
                    _LOADED_LIST,
                )
            self._next_discovery_at = now + self.discovery_interval_seconds

    def _flush_outbound(self, transport: AppServerTransport) -> None:
        for _ in range(self.max_outbound_messages):
            try:
                message = self._outbound.get_nowait()
            except Empty:
                return
            transport.send_message(message)

    def _handle_message(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if method is not None:
            if not isinstance(method, str) or not method:
                raise ValueError("app-server method must not be empty")
            if "id" in message:
                self._handle_server_request(message)
            else:
                self._handle_notification(method, message.get("params"))
            return
        if "id" not in message:
            raise ValueError("app-server response must include id")
        self._handle_response(message)

    def _handle_response(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        if not isinstance(request_id, int) or isinstance(request_id, bool):
            return
        with self._lock:
            request = self._pending_client_requests.pop(request_id, None)
        if request is None:
            return
        if "error" in message:
            if (
                request.kind == _RESUME
                and not self.thread_id
                and _is_missing_rollout_error(message.get("error"))
            ):
                with self._lock:
                    if self._resume_thread_id == request.thread_id:
                        self._attached_thread_id = request.thread_id
                        self._subscribed_thread_id = ""
                    self._next_discovery_at = (
                        time.monotonic() + self.discovery_interval_seconds
                    )
                return
            if request.kind == _LOADED_READ:
                self._handle_loaded_thread_read_error(request)
                return
            if request.kind in {
                _INITIALIZE,
                _LOADED_LIST,
                _RESUME,
            }:
                raise RuntimeError("Codex app-server setup request failed")
            if request.kind == _TURN_START:
                with self._lock:
                    if self._turn_submission_pending == request.command_id:
                        self._turn_submission_pending = ""
            if request.kind == _TURN_STEER:
                with self._lock:
                    if self._active_correlation_id == request.command_id:
                        self._active_correlation_id = self._active_turn_id
            if request.command_id and request.kind in {
                _TURN_START,
                _TURN_STEER,
                _INTERRUPT,
            }:
                self._publish_terminal(
                    InteractionKind.TASK_FAILED,
                    "Codex control request failed",
                    "codex_control_failed",
                    request.command_id,
                )
            return

        result = message.get("result")
        if not isinstance(result, dict):
            raise ValueError("app-server response result must be an object")
        if request.kind == _INITIALIZE:
            with self._lock:
                self._initialized = True
                self._queue_message_locked({"method": "initialized", "params": {}})
                self._next_discovery_at = time.monotonic()
            return
        if request.kind == _LOADED_LIST:
            self._handle_loaded_threads(result)
            return
        if request.kind == _LOADED_READ:
            self._handle_loaded_thread_read_result(request, result)
            return
        if request.kind == _RESUME:
            self._handle_resume_result(request, result)
            return
        if request.kind == _TURN_START:
            turn = _mapping(result.get("turn"), "turn")
            turn_id = _bounded_string(turn.get("id"), "turn.id")
            with self._lock:
                if self._active_turn_id and self._active_turn_id != turn_id:
                    raise RuntimeError("Codex returned a different active turn")
                self._subscribed_thread_id = request.thread_id
                self._active_turn_id = turn_id
                self._active_correlation_id = request.command_id
                if self._turn_submission_pending == request.command_id:
                    self._turn_submission_pending = ""

    def _handle_loaded_threads(self, result: dict[str, Any]) -> None:
        data = result.get("data")
        if not isinstance(data, list):
            raise ValueError("loaded thread response data must be an array")
        next_cursor = result.get("nextCursor")
        if next_cursor is not None:
            _bounded_string(next_cursor, "loaded thread cursor")
        thread_ids = tuple(
            dict.fromkeys(
                _bounded_string(value, "loaded thread id")
                for value in data[:3]
            )
        )
        if len(thread_ids) == 1 and next_cursor is None and len(data) == 1:
            with self._lock:
                self._attach_discovered_thread_locked(thread_ids[0])
            return
        if len(thread_ids) == 2 and next_cursor is None and len(data) == 2:
            with self._lock:
                available = (
                    self.max_pending_requests
                    - len(self._pending_client_requests)
                )
                if available < len(thread_ids):
                    inspect_candidates = False
                else:
                    inspect_candidates = True
                    self._loaded_thread_candidates = thread_ids
                    self._loaded_thread_ephemeral.clear()
                    self._loaded_thread_read_failures.clear()
                    for thread_id in thread_ids:
                        self._queue_request_locked(
                            "thread/read",
                            {
                                "threadId": thread_id,
                                "includeTurns": False,
                            },
                            _LOADED_READ,
                            thread_id=thread_id,
                        )
            if inspect_candidates:
                return
        if thread_ids or next_cursor is not None:
            self._report_thread_ambiguity()

    def _handle_loaded_thread_read_result(
        self,
        request: _ClientRequest,
        result: dict[str, Any],
    ) -> None:
        thread = _mapping(result.get("thread"), "thread")
        thread_id = _bounded_string(thread.get("id"), "thread.id")
        if thread_id != request.thread_id:
            raise RuntimeError("Codex read an unexpected loaded thread")
        ephemeral = thread.get("ephemeral")
        if not isinstance(ephemeral, bool):
            raise ValueError("thread.ephemeral must be a boolean")
        with self._lock:
            if thread_id not in self._loaded_thread_candidates:
                return
            self._loaded_thread_ephemeral[thread_id] = ephemeral
        self._finish_loaded_thread_reads()

    def _handle_loaded_thread_read_error(
        self,
        request: _ClientRequest,
    ) -> None:
        with self._lock:
            if request.thread_id not in self._loaded_thread_candidates:
                return
            self._loaded_thread_read_failures.add(request.thread_id)
        self._finish_loaded_thread_reads()

    def _finish_loaded_thread_reads(self) -> None:
        attach_thread_id = ""
        report_ambiguity = False
        with self._lock:
            completed = (
                len(self._loaded_thread_ephemeral)
                + len(self._loaded_thread_read_failures)
            )
            if completed < len(self._loaded_thread_candidates):
                return
            non_ephemeral = tuple(
                thread_id
                for thread_id in self._loaded_thread_candidates
                if self._loaded_thread_ephemeral.get(thread_id) is False
            )
            if (
                not self._loaded_thread_read_failures
                and len(non_ephemeral) == 1
            ):
                attach_thread_id = non_ephemeral[0]
                self._attach_discovered_thread_locked(attach_thread_id)
            else:
                report_ambiguity = True
            self._loaded_thread_candidates = ()
            self._loaded_thread_ephemeral.clear()
            self._loaded_thread_read_failures.clear()
        if report_ambiguity:
            self._report_thread_ambiguity()

    def _attach_discovered_thread_locked(self, thread_id: str) -> None:
        self._attached_thread_id = thread_id
        self._subscribed_thread_id = ""
        self._resume_thread_id = thread_id
        self._ambiguity_reported = False

    def _report_thread_ambiguity(self) -> None:
        publish_failure = False
        with self._lock:
            if not self._ambiguity_reported:
                self._ambiguity_reported = True
                publish_failure = True
        if publish_failure:
            self._publish_terminal(
                InteractionKind.TASK_FAILED,
                "Multiple Codex threads are active",
                "codex_thread_ambiguous",
                "",
            )

    def _handle_resume_result(
        self,
        request: _ClientRequest,
        result: dict[str, Any],
    ) -> None:
        thread = _mapping(result.get("thread"), "thread")
        thread_id = _bounded_string(thread.get("id"), "thread.id")
        if thread_id != request.thread_id:
            raise RuntimeError("Codex resumed an unexpected thread")
        active_turn_id = _active_turn_from_resume(result, thread)
        status = thread.get("status")
        with self._lock:
            self._attached_thread_id = thread_id
            self._subscribed_thread_id = thread_id
            self._resume_thread_id = thread_id
            if not self._active_turn_id:
                self._active_turn_id = active_turn_id
                self._active_correlation_id = active_turn_id
            self._ambiguity_reported = False
        if status is not None:
            self._handle_thread_status(thread_id, status)

    def _handle_notification(self, method: str, params_value: object) -> None:
        if method not in {
            "thread/status/changed",
            "turn/started",
            "turn/completed",
            "serverRequest/resolved",
            "thread/closed",
        }:
            return
        params = _mapping(params_value, "notification params")
        if method == "thread/status/changed":
            thread_id = _bounded_string(params.get("threadId"), "threadId")
            self._handle_thread_status(thread_id, params.get("status"))
            return
        if method == "turn/started":
            thread_id = _bounded_string(params.get("threadId"), "threadId")
            turn = _mapping(params.get("turn"), "turn")
            turn_id = _bounded_string(turn.get("id"), "turn.id")
            with self._lock:
                if not self._matches_attaching_thread_locked(thread_id):
                    return
                self._subscribed_thread_id = thread_id
                if (
                    self._active_turn_id == turn_id
                    and self._active_correlation_id
                ):
                    correlation_id = self._active_correlation_id
                else:
                    correlation_id = self._turn_submission_pending or turn_id
                self._active_turn_id = turn_id
                self._active_correlation_id = correlation_id
                self._turn_submission_pending = ""
            return
        if method == "turn/completed":
            self._handle_turn_completed(params)
            return
        if method == "serverRequest/resolved":
            self._handle_server_request_resolved(params)
            return
        if method == "thread/closed":
            thread_id = _bounded_string(params.get("threadId"), "threadId")
            clear_attention = False
            with self._lock:
                if thread_id != self._attached_thread_id:
                    return
                self._attached_thread_id = ""
                self._subscribed_thread_id = ""
                if not self.thread_id:
                    self._resume_thread_id = ""
                self._active_turn_id = ""
                self._active_correlation_id = ""
                clear_attention = self._waiting_on_approval
                self._waiting_on_approval = False
                self._next_discovery_at = time.monotonic()
            if clear_attention:
                self._publish_attention(False)

    def _handle_turn_completed(self, params: dict[str, Any]) -> None:
        thread_id = _bounded_string(params.get("threadId"), "threadId")
        turn = _mapping(params.get("turn"), "turn")
        turn_id = _bounded_string(turn.get("id"), "turn.id")
        status = _bounded_string(turn.get("status"), "turn.status")
        resolved_approvals: tuple[str, ...] = ()
        clear_attention = False
        with self._lock:
            if thread_id != self._attached_thread_id:
                return
            self._subscribed_thread_id = thread_id
            correlation_id = self._active_correlation_id or turn_id
            self._active_turn_id = ""
            self._active_correlation_id = ""
            self._turn_submission_pending = ""
            self._pending_user_inputs.clear()
            resolved_approvals = tuple(self._pending_approvals)
            self._pending_approvals.clear()
            self._approvals_by_rpc.clear()
            clear_attention = self._waiting_on_approval
            self._waiting_on_approval = False
        if clear_attention:
            self._publish_attention(False)
        router = self._router
        if router is not None:
            for request_id in resolved_approvals:
                router.resolve_approval(request_id)
        if status == "completed":
            self._publish_terminal(
                InteractionKind.TASK_COMPLETED,
                "",
                "",
                correlation_id,
            )
        elif status == "interrupted":
            self._publish_terminal(
                InteractionKind.TASK_FAILED,
                "Codex turn was interrupted",
                "interrupted",
                correlation_id,
            )
        else:
            self._publish_terminal(
                InteractionKind.TASK_FAILED,
                "Codex turn failed",
                "codex_turn_failed",
                correlation_id,
            )

    def _handle_server_request(self, message: dict[str, Any]) -> None:
        rpc_id = _rpc_id(message.get("id"))
        rpc_key = _rpc_key(rpc_id)
        method = message.get("method")
        params = _mapping(message.get("params"), "request params")
        if method not in {
            _COMMAND_APPROVAL_METHOD,
            _FILE_APPROVAL_METHOD,
            _PERMISSIONS_APPROVAL_METHOD,
            _USER_INPUT_METHOD,
        }:
            with self._lock:
                self._queue_error_response_locked(rpc_id)
            return
        thread_id = _bounded_string(params.get("threadId"), "threadId")
        with self._lock:
            if not self._matches_attaching_thread_locked(thread_id):
                self._queue_error_response_locked(rpc_id)
                return
            self._subscribed_thread_id = thread_id
        if method in {
            _COMMAND_APPROVAL_METHOD,
            _FILE_APPROVAL_METHOD,
            _PERMISSIONS_APPROVAL_METHOD,
        }:
            self._handle_approval_request(rpc_id, rpc_key, method, params)
            return
        if method == _USER_INPUT_METHOD:
            self._handle_user_input_request(rpc_id, rpc_key, params)

    def _handle_proxied_approval(
        self,
        request: ProxiedApprovalRequest,
    ) -> None:
        if request.method not in {
            _COMMAND_APPROVAL_METHOD,
            _FILE_APPROVAL_METHOD,
            _PERMISSIONS_APPROVAL_METHOD,
        }:
            return
        thread_id = _bounded_string(request.params.get("threadId"), "threadId")
        with self._lock:
            known_threads = {
                value
                for value in (
                    self._attached_thread_id,
                    self._resume_thread_id,
                )
                if value
            }
            if known_threads and thread_id not in known_threads:
                return
            if self.thread_id and thread_id != self.thread_id:
                return
            if not self._attached_thread_id:
                self._attached_thread_id = thread_id
                self._resume_thread_id = thread_id
                self._next_discovery_at = time.monotonic()
        self._handle_approval_request(
            request.rpc_id,
            ("proxy", request.token),
            request.method,
            request.params,
            proxy_token=request.token,
        )

    def _handle_proxied_approval_resolved(self, token: str) -> None:
        rpc_key = ("proxy", token)
        request_id = ""
        clear_attention = False
        with self._lock:
            request_id = self._approvals_by_rpc.pop(rpc_key, "")
            if request_id:
                self._pending_approvals.pop(request_id, None)
            clear_attention = (
                self._waiting_on_approval and not self._pending_approvals
            )
            if clear_attention:
                self._waiting_on_approval = False
        if clear_attention:
            self._publish_attention(False)
        router = self._router
        if request_id and router is not None:
            router.resolve_approval(request_id)

    def _handle_approval_request(
        self,
        rpc_id: str | int,
        rpc_key: tuple[str, str],
        method: str,
        params: dict[str, Any],
        *,
        proxy_token: str = "",
    ) -> None:
        now_ms = int(time.time() * 1000)
        request_id = _deskhelm_request_id(self.session, rpc_key)
        summaries = {
            _COMMAND_APPROVAL_METHOD: _COMMAND_APPROVAL_SUMMARY,
            _FILE_APPROVAL_METHOD: _FILE_APPROVAL_SUMMARY,
            _PERMISSIONS_APPROVAL_METHOD: _PERMISSIONS_APPROVAL_SUMMARY,
        }
        summary = summaries[method]
        requested_permissions = None
        if method == _PERMISSIONS_APPROVAL_METHOD:
            permissions = params.get("permissions")
            if not isinstance(permissions, dict):
                if not proxy_token:
                    with self._lock:
                        self._queue_error_response_locked(rpc_id)
                return
            requested_permissions = deepcopy(permissions)
        with self._lock:
            if rpc_key in self._approvals_by_rpc:
                return
            if len(self._pending_approvals) >= self.max_pending_approvals:
                if not proxy_token:
                    self._queue_message_locked(
                        {"id": rpc_id, "result": {"decision": "decline"}}
                    )
                return
            pending = _PendingApproval(
                rpc_id=rpc_id,
                rpc_key=rpc_key,
                request_id=request_id,
                expires_at=now_ms + self.approval_timeout_ms,
                method=method,
                proxy_token=proxy_token,
                requested_permissions=requested_permissions,
            )
            self._pending_approvals[request_id] = pending
            self._approvals_by_rpc[rpc_key] = request_id
            self._waiting_on_approval = True
        self._publish_event(
            InteractionEvent(
                kind=InteractionKind.APPROVAL_REQUEST,
                agent_id=self.session.agent_id,
                session_id=self.session.session_id,
                project_id=self.session.project_id,
                source=self.source,
                source_version=self.source_version,
                sequence=self._next_sequence(),
                occurred_at=now_ms,
                correlation_id=request_id,
                payload=ApprovalRequestPayload(
                    request_id=request_id,
                    summary=summary,
                    expires_at=pending.expires_at,
                ),
            )
        )

    def _handle_user_input_request(
        self,
        rpc_id: str | int,
        rpc_key: tuple[str, str],
        params: dict[str, Any],
    ) -> None:
        questions = params.get("questions")
        if not isinstance(questions, list) or not 1 <= len(questions) <= 3:
            with self._lock:
                self._queue_error_response_locked(rpc_id)
            return
        question_ids: list[str] = []
        contains_secret = False
        for question_value in questions:
            question = _mapping(question_value, "user input question")
            question_ids.append(
                _bounded_string(question.get("id"), "question.id")
            )
            is_secret = question.get("isSecret", False)
            if not isinstance(is_secret, bool):
                raise ValueError("question.isSecret must be a boolean")
            contains_secret = contains_secret or is_secret
        now_ms = int(time.time() * 1000)
        auto_resolution = params.get("autoResolutionMs")
        timeout_ms = self.user_input_timeout_ms
        if isinstance(auto_resolution, int) and not isinstance(auto_resolution, bool):
            timeout_ms = max(1, min(timeout_ms, auto_resolution))
        with self._lock:
            if rpc_key in self._pending_user_inputs:
                return
            if len(self._pending_user_inputs) >= self.max_pending_user_inputs:
                return
            pending = _PendingUserInput(
                rpc_id=rpc_id,
                rpc_key=rpc_key,
                question_ids=tuple(question_ids),
                contains_secret=contains_secret,
                expires_at=now_ms + timeout_ms,
            )
            self._pending_user_inputs[rpc_key] = pending
        request_id = _deskhelm_request_id(self.session, rpc_key)
        self._publish_event(
            InteractionEvent(
                kind=InteractionKind.USER_INPUT_REQUIRED,
                agent_id=self.session.agent_id,
                session_id=self.session.session_id,
                project_id=self.session.project_id,
                source=self.source,
                source_version=self.source_version,
                sequence=self._next_sequence(),
                occurred_at=now_ms,
                correlation_id=request_id,
                payload=UserInputPayload(prompt=_USER_INPUT_PROMPT),
            )
        )

    def _handle_server_request_resolved(self, params: dict[str, Any]) -> None:
        thread_id = _bounded_string(params.get("threadId"), "threadId")
        with self._lock:
            if not self._matches_attaching_thread_locked(thread_id):
                return
            self._subscribed_thread_id = thread_id
        rpc_id = _rpc_id(params.get("requestId"))
        rpc_key = _rpc_key(rpc_id)
        request_id = ""
        with self._lock:
            request_id = self._approvals_by_rpc.pop(rpc_key, "")
            if request_id:
                self._pending_approvals.pop(request_id, None)
            self._pending_user_inputs.pop(rpc_key, None)
        router = self._router
        if request_id and router is not None:
            router.resolve_approval(request_id)

    def _handle_thread_status(
        self, thread_id: str, status_value: object
    ) -> None:
        waiting = _status_waiting_on_approval(status_value)
        publish = False
        with self._lock:
            if not self._matches_attaching_thread_locked(thread_id):
                return
            self._subscribed_thread_id = thread_id
            if waiting == self._waiting_on_approval:
                return
            self._waiting_on_approval = waiting
            publish = True
        if publish:
            self._publish_attention(waiting)

    def _expire_client_requests(self) -> bool:
        now = time.monotonic()
        internal_timeout = False
        failed_commands: list[str] = []
        failed_loaded_reads: list[_ClientRequest] = []
        with self._lock:
            expired = tuple(
                request_id
                for request_id, request in self._pending_client_requests.items()
                if request.deadline <= now
            )
            for request_id in expired:
                request = self._pending_client_requests.pop(request_id)
                if request.kind in {_INITIALIZE, _LOADED_LIST, _RESUME}:
                    internal_timeout = True
                elif request.kind == _LOADED_READ:
                    failed_loaded_reads.append(request)
                elif request.command_id and request.kind in {
                    _TURN_START,
                    _TURN_STEER,
                    _INTERRUPT,
                }:
                    failed_commands.append(request.command_id)
                if (
                    request.kind == _TURN_START
                    and self._turn_submission_pending == request.command_id
                ):
                    self._turn_submission_pending = ""
                if (
                    request.kind == _TURN_STEER
                    and self._active_correlation_id == request.command_id
                ):
                    self._active_correlation_id = self._active_turn_id
            self._purge_user_inputs_locked(int(time.time() * 1000))
        for request in failed_loaded_reads:
            self._handle_loaded_thread_read_error(request)
        for command_id in failed_commands:
            self._publish_terminal(
                InteractionKind.TASK_FAILED,
                "Codex control request timed out",
                "codex_control_timeout",
                command_id,
            )
        return internal_timeout

    def _queue_request_locked(
        self,
        method: str,
        params: dict[str, Any],
        kind: str,
        *,
        command_id: str = "",
        thread_id: str = "",
    ) -> int:
        if not self._connected:
            raise RuntimeError("Codex app-server is not connected")
        if len(self._pending_client_requests) >= self.max_pending_requests:
            raise RuntimeError("Codex app-server request capacity is exhausted")
        request_id = self._next_request_id
        self._next_request_id += 1
        self._pending_client_requests[request_id] = _ClientRequest(
            kind=kind,
            deadline=time.monotonic() + self.request_timeout_seconds,
            command_id=command_id,
            thread_id=thread_id,
        )
        try:
            self._queue_message_locked(
                {"method": method, "id": request_id, "params": params}
            )
        except BaseException:
            del self._pending_client_requests[request_id]
            raise
        return request_id

    def _queue_message_locked(self, message: dict[str, Any]) -> None:
        if not self._connected:
            raise RuntimeError("Codex app-server is not connected")
        try:
            self._outbound.put_nowait(message)
        except Full as error:
            raise RuntimeError("Codex app-server outbound queue is full") from error

    def _queue_error_response_locked(self, rpc_id: str | int) -> None:
        self._queue_message_locked(
            {
                "id": rpc_id,
                "error": {
                    "code": -32601,
                    "message": "Method not supported",
                },
            }
        )

    def _require_attached_thread_locked(self) -> str:
        if not self._connected or not self._initialized:
            raise RuntimeError("Codex app-server is not ready")
        if not self._attached_thread_id:
            raise RuntimeError("no Codex thread is attached")
        return self._attached_thread_id

    def _matches_attaching_thread_locked(self, thread_id: str) -> bool:
        return thread_id in {
            self._attached_thread_id,
            self._resume_thread_id,
        }

    def _validate_target(self, command: ControlCommand) -> None:
        target = SessionKey(
            command.agent_id,
            command.session_id,
            command.project_id,
        )
        if target != self.session:
            raise RuntimeError("control target does not match Codex app-server session")

    def _purge_user_inputs_locked(self, now_ms: int) -> None:
        expired = tuple(
            rpc_key
            for rpc_key, pending in self._pending_user_inputs.items()
            if pending.expires_at <= now_ms
        )
        for rpc_key in expired:
            del self._pending_user_inputs[rpc_key]

    def _drain_queue_locked(self) -> None:
        while True:
            try:
                self._outbound.get_nowait()
            except Empty:
                return

    def _next_sequence(self) -> int:
        with self._lock:
            sequence = self._sequence
            self._sequence += 1
            return sequence

    def _publish_terminal(
        self,
        kind: InteractionKind,
        message: str,
        error_code: str,
        correlation_id: str,
    ) -> None:
        self._publish_event(
            InteractionEvent(
                kind=kind,
                agent_id=self.session.agent_id,
                session_id=self.session.session_id,
                project_id=self.session.project_id,
                source=self.source,
                source_version=self.source_version,
                sequence=self._next_sequence(),
                correlation_id=correlation_id,
                payload=TaskPayload(message=message, error_code=error_code),
            )
        )

    def _publish_attention(self, active: bool) -> None:
        self._publish_event(
            InteractionEvent(
                kind=InteractionKind.ATTENTION,
                agent_id=self.session.agent_id,
                session_id=self.session.session_id,
                project_id=self.session.project_id,
                source=self.source,
                source_version=self.source_version,
                sequence=self._next_sequence(),
                payload=AttentionPayload(
                    reason=AttentionReason.APPROVAL,
                    active=active,
                ),
            )
        )

    def _publish_event(self, event: InteractionEvent) -> None:
        publish = self._publish
        if publish is None:
            return
        try:
            publish(event)
        except Exception:
            pass


def _approval_result(
    pending: _PendingApproval,
    decision: str,
) -> dict[str, Any]:
    if pending.method != _PERMISSIONS_APPROVAL_METHOD:
        return {"decision": decision}
    permissions = (
        deepcopy(pending.requested_permissions)
        if decision == "accept" and pending.requested_permissions is not None
        else {}
    )
    return {"permissions": permissions, "scope": "turn"}


def _active_turn_from_resume(
    result: dict[str, Any],
    thread: dict[str, Any],
) -> str:
    candidates: list[object] = []
    initial_page = result.get("initialTurnsPage")
    if isinstance(initial_page, dict):
        data = initial_page.get("data")
        if isinstance(data, list):
            candidates.extend(data[:1])
    turns = thread.get("turns")
    if isinstance(turns, list):
        candidates.extend(turns[-1:])
    for value in candidates:
        if not isinstance(value, dict) or value.get("status") != "inProgress":
            continue
        return _bounded_string(value.get("id"), "turn.id")
    return ""


def _status_waiting_on_approval(status_value: object) -> bool:
    status = _mapping(status_value, "thread status")
    status_type = _bounded_string(status.get("type"), "thread status type")
    active_flags = status.get("activeFlags", [])
    if not isinstance(active_flags, list):
        raise ValueError("thread status activeFlags must be an array")
    flags = tuple(
        _bounded_string(value, "thread status active flag")
        for value in active_flags
    )
    return status_type == "active" and "waitingOnApproval" in flags


def detect_codex_version(
    executable: str,
    *,
    timeout_seconds: float = 10.0,
) -> str:
    if not isinstance(executable, str) or not executable.strip():
        raise ValueError("Codex executable must not be empty")
    if timeout_seconds <= 0:
        raise ValueError("Codex version timeout must be greater than zero")
    try:
        result = subprocess.run(
            [executable, "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("Codex version could not be determined") from error
    lines = result.stdout.strip().splitlines()
    if result.returncode != 0 or not lines:
        raise RuntimeError("Codex version could not be determined")
    return lines[0][:128]


def _deskhelm_request_id(
    session: SessionKey,
    rpc_key: tuple[str, str],
) -> str:
    digest = hashlib.sha256(
        (
            f"{session.agent_id}\0{session.session_id}\0{session.project_id}\0"
            f"{rpc_key[0]}\0{rpc_key[1]}"
        ).encode("utf-8")
    ).hexdigest()
    return f"codex-app-server:{digest}"


def _rpc_id(value: object) -> str | int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("app-server request id must be a string or integer")
    if isinstance(value, str):
        _bounded_string(value, "request id")
    return value


def _rpc_key(value: str | int) -> tuple[str, str]:
    return ("string" if isinstance(value, str) else "integer", str(value))


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _is_missing_rollout_error(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    message = value.get("message")
    return (
        isinstance(message, str)
        and "no rollout found for thread id" in message.lower()
    )


def _bounded_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")
    if len(value) > MAX_IDENTIFIER_CHARS:
        raise ValueError(f"{name} exceeds length limit")
    return value

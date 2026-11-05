from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
from queue import Empty, Full, Queue
import shlex
import subprocess
from threading import Event, RLock, Thread
import time
from typing import Any

from .controls import (
    AnswerInputPayload,
    ApprovalDecisionPayload,
    ConfigureSessionPayload,
    ControlCommand,
    ControlHost,
    ControlKind,
    ControlUndelivered,
    InterruptPayload,
    SessionKey,
    SubmitPromptPayload,
)
from .events import (
    ApprovalRequestPayload,
    AttentionPayload,
    AttentionReason,
    InteractionEvent,
    InteractionKind,
    MessagePayload,
    MessagePhase,
    MessageRole,
    TaskPayload,
    ToolPayload,
    ToolPhase,
    ToolStatus,
    ToolStream,
    UserInputAnswerPayload,
    UserInputPayload,
)

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
_CATALOG = "catalog"
_MODEL_CACHE_SECONDS = 120.0
_MAX_CATALOG_MODELS = 40
_MAX_CATALOG_EFFORTS = 8
_MAX_CATALOG_SERVERS = 24
_MAX_CATALOG_TOOLS = 12
_MAX_CATALOG_SKILLS = 80

_COMMAND_APPROVAL_METHOD = "item/commandExecution/requestApproval"
_FILE_APPROVAL_METHOD = "item/fileChange/requestApproval"
_PERMISSIONS_APPROVAL_METHOD = "item/permissions/requestApproval"
_USER_INPUT_METHOD = "item/tool/requestUserInput"
# Codex 0.166.0 asks without waiting through this tool: its agentMessage
# carries `questions`, and the TUI sends the answers back as a user message
# in these tags, naming each question as [tool, call id, index].
_ASYNC_QUESTION_TOOL = "request_user_input_async"
_QUESTION_REPLY_OPEN = "<send_user_message_question_reply>"
_QUESTION_REPLY_CLOSE = "</send_user_message_question_reply>"

_COMMAND_APPROVAL_SUMMARY = "Codex command execution approval"
_FILE_APPROVAL_SUMMARY = "Codex file change approval"
_PERMISSIONS_APPROVAL_SUMMARY = "Codex permissions approval"
_USER_INPUT_PROMPT = "Codex requested user input"

MAX_IDENTIFIER_CHARS = 512
MAX_PROMPT_CHARS = 64 * 1024
MAX_ITEM_TEXT_CHARS = 16_384
_MAX_SUBMITTED_PROMPTS = 8
MAX_COMMAND_CHARS = 2_000
MAX_APPROVAL_DETAIL_CHARS = 8 * 1024
MAX_ITEM_TITLES = 64
ASYNC_QUESTION_TIMEOUT_MS = 60 * 60 * 1000
MAX_PENDING_QUESTIONS = 8
MAX_QUESTIONS = 8
MAX_QUESTION_OPTIONS = 16
MAX_QUESTION_CHARS = 1024
MAX_QUESTION_HEADER_CHARS = 128
MAX_OPTION_CHARS = 512
MAX_FILE_CHANGES = 12
_ITEM_TEXT_MARKER = "\n…[truncated]"


@dataclass
class _RpcWait:
    event: Event = field(default_factory=Event)
    result: dict[str, Any] | None = None
    code: str = ""

    def succeed(self, result: dict[str, Any]) -> None:
        if self.event.is_set():
            return
        self.result = result
        self.event.set()

    def fail(self, code: str) -> None:
        if self.event.is_set():
            return
        self.code = code
        self.event.set()


@dataclass(frozen=True, slots=True)
class _ClientRequest:
    kind: str
    deadline: float
    command_id: str = ""
    thread_id: str = ""
    wait: _RpcWait | None = None
    prompt: str = ""


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


@dataclass(frozen=True, slots=True)
class _AsyncQuestion:
    """A question Codex asked without waiting, with its titles by question id."""

    call_id: str
    titles: dict[str, str]
    expires_at: int


@dataclass(slots=True)
class CodexAppServerAdapter:
    socket_path: Path
    session: SessionKey
    source_version: str
    client_version: str = "0.3.0"
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
    _router: ControlHost | None = field(default=None, init=False, repr=False)
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
    _submitted_prompts: list[str] = field(
        default_factory=list,
        init=False,
        repr=False,
    )
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
    _model_override: str = field(default="", init=False)
    _effort_override: str = field(default="", init=False)
    _model_cache: dict[str, tuple[str, ...]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _model_cache_at: float = field(default=0.0, init=False)
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
    # Recent items' command lines or paths, for the approvals that name them.
    _item_titles: dict[str, str] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    # Async questions by DeskHelm request id, and the call ids DeskHelm
    # answered, whose echoed reply is no answer from the TUI.
    _pending_questions: dict[str, _AsyncQuestion] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _answered_questions: dict[str, None] = field(
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
        control_router: ControlHost,
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
                unregister.append(
                    control_router.register_handler(
                        ControlKind.ANSWER_INPUT,
                        self.answer_input,
                    )
                )
                unregister.append(
                    control_router.register_handler(
                        ControlKind.CONFIGURE_SESSION,
                        self.configure_session,
                    )
                )
                unregister.append(
                    control_router.register_catalog_provider(self.session_catalog)
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
                        self._handle_proxied_thread_resumed,
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
        submitted = False
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
                submitted = True
            else:
                self._send_user_text_locked(thread_id, payload.text, command.command_id)
                submitted = True
        if submitted:
            self._publish_submitted_prompt(command.command_id, payload.text)

    def _send_user_text_locked(self, thread_id: str, text: str, command_id: str) -> None:
        """Steer the active turn with a user message, or start a turn with it."""
        if self._turn_submission_pending:
            raise RuntimeError("a Codex turn submission is already pending")
        if self._active_turn_id:
            self._queue_request_locked(
                "turn/steer",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": text}],
                    "expectedTurnId": self._active_turn_id,
                },
                _TURN_STEER,
                command_id=command_id,
                thread_id=thread_id,
                prompt=text,
            )
            if self._active_correlation_id == self._active_turn_id:
                self._active_correlation_id = command_id
            return
        turn_params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": text}],
        }
        if self._model_override:
            turn_params["model"] = self._model_override
        if self._effort_override:
            turn_params["effort"] = self._effort_override
        self._queue_request_locked(
            "turn/start",
            turn_params,
            _TURN_START,
            command_id=command_id,
            thread_id=thread_id,
        )
        self._turn_submission_pending = command_id

    def answer_input(self, command: ControlCommand) -> None:
        """Answer a Codex question: a requestUserInput request on its own
        JSON-RPC channel, or an async question with the reply message the TUI
        would send (ADR 0046)."""
        self._validate_target(command)
        payload = command.payload
        if not isinstance(payload, AnswerInputPayload):
            raise ValueError("answer_input command has an invalid payload")
        now_ms = int(time.time() * 1000)
        with self._lock:
            thread_id = self._require_attached_thread_locked()
            self._purge_user_inputs_locked(now_ms)
            self._purge_questions_locked(now_ms)
            question = self._pending_questions.get(payload.request_id)
            if question is not None:
                if not set(payload.answers) <= set(question.titles):
                    raise ValueError("the answers name an unknown question")
                self._send_user_text_locked(thread_id, _question_reply(question, payload.answers), command.command_id)
                del self._pending_questions[payload.request_id]
                self._answered_questions[question.call_id] = None
                while len(self._answered_questions) > MAX_PENDING_QUESTIONS:
                    self._answered_questions.pop(next(iter(self._answered_questions)))
                return
            pending = next(
                (
                    item
                    for item in self._pending_user_inputs.values()
                    if _deskhelm_request_id(self.session, item.rpc_key) == payload.request_id
                ),
                None,
            )
            if pending is None:
                raise RuntimeError("the Codex question is no longer open")
            if pending.contains_secret:
                raise ValueError("a secret question is answered in the Codex TUI")
            if not set(payload.answers) <= set(pending.question_ids):
                raise ValueError("the answers name an unknown question")
            self._queue_message_locked(
                {
                    "id": pending.rpc_id,
                    "result": {
                        "answers": {
                            question_id: {"answers": list(items)}
                            for question_id, items in payload.answers.items()
                        }
                    },
                }
            )
            del self._pending_user_inputs[pending.rpc_key]

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

    def configure_session(self, command: ControlCommand) -> None:
        self._validate_target(command)
        payload = command.payload
        if not isinstance(payload, ConfigureSessionPayload):
            raise ValueError("configure_session command has an invalid payload")
        with self._lock:
            fresh = (
                bool(self._model_cache)
                and time.monotonic() - self._model_cache_at <= _MODEL_CACHE_SECONDS
            )
            cache = dict(self._model_cache)
            model = self._model_override
            effort = self._effort_override
        if not fresh:
            raise RuntimeError("Codex model catalog is stale")
        if payload.model_set:
            model = payload.model
        if payload.effort_set:
            effort = payload.reasoning_effort
        if model and model not in cache:
            raise RuntimeError("Codex model is not in the catalog")
        if effort and (not model or effort not in cache.get(model, ())):
            raise RuntimeError("Codex reasoning effort is not supported")
        with self._lock:
            self._model_override = model
            self._effort_override = effort

    def session_catalog(
        self, agent_id: str, session_id: str, project_id: str
    ) -> dict[str, Any]:
        if (
            agent_id != self.session.agent_id
            or session_id != self.session.session_id
            or project_id != self.session.project_id
        ):
            return _empty_session_catalog("catalog_unavailable")
        with self._lock:
            ready = self._initialized and bool(self._attached_thread_id)
            thread_id = self._attached_thread_id
            selected_model = self._model_override
            selected_effort = self._effort_override
        if not ready:
            return _empty_session_catalog("catalog_unavailable")
        try:
            thread_result = self._call_app_server(
                "thread/read",
                {"threadId": thread_id, "includeTurns": False},
            )
        except RuntimeError:
            return _empty_session_catalog("catalog_failed")
        thread = thread_result.get("thread")
        thread_model = ""
        cwd = ""
        if isinstance(thread, dict):
            if isinstance(thread.get("model"), str):
                thread_model = _catalog_token(thread.get("model"), 128)
            if isinstance(thread.get("cwd"), str):
                cwd = thread["cwd"].strip()
        models, model_cache, models_notice, models_truncated = self._read_models()
        servers, mcp_notice, mcp_truncated = self._read_mcp(thread_id)
        skills, skills_notice, skills_truncated = self._read_skills(cwd)
        if models_notice == "":
            with self._lock:
                self._model_cache = model_cache
                self._model_cache_at = time.monotonic()
        return {
            "available": True,
            "notice": "",
            "model": selected_model or thread_model,
            "reasoning_effort": selected_effort,
            "models": models,
            "models_notice": models_notice,
            "mcp_servers": servers,
            "mcp_notice": mcp_notice,
            "skills": skills,
            "skills_notice": skills_notice,
            "truncated": models_truncated or mcp_truncated or skills_truncated,
        }

    def attached_thread_id(self) -> str:
        with self._lock:
            return self._attached_thread_id

    def subscribed_thread_id(self) -> str:
        with self._lock:
            return self._subscribed_thread_id

    def _call_app_server(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        wait = _RpcWait()
        with self._lock:
            if not self._connected or not self._initialized:
                raise RuntimeError("catalog_unavailable")
            self._queue_request_locked(method, params, _CATALOG, wait=wait)
        if not wait.event.wait(self.request_timeout_seconds + 0.3):
            raise RuntimeError("catalog_failed")
        if wait.code or not isinstance(wait.result, dict):
            raise RuntimeError(wait.code or "catalog_failed")
        return wait.result

    def _read_models(
        self,
    ) -> tuple[list[dict[str, Any]], dict[str, tuple[str, ...]], str, bool]:
        try:
            result = self._call_app_server(
                "model/list",
                {"limit": _MAX_CATALOG_MODELS, "includeHidden": False},
            )
        except RuntimeError:
            return [], {}, "unavailable", False
        if not isinstance(result.get("data"), list):
            return [], {}, "unavailable", False
        parsed, cache, truncated = _parse_models(result)
        return parsed, cache, "", truncated

    def _read_mcp(self, thread_id: str) -> tuple[list[dict[str, Any]], str, bool]:
        try:
            result = self._call_app_server(
                "mcpServerStatus/list",
                {
                    "limit": _MAX_CATALOG_SERVERS,
                    "detail": "toolsAndAuthOnly",
                    "threadId": thread_id,
                },
            )
        except RuntimeError:
            return [], "unavailable", False
        return _parse_mcp(result)

    def _read_skills(self, cwd: str) -> tuple[list[dict[str, Any]], str, bool]:
        if not cwd or any(ord(character) < 32 for character in cwd):
            return [], "unavailable", False
        try:
            result = self._call_app_server(
                "skills/list",
                {"cwds": [cwd], "forceReload": False},
            )
        except RuntimeError:
            return [], "unavailable", False
        return _parse_skills(result)

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
        with self._lock:
            pending = self._pending_approvals.get(payload.request_id)
            if pending is None or pending.expires_at <= now_ms:
                raise RuntimeError("Codex approval request is no longer pending")
            proxy = self._tui_proxy if pending.proxy_token else None
            if pending.proxy_token and proxy is None:
                raise ControlUndelivered("Codex TUI approval proxy is unavailable")
            if proxy is None:
                self._queue_message_locked(
                    {
                        "id": pending.rpc_id,
                        "result": _approval_result(pending, decision),
                    }
                )
                self._forget_approval_locked(payload.request_id, pending)
                return
        # The proxy takes its own lock, so call it without holding this one.
        # Keep the record until the proxy accepts, so an undelivered decision
        # leaves both sides pending.
        proxy.resolve_approval(pending.proxy_token, decision)
        with self._lock:
            self._forget_approval_locked(payload.request_id, pending)

    def _forget_approval_locked(
        self, request_id: str, pending: _PendingApproval
    ) -> None:
        if self._pending_approvals.get(request_id) is pending:
            del self._pending_approvals[request_id]
            self._approvals_by_rpc.pop(pending.rpc_key, None)

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
            catalog_waiters = [
                request.wait
                for request in self._pending_client_requests.values()
                if request.wait is not None
            ]
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
            for waiter in catalog_waiters:
                waiter.fail("catalog_unavailable")
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
        if request.wait is not None:
            if "error" in message:
                request.wait.fail("catalog_failed")
                return
            result = message.get("result")
            if isinstance(result, dict):
                request.wait.succeed(result)
            else:
                request.wait.fail("catalog_failed")
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
            if (
                request.kind == _TURN_STEER
                and request.prompt
                and _is_idle_steer_error(message.get("error"))
                and self._retry_idle_steer(request)
            ):
                return
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
                # Starting a turn does not subscribe this connection to its
                # events (Codex 0.166.0), so thread/resume retries continue.
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
        if method in {"item/started", "item/completed"}:
            self._publish_thread_item(method, params_value)
            return
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
            with self._lock:
                proxied = self._tui_proxy is not None
            # Codex sends a request to every subscribed connection. With a
            # managed TUI, its proxied copy carries the decision, and this
            # copy would make one approval appear twice.
            if not proxied:
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

    def _handle_proxied_thread_resumed(self, thread_id: str) -> None:
        thread_id = _bounded_string(thread_id, "resumed thread_id")
        with self._lock:
            if self.thread_id and thread_id != self.thread_id:
                return
            if thread_id == self._attached_thread_id:
                return
            self._attached_thread_id = thread_id
            self._subscribed_thread_id = ""
            self._resume_thread_id = thread_id
            self._active_turn_id = ""
            self._active_correlation_id = ""
            self._turn_submission_pending = ""
            self._next_discovery_at = time.monotonic()

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
        item_id = params.get("itemId")
        with self._lock:
            title = self._item_titles.get(item_id, "") if isinstance(item_id, str) else ""
        detail = _approval_detail(method, params, title)
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
                    detail=detail,
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
        shaped: list[dict[str, Any]] = []
        contains_secret = False
        for question_value in questions:
            question = _mapping(question_value, "user input question")
            question_id = _bounded_string(question.get("id"), "question.id")
            question_ids.append(question_id)
            is_secret = question.get("isSecret", False)
            if not isinstance(is_secret, bool):
                raise ValueError("question.isSecret must be a boolean")
            contains_secret = contains_secret or is_secret
            shaped.append(_input_question(question, question_id, is_secret))
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
                payload=UserInputPayload(prompt=_USER_INPUT_PROMPT, questions=tuple(shaped)),
            )
        )

    def _open_async_question(self, item: dict[str, Any]) -> bool:
        """Track a question Codex asked without waiting; False if it cannot be."""
        call_id = item.get("id")
        raw = item.get("questions")
        if not isinstance(call_id, str) or not call_id.strip() or len(call_id) > MAX_IDENTIFIER_CHARS:
            return False
        questions: list[dict[str, Any]] = []
        # A question's id is its index in the call, which the reply names.
        for index, value in enumerate(raw if isinstance(raw, list) else []):
            title = _clip_line(value.get("title"), MAX_QUESTION_CHARS) if isinstance(value, dict) else ""
            if not title or len(questions) >= MAX_QUESTIONS:
                continue
            labels = (_clip_line(option, MAX_OPTION_CHARS) for option in value.get("options") or [])
            options = [{"label": label, "description": ""} for label in labels if label][:MAX_QUESTION_OPTIONS]
            # Codex takes a free-text answer as well as a suggested one.
            questions.append({"id": str(index), "header": "", "title": title, "options": options,
                              "other": True, "secret": False})
        if not questions:
            return False
        now_ms = int(time.time() * 1000)
        request_id = _deskhelm_request_id(self.session, ("async", call_id))
        with self._lock:
            self._purge_questions_locked(now_ms)
            if request_id in self._pending_questions:
                return True
            if len(self._pending_questions) >= MAX_PENDING_QUESTIONS:
                return False
            self._pending_questions[request_id] = _AsyncQuestion(
                call_id=call_id,
                titles={question["id"]: question["title"] for question in questions},
                expires_at=now_ms + ASYNC_QUESTION_TIMEOUT_MS,
            )
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
                payload=UserInputPayload(prompt=_USER_INPUT_PROMPT, questions=tuple(questions), blocking=False),
            )
        )
        return True

    def _question_replied(self, item_id: str, replies: list[tuple[str, int, str]]) -> None:
        """A user message that answers async questions. An answer from the TUI
        resolves its question, the echo of a DeskHelm answer is dropped, and an
        answer to a question DeskHelm never saw is shown as what the user said."""
        by_call: dict[str, dict[str, list[str]]] = {}
        for call_id, index, answer in replies:
            by_call.setdefault(call_id, {}).setdefault(str(index), []).append(answer)
        answered: list[tuple[str, dict[str, list[str]]]] = []
        unknown: list[str] = []
        with self._lock:
            for call_id, answers in by_call.items():
                if call_id in self._answered_questions:
                    del self._answered_questions[call_id]
                    continue
                request_id = _deskhelm_request_id(self.session, ("async", call_id))
                if self._pending_questions.pop(request_id, None) is not None:
                    answered.append((request_id, answers))
                else:
                    unknown.extend(answer for items in answers.values() for answer in items)
        for request_id, answers in answered:
            self._publish_event(
                InteractionEvent(
                    kind=InteractionKind.USER_INPUT_ANSWERED,
                    agent_id=self.session.agent_id,
                    session_id=self.session.session_id,
                    project_id=self.session.project_id,
                    source=self.source,
                    source_version=self.source_version,
                    sequence=self._next_sequence(),
                    correlation_id=request_id,
                    payload=UserInputAnswerPayload(
                        answers={question_id: tuple(items) for question_id, items in answers.items()}
                    ),
                )
            )
        if unknown:
            self._publish_event(
                InteractionEvent(
                    kind=InteractionKind.MESSAGE,
                    agent_id=self.session.agent_id,
                    session_id=self.session.session_id,
                    project_id=self.session.project_id,
                    source=self.source,
                    source_version=self.source_version,
                    sequence=self._next_sequence(),
                    correlation_id=item_id,
                    payload=MessagePayload(
                        role=MessageRole.USER,
                        phase=MessagePhase.COMPLETE,
                        text=_clip_text("\n".join(unknown), MAX_ITEM_TEXT_CHARS),
                    ),
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
            # Codex sends status changes to every connection, subscribed or
            # not, so a status change proves no subscription.
            if not self._matches_attaching_thread_locked(thread_id):
                return
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
                elif request.wait is not None:
                    request.wait.fail("catalog_failed")
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

    def _retry_idle_steer(self, request: _ClientRequest) -> bool:
        """Start one new turn after Codex reports the steered turn is gone."""
        queued = False
        with self._lock:
            self._active_turn_id = ""
            self._active_correlation_id = ""
            pending = bool(self._turn_submission_pending)
            thread_id = request.thread_id or self._attached_thread_id
            if pending or not self._connected or not thread_id or not request.prompt:
                queued = False
            else:
                turn_params: dict[str, Any] = {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": request.prompt}],
                }
                if self._model_override:
                    turn_params["model"] = self._model_override
                if self._effort_override:
                    turn_params["effort"] = self._effort_override
                try:
                    self._queue_request_locked(
                        "turn/start",
                        turn_params,
                        _TURN_START,
                        command_id=request.command_id,
                        thread_id=thread_id,
                        prompt=request.prompt,
                    )
                except RuntimeError:
                    queued = False
                else:
                    self._turn_submission_pending = request.command_id
                    queued = True
        if queued:
            return True
        if request.command_id:
            self._publish_terminal(
                InteractionKind.TASK_FAILED,
                "Codex control request failed",
                "codex_control_failed",
                request.command_id,
            )
        return True

    def _queue_request_locked(
        self,
        method: str,
        params: dict[str, Any],
        kind: str,
        *,
        command_id: str = "",
        thread_id: str = "",
        wait: _RpcWait | None = None,
        prompt: str = "",
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
            wait=wait,
            prompt=prompt,
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
            raise ControlUndelivered("Codex app-server is not connected")
        try:
            self._outbound.put_nowait(message)
        except Full as error:
            raise ControlUndelivered("Codex app-server outbound queue is full") from error

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
            raise ControlUndelivered("Codex app-server is not ready")
        if not self._attached_thread_id:
            raise ControlUndelivered("no Codex thread is attached")
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

    def _purge_questions_locked(self, now_ms: int) -> None:
        for request_id in [
            request_id for request_id, question in self._pending_questions.items() if question.expires_at <= now_ms
        ]:
            del self._pending_questions[request_id]

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

    def _item_targets_attached_thread(self, params: dict[str, Any]) -> bool:
        with self._lock:
            attached = self._attached_thread_id
        if not attached:
            return False
        thread_id = params.get("threadId")
        if isinstance(thread_id, str) and thread_id.strip():
            return thread_id == attached
        # Public item examples omit threadId. This connection is subscribed to
        # one attached thread, so an item without a thread id stays on it.
        return True

    def _publish_thread_item(self, method: str, params_value: object) -> None:
        if not isinstance(params_value, dict):
            return
        if not self._item_targets_attached_thread(params_value):
            return
        item = params_value.get("item")
        if not isinstance(item, dict):
            return
        if item.get("type") == "agentMessage" and item.get("questions"):
            # A question card replaces the message that lists the question.
            if method != "item/completed" or self._open_async_question(item):
                return
        try:
            drafts = _thread_item_drafts(method, item)
        except (TypeError, ValueError):
            return
        for kind, correlation_id, payload in drafts:
            if isinstance(payload, MessagePayload) and payload.role is MessageRole.USER:
                replies = _question_replies(payload.text)
                if replies is not None:
                    self._question_replied(correlation_id, replies)
                    continue
            if isinstance(payload, ToolPayload) and payload.phase is ToolPhase.START:
                with self._lock:
                    self._item_titles[payload.tool_call_id] = payload.name
                    while len(self._item_titles) > MAX_ITEM_TITLES:
                        self._item_titles.pop(next(iter(self._item_titles)))
            if (
                kind is InteractionKind.MESSAGE
                and isinstance(payload, MessagePayload)
                and payload.role is MessageRole.USER
                and payload.phase is MessagePhase.COMPLETE
                and self._consume_submitted_prompt(payload.text)
            ):
                continue
            try:
                event = InteractionEvent(
                    kind=kind,
                    agent_id=self.session.agent_id,
                    session_id=self.session.session_id,
                    project_id=self.session.project_id,
                    source=self.source,
                    source_version=self.source_version,
                    sequence=self._next_sequence(),
                    correlation_id=correlation_id,
                    payload=payload,
                )
            except (TypeError, ValueError):
                continue
            self._publish_event(event)

    def _publish_submitted_prompt(self, command_id: str, text: str) -> None:
        clipped = _clip_text(text, MAX_ITEM_TEXT_CHARS).strip()
        if not clipped:
            return
        with self._lock:
            self._submitted_prompts.append(clipped)
            if len(self._submitted_prompts) > _MAX_SUBMITTED_PROMPTS:
                del self._submitted_prompts[:-_MAX_SUBMITTED_PROMPTS]
        self._publish_event(
            InteractionEvent(
                kind=InteractionKind.MESSAGE,
                agent_id=self.session.agent_id,
                session_id=self.session.session_id,
                project_id=self.session.project_id,
                source=self.source,
                source_version=self.source_version,
                sequence=self._next_sequence(),
                correlation_id=command_id,
                payload=MessagePayload(
                    role=MessageRole.USER,
                    phase=MessagePhase.COMPLETE,
                    text=clipped,
                ),
            )
        )

    def _consume_submitted_prompt(self, text: str) -> bool:
        with self._lock:
            try:
                self._submitted_prompts.remove(text)
            except ValueError:
                return False
            return True

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


def _is_idle_steer_error(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    message = value.get("message")
    return isinstance(message, str) and "no active turn" in message.lower()


def _empty_session_catalog(notice: str) -> dict[str, Any]:
    return {
        "available": False,
        "notice": notice,
        "model": "",
        "reasoning_effort": "",
        "models": [],
        "models_notice": "unavailable",
        "mcp_servers": [],
        "mcp_notice": "unavailable",
        "skills": [],
        "skills_notice": "unavailable",
        "truncated": False,
    }


def _catalog_token(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or len(text) > limit:
        return ""
    if any(character.isspace() or ord(character) < 32 for character in text):
        return ""
    return text


def _catalog_label(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.replace("\n", " ").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _parse_models(
    result: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, tuple[str, ...]], bool]:
    data = result.get("data")
    if not isinstance(data, list):
        return [], {}, False
    models: list[dict[str, Any]] = []
    cache: dict[str, tuple[str, ...]] = {}
    overflow = False
    for item in data:
        if not isinstance(item, dict) or item.get("hidden") is True:
            continue
        if len(models) >= _MAX_CATALOG_MODELS:
            overflow = True
            break
        model_id = _catalog_token(item.get("model"), 128) or _catalog_token(
            item.get("id"), 128
        )
        if not model_id:
            continue
        efforts: list[dict[str, str]] = []
        effort_ids: list[str] = []
        raw_efforts = item.get("supportedReasoningEfforts")
        if isinstance(raw_efforts, list):
            for effort in raw_efforts:
                if len(efforts) >= _MAX_CATALOG_EFFORTS:
                    break
                if not isinstance(effort, dict):
                    continue
                effort_id = _catalog_token(effort.get("reasoningEffort"), 64)
                if not effort_id or effort_id in effort_ids:
                    continue
                effort_ids.append(effort_id)
                efforts.append(
                    {
                        "id": effort_id,
                        "description": _catalog_label(effort.get("description"), 240),
                    }
                )
        default_effort = _catalog_token(item.get("defaultReasoningEffort"), 64)
        if default_effort not in effort_ids:
            default_effort = ""
        models.append(
            {
                "id": model_id,
                "name": _catalog_label(item.get("displayName"), 128) or model_id,
                "is_default": item.get("isDefault") is True,
                "default_effort": default_effort,
                "efforts": efforts,
            }
        )
        cache[model_id] = tuple(effort_ids)
    cursor = result.get("nextCursor")
    truncated = overflow or (isinstance(cursor, str) and bool(cursor))
    return models, cache, truncated


def _parse_mcp(result: dict[str, Any]) -> tuple[list[dict[str, Any]], str, bool]:
    data = result.get("data")
    if not isinstance(data, list):
        return [], "unavailable", False
    statuses = {
        "notStarted",
        "starting",
        "connected",
        "authenticationRequired",
        "failed",
        "cancelled",
        "disabled",
    }
    auth_values = {"unknown", "unsupported", "notLoggedIn", "bearerToken", "oAuth"}
    servers: list[dict[str, Any]] = []
    overflow = False
    for item in data:
        if len(servers) >= _MAX_CATALOG_SERVERS:
            overflow = True
            break
        if not isinstance(item, dict):
            continue
        name = _catalog_token(item.get("name"), 128)
        if not name:
            continue
        status = item.get("runtimeStatus")
        auth = item.get("authStatus")
        tools: list[str] = []
        raw_tools = item.get("tools")
        if isinstance(raw_tools, dict):
            for tool_name in raw_tools:
                if len(tools) >= _MAX_CATALOG_TOOLS:
                    break
                token = _catalog_token(tool_name, 128)
                if token:
                    tools.append(token)
        servers.append(
            {
                "name": name,
                "status": status if isinstance(status, str) and status in statuses else "unknown",
                "auth": auth if isinstance(auth, str) and auth in auth_values else "",
                "tools": tools,
            }
        )
    cursor = result.get("nextCursor")
    truncated = overflow or (isinstance(cursor, str) and bool(cursor))
    return servers, "", truncated


def _parse_skills(result: dict[str, Any]) -> tuple[list[dict[str, Any]], str, bool]:
    data = result.get("data")
    if not isinstance(data, list):
        return [], "unavailable", False
    scopes = {"user", "repo", "system", "admin"}
    skills: list[dict[str, Any]] = []
    overflow = False
    for entry in data:
        if not isinstance(entry, dict):
            continue
        raw_skills = entry.get("skills")
        if not isinstance(raw_skills, list):
            continue
        for item in raw_skills:
            if len(skills) >= _MAX_CATALOG_SKILLS:
                overflow = True
                break
            if not isinstance(item, dict) or not isinstance(item.get("enabled"), bool):
                continue
            name = _catalog_token(item.get("name"), 128)
            scope = item.get("scope")
            if not name or scope not in scopes:
                continue
            description = item.get("shortDescription")
            if not isinstance(description, str) or not description.strip():
                description = item.get("description")
            skills.append(
                {
                    "name": name,
                    "description": _catalog_label(description, 240),
                    "scope": scope,
                    "enabled": item["enabled"],
                }
            )
        if overflow:
            break
    cursor = result.get("nextCursor")
    truncated = overflow or (isinstance(cursor, str) and bool(cursor))
    return skills, "", truncated


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


def _clip_text(value: object, limit: int) -> str:
    if not isinstance(value, str) or limit < 1:
        return ""
    if len(value) <= limit:
        return value
    keep = max(0, limit - len(_ITEM_TEXT_MARKER))
    return value[:keep] + _ITEM_TEXT_MARKER


def _thread_item_drafts(
    method: str, item: dict[str, Any]
) -> list[tuple[InteractionKind, str, MessagePayload | ToolPayload]]:
    """Map one public app-server ThreadItem into interaction drafts.

    The shape comes from the public app-server item documentation. Callers
    pass synthetic or live items; this function does not claim a fixture was
    captured locally. Reasoning content is omitted. Working directories and
    tool arguments are omitted.
    """
    if method not in {"item/started", "item/completed"}:
        return []
    item_type = item.get("type")
    item_id = item.get("id")
    if (
        not isinstance(item_type, str)
        or not isinstance(item_id, str)
        or not item_id.strip()
        or len(item_id) > MAX_IDENTIFIER_CHARS
    ):
        return []
    started = method == "item/started"
    if item_type == "userMessage":
        # Codex 0.166.0 sends a user message as both item/started and
        # item/completed with the same text; publish it once.
        if started:
            return []
        text = _user_item_text(item)
        if not text:
            return []
        return [
            (
                InteractionKind.MESSAGE,
                item_id,
                MessagePayload(
                    role=MessageRole.USER,
                    phase=MessagePhase.COMPLETE,
                    text=text,
                ),
            )
        ]
    if item_type in {"agentMessage", "plan"}:
        text = _clip_text(item.get("text"), MAX_ITEM_TEXT_CHARS).strip()
        if not text:
            return []
        phase = MessagePhase.DELTA if started else MessagePhase.COMPLETE
        return [
            (
                InteractionKind.MESSAGE,
                item_id,
                MessagePayload(
                    role=MessageRole.ASSISTANT,
                    phase=phase,
                    text=text,
                ),
            )
        ]
    if item_type == "exitedReviewMode":
        if started:
            return []
        text = _clip_text(item.get("review"), MAX_ITEM_TEXT_CHARS).strip()
        if not text:
            return []
        return [
            (
                InteractionKind.MESSAGE,
                item_id,
                MessagePayload(
                    role=MessageRole.ASSISTANT,
                    phase=MessagePhase.COMPLETE,
                    text=text,
                ),
            )
        ]
    if item_type == "commandExecution":
        return _command_drafts(item, item_id, started=started)
    if item_type == "fileChange":
        return _file_drafts(item, item_id, started=started)
    if item_type == "webSearch":
        query = _clip_text(item.get("query"), 240).strip()
        name = f"web search: {query}" if query else "web search"
        return _tool_lifecycle(item_id, name, started=started, output="", status=_tool_status(item))
    if item_type == "mcpToolCall":
        tool = item.get("tool") if isinstance(item.get("tool"), str) else ""
        server = item.get("server") if isinstance(item.get("server"), str) else ""
        name = _clip_text(f"{server} {tool}".strip() or "mcp", 240)
        output = ""
        stream = ToolStream.STDOUT
        status = _tool_status(item)
        if not started:
            error = item.get("error")
            result = item.get("result")
            if isinstance(error, str) and error.strip():
                output = _clip_text(error, MAX_ITEM_TEXT_CHARS)
                stream = ToolStream.STDERR
                status = ToolStatus.FAILED
            elif isinstance(result, str) and result.strip():
                output = _clip_text(result, MAX_ITEM_TEXT_CHARS)
        return _tool_lifecycle(
            item_id,
            name,
            started=started,
            output=output,
            stream=stream,
            status=status,
        )
    return []


def _user_item_text(item: dict[str, Any]) -> str:
    content = item.get("content")
    parts: list[str] = []
    entries = [content] if isinstance(content, str) else content
    if not isinstance(entries, list):
        return ""
    for entry in entries:
        if isinstance(entry, str) and entry.strip():
            parts.append(entry)
        elif isinstance(entry, dict):
            if entry.get("type") == "text" and isinstance(entry.get("text"), str):
                parts.append(entry["text"])
            elif entry.get("type") in {"image", "localImage"}:
                parts.append("[image]")
    text = "\n".join(part for part in parts if part.strip())
    return _clip_text(text, MAX_ITEM_TEXT_CHARS).strip()


_SHELLS = frozenset({"bash", "sh", "zsh"})


def _tool_status(item: dict[str, Any], exit_code: int | None = None) -> ToolStatus:
    """How an item ended: Codex says declined or failed, or a nonzero exit code does."""
    status = item.get("status")
    if status == "declined":
        return ToolStatus.DECLINED
    if status == "failed" or exit_code not in (None, 0):
        return ToolStatus.FAILED
    return ToolStatus.COMPLETED


def _shell_script(command: str | list[Any]) -> str:
    """The script of Codex's `<shell> -lc <script>` wrapper, or the command itself."""
    if isinstance(command, list):
        parts = [part for part in command if isinstance(part, str)]
    else:
        try:
            parts = shlex.split(command)
        except ValueError:
            return command
    if len(parts) == 3 and parts[1] in ("-lc", "-c") and os.path.basename(parts[0]) in _SHELLS and parts[2].strip():
        return parts[2]
    return command if isinstance(command, str) else " ".join(parts)


def _clip_line(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _input_question(question: dict[str, Any], question_id: str, secret: bool) -> dict[str, Any]:
    """A requestUserInput question in DeskHelm's shape (ADR 0046)."""
    raw_options = question.get("options")
    options = []
    for option in raw_options if isinstance(raw_options, list) else []:
        label = _clip_line(option.get("label"), MAX_OPTION_CHARS) if isinstance(option, dict) else ""
        if label and len(options) < MAX_QUESTION_OPTIONS:
            options.append({"label": label, "description": _clip_line(option.get("description"), MAX_OPTION_CHARS)})
    title = _clip_line(question.get("question"), MAX_QUESTION_CHARS)
    header = _clip_line(question.get("header"), MAX_QUESTION_HEADER_CHARS)
    return {
        "id": question_id,
        "header": header if title else "",
        "title": title or header or "Codex question",
        "options": options,
        "other": question.get("isOther") is True or not options,
        "secret": secret,
    }


def _question_reply(question: _AsyncQuestion, answers: dict[str, tuple[str, ...]]) -> str:
    """The user message the Codex TUI sends to answer an async question."""
    replies = [
        {
            "answer": answer,
            "question": question.titles[question_id],
            "questionItemId": json.dumps([_ASYNC_QUESTION_TOOL, question.call_id, int(question_id)], separators=(",", ":")),
        }
        for question_id in sorted(answers, key=int)
        for answer in answers[question_id]
    ]
    body = json.dumps(replies, ensure_ascii=False, separators=(",", ":"))
    return f"{_QUESTION_REPLY_OPEN}\n{body}\n{_QUESTION_REPLY_CLOSE}"


def _question_replies(text: str) -> list[tuple[str, int, str]] | None:
    """The (call id, question index, answer) of each reply in a TUI answer
    message, or None when the text is not one."""
    body = text.strip()
    if not (body.startswith(_QUESTION_REPLY_OPEN) and body.endswith(_QUESTION_REPLY_CLOSE)):
        return None
    try:
        values = json.loads(body[len(_QUESTION_REPLY_OPEN):-len(_QUESTION_REPLY_CLOSE)])
    except ValueError:
        return None
    if not isinstance(values, list) or not values:
        return None
    replies = []
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get("answer"), str):
            return None
        try:
            tool, call_id, index = json.loads(value.get("questionItemId"))
        except (TypeError, ValueError):
            return None
        if tool != _ASYNC_QUESTION_TOOL or not isinstance(call_id, str) or type(index) is not int:
            return None
        replies.append((call_id, index, value["answer"]))
    return replies


def _approval_detail(method: str, params: dict[str, Any], title: str) -> str:
    """What an approval would allow: its command, the item's command or paths,
    or else Codex's reason and the permissions it asks for."""
    command = params.get("command")
    if method == _COMMAND_APPROVAL_METHOD and isinstance(command, (str, list)):
        command = _shell_script(command)
        if isinstance(command, str) and command.strip():
            return _clip_text(command.strip(), MAX_APPROVAL_DETAIL_CHARS)
    if title.strip() and method != _PERMISSIONS_APPROVAL_METHOD:
        return _clip_text(title.strip(), MAX_APPROVAL_DETAIL_CHARS)
    parts = []
    reason = params.get("reason")
    if isinstance(reason, str) and reason.strip():
        parts.append(reason.strip())
    permissions = params.get("permissions")
    if method == _PERMISSIONS_APPROVAL_METHOD and isinstance(permissions, dict) and permissions:
        parts.append(", ".join(sorted(str(name) for name in permissions)))
    return _clip_text("\n".join(parts), MAX_APPROVAL_DETAIL_CHARS)


def _command_drafts(
    item: dict[str, Any], item_id: str, *, started: bool
) -> list[tuple[InteractionKind, str, ToolPayload]]:
    command = item.get("command")
    if isinstance(command, (str, list)):
        command = _shell_script(command)
    if not isinstance(command, str) or not command.strip():
        name = "command"
    else:
        name = _clip_text(command.strip(), MAX_COMMAND_CHARS)
    if started:
        return [_tool(item_id, name, ToolPhase.START)]
    exit_code = _exit_code(item)
    output = item.get("aggregatedOutput")
    text = _clip_text(output, MAX_ITEM_TEXT_CHARS) if isinstance(output, str) else ""
    return _tool_lifecycle(
        item_id, name, started=False, output=text, exit_code=exit_code, status=_tool_status(item, exit_code)
    )


def _file_drafts(
    item: dict[str, Any], item_id: str, *, started: bool
) -> list[tuple[InteractionKind, str, ToolPayload]]:
    changes = item.get("changes")
    names: list[str] = []
    lines: list[str] = []
    if isinstance(changes, list):
        for change in changes[:MAX_FILE_CHANGES]:
            if not isinstance(change, dict):
                continue
            path = change.get("path") if isinstance(change.get("path"), str) else "file"
            kind = change.get("kind") if isinstance(change.get("kind"), str) else "change"
            names.append(path.strip() or "file")
            lines.append(f"{kind} {path.strip() or 'file'}")
            diff = change.get("diff")
            if isinstance(diff, str) and diff.strip():
                lines.append(_clip_text(diff, MAX_ITEM_TEXT_CHARS))
        if len(changes) > MAX_FILE_CHANGES:
            lines.append(_ITEM_TEXT_MARKER.strip())
    name = _clip_text(", ".join(names) or "file change", MAX_COMMAND_CHARS)
    output = _clip_text("\n".join(lines), MAX_ITEM_TEXT_CHARS) if not started else ""
    return _tool_lifecycle(item_id, name, started=started, output=output, status=_tool_status(item))


def _tool_lifecycle(
    item_id: str,
    name: str,
    *,
    started: bool,
    output: str,
    exit_code: int | None = None,
    stream: ToolStream = ToolStream.STDOUT,
    status: ToolStatus = ToolStatus.COMPLETED,
) -> list[tuple[InteractionKind, str, ToolPayload]]:
    if started:
        return [_tool(item_id, name, ToolPhase.START)]
    drafts = []
    if output.strip():
        drafts.append(
            _tool(
                item_id,
                name,
                ToolPhase.OUTPUT,
                text=output,
                stream=stream,
            )
        )
    drafts.append(_tool(item_id, name, ToolPhase.COMPLETE, exit_code=exit_code, status=status))
    return drafts


def _tool(
    item_id: str,
    name: str,
    phase: ToolPhase,
    text: str = "",
    stream: ToolStream | None = None,
    exit_code: int | None = None,
    status: ToolStatus | None = None,
) -> tuple[InteractionKind, str, ToolPayload]:
    return (
        InteractionKind.TOOL,
        item_id,
        ToolPayload(
            tool_call_id=item_id,
            name=name or "tool",
            phase=phase,
            text=text,
            stream=stream,
            exit_code=exit_code,
            status=status,
        ),
    )


def _exit_code(item: dict[str, Any]) -> int | None:
    value = item.get("exitCode")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _bounded_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")
    if len(value) > MAX_IDENTIFIER_CHARS:
        raise ValueError(f"{name} exceeds length limit")
    return value

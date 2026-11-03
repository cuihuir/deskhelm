"""Shapes shared by hosts, clients, and the Bridge in protocol v2.

Every ``from_dict`` validates strictly at the protocol boundary, so the core
only sees well-formed values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
from typing import Any

MAX_IDENTITY_CHARS = 256
MAX_LABEL_CHARS = 128
MAX_SUMMARY_CHARS = 512
MAX_TEXT_CHARS = 64 * 1024
MAX_OPEN_REQUESTS = 16
MAX_CATALOG_BYTES = 256 * 1024


class ProtocolViolation(ValueError):
    """A well-formed frame breaks a protocol rule."""


class MessageType(StrEnum):
    HELLO = "hello"
    HELLO_OK = "hello_ok"
    ERROR = "error"
    SESSION_OPEN = "session_open"
    SESSION_OPEN_RESULT = "session_open_result"
    FACT = "fact"
    EFFECT = "effect"
    EFFECT_RESULT = "effect_result"
    SESSION_CLOSE = "session_close"
    SUBSCRIBE = "subscribe"
    SNAPSHOT = "snapshot"
    DELTA = "delta"
    INTERACTION = "interaction"
    COMMAND = "command"
    COMMAND_RESULT = "command_result"
    QUERY = "query"
    QUERY_RESULT = "query_result"


class Role(StrEnum):
    HOST = "host"
    CLIENT = "client"


class Tier(StrEnum):
    OPERATOR = "operator"
    VOICE = "voice"
    DEVICE = "device"
    OBSERVER = "observer"


class Stream(StrEnum):
    CONSOLE = "console"
    INTERACTION = "interaction"


def text_field(value: Any, name: str, limit: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ProtocolViolation(f"{name} must be a string")
    if len(value) > limit:
        raise ProtocolViolation(f"{name} is too long")
    if not allow_empty and not value.strip():
        raise ProtocolViolation(f"{name} must not be empty")
    return value


def identity_field(value: Any, name: str) -> str:
    text = text_field(value, name, MAX_IDENTITY_CHARS)
    if text != text.strip() or any(ord(character) < 32 for character in text):
        raise ProtocolViolation(f"{name} has invalid characters")
    return text


def int_field(value: Any, name: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ProtocolViolation(f"{name} must be an integer of at least {minimum}")
    return value


def bool_field(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ProtocolViolation(f"{name} must be a boolean")
    return value


def object_field(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolViolation(f"{name} must be an object")
    return value


def enum_field(enum: type[StrEnum], value: Any, name: str) -> Any:
    try:
        return enum(value)
    except ValueError as error:
        raise ProtocolViolation(f"{name} is not recognized") from error


@dataclass(frozen=True, slots=True)
class SessionKey:
    agent_id: str
    session_id: str
    project_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "project_id": self.project_id,
        }

    @classmethod
    def from_dict(cls, value: Any) -> SessionKey:
        value = object_field(value, "session")
        return cls(
            identity_field(value.get("agent_id"), "agent_id"),
            identity_field(value.get("session_id"), "session_id"),
            identity_field(value.get("project_id"), "project_id"),
        )


@dataclass(frozen=True, slots=True)
class RuntimeIdentity:
    adapter_id: str
    adapter_version: str
    runtime_name: str
    runtime_version: str

    def to_dict(self) -> dict[str, str]:
        return {
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "runtime_name": self.runtime_name,
            "runtime_version": self.runtime_version,
        }

    @classmethod
    def from_dict(cls, value: Any) -> RuntimeIdentity:
        value = object_field(value, "runtime")
        return cls(
            *(
                text_field(value.get(name), name, MAX_LABEL_CHARS)
                for name in ("adapter_id", "adapter_version", "runtime_name", "runtime_version")
            )
        )


class PromptWhileBusy(StrEnum):
    STEER = "steer"
    QUEUE = "queue"
    REJECT = "reject"


class ApprovalSupport(StrEnum):
    ANSWERABLE = "answerable"
    NOTIFY_ONLY = "notify_only"
    NONE = "none"


class ApprovalOverflow(StrEnum):
    DECLINE = "decline"
    NATIVE_UI = "native_ui"


class InputSupport(StrEnum):
    ANSWERABLE = "answerable"
    NATIVE_UI = "native_ui"
    NONE = "none"


CONFIGURE_FIELDS = frozenset({"model", "effort"})
CATALOG_PARTS = frozenset({"models", "mcp", "skills"})


@dataclass(frozen=True, slots=True)
class CapabilityMatrix:
    """What a driver's vendor can really do; the Bridge enforces it."""

    version: int
    prompt_while_busy: PromptWhileBusy
    interrupt: bool
    approvals: ApprovalSupport
    approval_overflow: ApprovalOverflow
    user_input: InputSupport
    resume: bool
    configure: frozenset[str] = frozenset()
    catalog: frozenset[str] = frozenset()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "prompt_while_busy": self.prompt_while_busy.value,
            "interrupt": self.interrupt,
            "approvals": self.approvals.value,
            "approval_overflow": self.approval_overflow.value,
            "user_input": self.user_input.value,
            "resume": self.resume,
            "configure": sorted(self.configure),
            "catalog": sorted(self.catalog),
        }

    @classmethod
    def from_dict(cls, value: Any) -> CapabilityMatrix:
        value = object_field(value, "capabilities")
        configure = _token_set(value.get("configure", []), CONFIGURE_FIELDS, "configure")
        catalog = _token_set(value.get("catalog", []), CATALOG_PARTS, "catalog")
        return cls(
            version=int_field(value.get("version"), "capabilities.version", minimum=1),
            prompt_while_busy=enum_field(PromptWhileBusy, value.get("prompt_while_busy"), "prompt_while_busy"),
            interrupt=bool_field(value.get("interrupt"), "interrupt"),
            approvals=enum_field(ApprovalSupport, value.get("approvals"), "approvals"),
            approval_overflow=enum_field(ApprovalOverflow, value.get("approval_overflow"), "approval_overflow"),
            user_input=enum_field(InputSupport, value.get("user_input"), "user_input"),
            resume=bool_field(value.get("resume"), "resume"),
            configure=configure,
            catalog=catalog,
        )


def _token_set(value: Any, allowed: frozenset[str], name: str) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ProtocolViolation(f"{name} must be a list of strings")
    tokens = frozenset(value)
    if not tokens <= allowed:
        raise ProtocolViolation(f"{name} has unknown entries")
    return tokens


class RequestKind(StrEnum):
    APPROVAL = "approval"
    INPUT = "input"


@dataclass(frozen=True, slots=True)
class OpenRequest:
    """A vendor request waiting for a person, as the host reports it."""

    provider_ref: str
    kind: RequestKind
    summary: str
    expires_at: int
    answerable: bool

    @classmethod
    def from_dict(cls, value: Any) -> OpenRequest:
        value = object_field(value, "request")
        return cls(
            provider_ref=identity_field(value.get("provider_ref"), "provider_ref"),
            kind=enum_field(RequestKind, value.get("kind"), "request kind"),
            summary=text_field(value.get("summary"), "summary", MAX_SUMMARY_CHARS),
            expires_at=int_field(value.get("expires_at"), "expires_at", minimum=1),
            answerable=bool_field(value.get("answerable"), "answerable"),
        )


class FactKind(StrEnum):
    READY = "ready"
    TURN_STARTED = "turn_started"
    MESSAGE = "message"
    TOOL = "tool"
    REQUEST_OPENED = "request_opened"
    REQUEST_RESOLVED = "request_resolved"
    TURN_TERMINAL = "turn_terminal"
    STATUS = "status"
    CATALOG = "catalog"
    CAPABILITIES = "capabilities"
    SESSION_ENDED = "session_ended"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class MessagePhase(StrEnum):
    DELTA = "delta"
    COMPLETE = "complete"


class ToolPhase(StrEnum):
    START = "start"
    OUTPUT = "output"
    COMPLETE = "complete"


class TurnStatus(StrEnum):
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class Failure(StrEnum):
    PROVIDER = "provider"
    USAGE_LIMIT = "usage_limit"
    AUTH = "auth"
    PROTOCOL = "protocol"
    TRANSPORT_LOST = "transport_lost"
    PROCESS_EXIT = "process_exit"
    TIMEOUT = "timeout"


class ResolvedBy(StrEnum):
    NATIVE = "native"
    DESKHELM = "deskhelm"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass(frozen=True, slots=True)
class Fact:
    """One normalized runtime fact. ``meta`` is content-free; ``content`` is not."""

    seq: int
    kind: FactKind
    meta: dict[str, Any] = field(default_factory=dict)
    content: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Fact:
        seq = int_field(value.get("seq"), "seq", minimum=1)
        kind = enum_field(FactKind, value.get("kind"), "fact kind")
        meta = object_field(value.get("meta", {}), "meta")
        content = object_field(value.get("content", {}), "content")
        _FACT_VALIDATORS[kind](meta, content)
        return cls(seq, kind, meta, content)


def _no_content(content: dict[str, Any]) -> None:
    if content:
        raise ProtocolViolation("this fact carries no content")


def _optional_identity(meta: dict[str, Any], name: str) -> None:
    if name in meta and meta[name] is not None:
        identity_field(meta[name], name)


def _validate_ready(meta: dict[str, Any], content: dict[str, Any]) -> None:
    _no_content(content)


def _validate_turn_started(meta: dict[str, Any], content: dict[str, Any]) -> None:
    identity_field(meta.get("turn_ref"), "turn_ref")
    _optional_identity(meta, "command_id")
    _no_content(content)


def _validate_message(meta: dict[str, Any], content: dict[str, Any]) -> None:
    identity_field(meta.get("message_ref"), "message_ref")
    enum_field(MessageRole, meta.get("role"), "role")
    enum_field(MessagePhase, meta.get("phase"), "phase")
    _optional_identity(meta, "command_id")
    text_field(content.get("text", ""), "text", MAX_TEXT_CHARS, allow_empty=True)


def _validate_tool(meta: dict[str, Any], content: dict[str, Any]) -> None:
    identity_field(meta.get("tool_ref"), "tool_ref")
    text_field(meta.get("label"), "label", MAX_LABEL_CHARS)
    enum_field(ToolPhase, meta.get("phase"), "tool phase")
    if meta.get("exit_code") is not None:
        if not isinstance(meta["exit_code"], int) or isinstance(meta["exit_code"], bool):
            raise ProtocolViolation("exit_code must be an integer")
    if "replace" in meta and not isinstance(meta["replace"], bool):
        raise ProtocolViolation("replace must be a boolean")
    text_field(content.get("text", ""), "text", MAX_TEXT_CHARS, allow_empty=True)


def _validate_request_opened(meta: dict[str, Any], content: dict[str, Any]) -> None:
    OpenRequest.from_dict(meta)
    _no_content(content)


def _validate_request_resolved(meta: dict[str, Any], content: dict[str, Any]) -> None:
    identity_field(meta.get("provider_ref"), "provider_ref")
    enum_field(ResolvedBy, meta.get("by"), "resolved by")
    _no_content(content)


def _validate_turn_terminal(meta: dict[str, Any], content: dict[str, Any]) -> None:
    enum_field(TurnStatus, meta.get("status"), "turn status")
    if meta.get("failure") is not None:
        enum_field(Failure, meta["failure"], "failure")
    bool_field(meta.get("reusable", True), "reusable")
    _optional_identity(meta, "turn_ref")
    text_field(content.get("message", ""), "message", MAX_SUMMARY_CHARS, allow_empty=True)


def _validate_status(meta: dict[str, Any], content: dict[str, Any]) -> None:
    bool_field(meta.get("attention"), "attention")
    _no_content(content)


def _validate_catalog(meta: dict[str, Any], content: dict[str, Any]) -> None:
    catalog = object_field(content.get("catalog"), "catalog")
    if len(json.dumps(catalog)) > MAX_CATALOG_BYTES:
        raise ProtocolViolation("catalog is too large")


def _validate_capabilities(meta: dict[str, Any], content: dict[str, Any]) -> None:
    CapabilityMatrix.from_dict(meta.get("capabilities"))
    _no_content(content)


def _validate_session_ended(meta: dict[str, Any], content: dict[str, Any]) -> None:
    text_field(meta.get("reason", "ended"), "reason", MAX_LABEL_CHARS)
    _no_content(content)


_FACT_VALIDATORS = {
    FactKind.READY: _validate_ready,
    FactKind.TURN_STARTED: _validate_turn_started,
    FactKind.MESSAGE: _validate_message,
    FactKind.TOOL: _validate_tool,
    FactKind.REQUEST_OPENED: _validate_request_opened,
    FactKind.REQUEST_RESOLVED: _validate_request_resolved,
    FactKind.TURN_TERMINAL: _validate_turn_terminal,
    FactKind.STATUS: _validate_status,
    FactKind.CATALOG: _validate_catalog,
    FactKind.CAPABILITIES: _validate_capabilities,
    FactKind.SESSION_ENDED: _validate_session_ended,
}


class CommandKind(StrEnum):
    FOCUS = "focus"
    SUBMIT_PROMPT = "submit_prompt"
    INTERRUPT = "interrupt"
    APPROVE = "approve"
    REJECT = "reject"
    CONFIGURE_SESSION = "configure_session"


@dataclass(frozen=True, slots=True)
class Command:
    """A client's control envelope. Approvals name the Bridge approval id."""

    command_id: str
    kind: CommandKind
    target: SessionKey
    issued_by: str
    issued_at: int
    expires_at: int
    idempotency_key: str
    payload: dict[str, Any]

    def fingerprint(self) -> str:
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "kind": self.kind.value,
            **self.target.to_dict(),
            "issued_by": self.issued_by,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "idempotency_key": self.idempotency_key,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, value: Any) -> Command:
        value = object_field(value, "command")
        kind = enum_field(CommandKind, value.get("kind"), "command kind")
        issued_at = int_field(value.get("issued_at"), "issued_at", minimum=1)
        expires_at = int_field(value.get("expires_at"), "expires_at", minimum=1)
        if expires_at <= issued_at:
            raise ProtocolViolation("expires_at must follow issued_at")
        payload = _COMMAND_PAYLOADS[kind](object_field(value.get("payload", {}), "payload"))
        return cls(
            command_id=identity_field(value.get("command_id"), "command_id"),
            kind=kind,
            target=SessionKey.from_dict(value),
            issued_by=identity_field(value.get("issued_by"), "issued_by"),
            issued_at=issued_at,
            expires_at=expires_at,
            idempotency_key=identity_field(value.get("idempotency_key"), "idempotency_key"),
            payload=payload,
        )


def _empty_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload:
        raise ProtocolViolation("this command takes no payload")
    return {}


def _prompt_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) != {"text"}:
        raise ProtocolViolation("submit_prompt takes exactly a text")
    return {"text": text_field(payload["text"], "text", MAX_TEXT_CHARS)}


def _decision_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) != {"approval_id"}:
        raise ProtocolViolation("a decision takes exactly an approval_id")
    return {"approval_id": identity_field(payload["approval_id"], "approval_id")}


def _configure_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload or not set(payload) <= CONFIGURE_FIELDS:
        raise ProtocolViolation("configure_session takes model and/or effort")
    for name, item in payload.items():
        if item is not None:
            text_field(item, name, MAX_LABEL_CHARS)
    return dict(payload)


_COMMAND_PAYLOADS = {
    CommandKind.FOCUS: _empty_payload,
    CommandKind.SUBMIT_PROMPT: _prompt_payload,
    CommandKind.INTERRUPT: _empty_payload,
    CommandKind.APPROVE: _decision_payload,
    CommandKind.REJECT: _decision_payload,
    CommandKind.CONFIGURE_SESSION: _configure_payload,
}


class EffectKind(StrEnum):
    SUBMIT = "submit"
    INTERRUPT = "interrupt"
    RESPOND = "respond"
    CONFIGURE = "configure"


class EffectOutcome(StrEnum):
    ACCEPTED = "accepted"
    UNDELIVERED = "undelivered"
    UNSUPPORTED = "unsupported"


class ResultCode(StrEnum):
    FOCUSED = "focused"
    DISPATCHED = "dispatched"
    EXPIRED = "expired"
    ISSUER_MISMATCH = "issuer_mismatch"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    IDEMPOTENCY_CAPACITY = "idempotency_capacity"
    TARGET_NOT_FOUND = "target_not_found"
    TARGET_INACTIVE = "target_inactive"
    NOT_PERMITTED = "not_permitted"
    UNSUPPORTED = "unsupported"
    APPROVAL_NOT_FOUND = "approval_not_found"
    APPROVAL_ALREADY_DECIDED = "approval_already_decided"
    UNDELIVERED = "undelivered"
    DISPATCH_FAILED = "dispatch_failed"
    INVALID_COMMAND = "invalid_command"


ACCEPTED_CODES = frozenset({ResultCode.FOCUSED, ResultCode.DISPATCHED})

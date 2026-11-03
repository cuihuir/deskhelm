from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any

from .control import ControlCommand, ControlKind, ControlUndelivered
from .control_router import CatalogProvider, ControlHandler, ControlRouter
from .event import PROTOCOL_VERSION, ProtocolError
from .session_registry import SessionKey


DEFAULT_RUNTIME_CAPACITY = 32
MAX_IDENTITY_CHARS = 512
MAX_SOURCE_VERSION_CHARS = 128
MAX_SOCKET_CHARS = 4096
ATTACHED = "attached"
ALREADY_ATTACHED = "already_attached"
CAPACITY = "capacity"
INVALID = "invalid"
FAILED = "failed"
DETACHED = "detached"
NOT_ATTACHED = "not_attached"
ATTACH_STATUSES = frozenset(
    {ATTACHED, ALREADY_ATTACHED, CAPACITY, INVALID, FAILED, DETACHED}
)
DETACH_STATUSES = frozenset({DETACHED, NOT_ATTACHED, INVALID, FAILED})
_FORBIDDEN_PATH_FIELDS = frozenset(
    {"working_directory", "cwd", "agent_workdir", "workdir"}
)
ATTACHED_CONTROL_KINDS = (
    ControlKind.SUBMIT_PROMPT,
    ControlKind.INTERRUPT,
    ControlKind.APPROVE,
    ControlKind.REJECT,
    ControlKind.CONFIGURE_SESSION,
)
RUNTIME_KIND_CODEX = "codex_app_server"
RUNTIME_KIND_CLAUDE = "claude_stream"
RUNTIME_KIND_GROK = "grok_stdio"
RUNTIME_KINDS = frozenset(
    {RUNTIME_KIND_CODEX, RUNTIME_KIND_CLAUDE, RUNTIME_KIND_GROK}
)

RuntimeFactory = Callable[["RuntimeAttachSpec"], Any]
VoiceEnabled = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class RuntimeAttachSpec:
    session: SessionKey
    source_version: str
    app_server_socket: Path | None = None
    tui_proxy_socket: Path | None = None
    runtime_kind: str = RUNTIME_KIND_CODEX
    relay_socket: Path | None = None

    def __post_init__(self) -> None:
        if self.runtime_kind not in RUNTIME_KINDS:
            raise ValueError("runtime kind is invalid")
        if self.runtime_kind == RUNTIME_KIND_CODEX:
            if self.relay_socket is not None:
                raise ValueError("codex runtime does not use a relay socket")
            if self.app_server_socket is None or self.tui_proxy_socket is None:
                raise ValueError("codex runtime requires both sockets")
            return
        if self.app_server_socket is not None or self.tui_proxy_socket is not None:
            raise ValueError("relay runtime does not use Codex sockets")
        if self.relay_socket is None:
            raise ValueError("relay socket is required")


@dataclass(frozen=True, slots=True)
class AgentRuntimeDirectoryStatus:
    enabled: bool
    voice_enabled: bool
    capacity: int
    attached: int


@dataclass(slots=True)
class _PendingAttach:
    cancel: bool = False


class _RuntimeFacade:
    """Register one runtime's handlers without taking the router's only slot."""

    def __init__(self, directory: AgentRuntimeDirectory, session: SessionKey) -> None:
        self._directory = directory
        self._session = session

    def register_handler(
        self, kind: ControlKind, handler: ControlHandler
    ) -> Callable[[], None]:
        return self._directory.register_session_handler(self._session, kind, handler)

    def register_catalog_provider(self, provider: CatalogProvider) -> Callable[[], None]:
        return self._directory.register_session_catalog(self._session, provider)

    def resolve_approval(
        self, request_id: str, *, now_ms: int | None = None
    ) -> bool:
        return self._directory.control_router.resolve_approval(
            request_id, now_ms=now_ms
        )


@dataclass(slots=True)
class AgentRuntimeDirectory:
    """Own one attached runtime per session and dispatch by session key."""

    control_router: ControlRouter
    factory: RuntimeFactory
    runtime_root: Path
    bridge_socket: Path
    capacity: int = DEFAULT_RUNTIME_CAPACITY
    voice_enabled: VoiceEnabled = field(default=lambda: False, repr=False)
    _handlers: dict[SessionKey, dict[ControlKind, ControlHandler]] = field(
        default_factory=dict, init=False, repr=False
    )
    _catalogs: dict[SessionKey, CatalogProvider] = field(
        default_factory=dict, init=False, repr=False
    )
    _attached: dict[SessionKey, Any] = field(
        default_factory=dict, init=False, repr=False
    )
    _pending: dict[SessionKey, _PendingAttach] = field(
        default_factory=dict, init=False, repr=False
    )
    _unregisters: list[Callable[[], None]] = field(
        default_factory=list, init=False, repr=False
    )
    _installed: bool = field(default=False, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not callable(self.factory):
            raise ValueError("runtime factory must be callable")
        if not callable(self.voice_enabled):
            raise ValueError("voice_enabled must be callable")
        if not isinstance(self.capacity, int) or isinstance(self.capacity, bool):
            raise ValueError("runtime capacity must be an integer")
        if self.capacity < 1:
            raise ValueError("runtime capacity must be at least 1")
        if not isinstance(self.runtime_root, Path) or not isinstance(
            self.bridge_socket, Path
        ):
            raise ValueError("runtime paths must be Path values")
        self.runtime_root = self.runtime_root.expanduser().resolve()
        self.bridge_socket = self.bridge_socket.expanduser().resolve()

    def install(self) -> None:
        if self._installed:
            raise RuntimeError("runtime directory is already installed")
        unregister: list[Callable[[], None]] = []
        try:
            for kind in ATTACHED_CONTROL_KINDS:
                unregister.append(
                    self.control_router.register_handler(kind, self._dispatcher(kind))
                )
            unregister.append(
                self.control_router.register_catalog_provider(self._catalog)
            )
        except BaseException:
            for callback in reversed(unregister):
                callback()
            raise
        self._unregisters = unregister
        self._installed = True

    def status(self) -> AgentRuntimeDirectoryStatus:
        with self._lock:
            attached = len(self._attached)
        return AgentRuntimeDirectoryStatus(
            enabled=True,
            voice_enabled=bool(self.voice_enabled()),
            capacity=self.capacity,
            attached=attached,
        )

    def attach_frame(
        self,
        value: Mapping[str, Any],
        publish: Callable[..., None],
    ) -> str:
        if value.get("protocol_version") != PROTOCOL_VERSION:
            raise ProtocolError("agent runtime attach protocol version is invalid")
        try:
            spec = self._spec_from_frame(value)
        except ValueError:
            return INVALID
        return self._attach(spec, publish)

    def detach_frame(self, value: Mapping[str, Any]) -> str:
        if value.get("protocol_version") != PROTOCOL_VERSION:
            raise ProtocolError("agent runtime detach protocol version is invalid")
        try:
            session = _session_from_frame(value)
        except ValueError:
            return INVALID
        return self._detach(session)

    def register_session_handler(
        self,
        session: SessionKey,
        kind: ControlKind,
        handler: ControlHandler,
    ) -> Callable[[], None]:
        if kind not in ATTACHED_CONTROL_KINDS:
            raise ValueError("control kind is not available on an attached runtime")
        if not callable(handler):
            raise ValueError("control handler must be callable")
        with self._lock:
            self._require_open(session)
            bucket = self._handlers.setdefault(session, {})
            if kind in bucket:
                raise ValueError(f"handler for {kind.value} is already registered")
            bucket[kind] = handler

        def unregister() -> None:
            with self._lock:
                current = self._handlers.get(session)
                if current is not None and current.get(kind) is handler:
                    del current[kind]

        return unregister

    def register_session_catalog(
        self, session: SessionKey, provider: CatalogProvider
    ) -> Callable[[], None]:
        if not callable(provider):
            raise ValueError("catalog provider must be callable")
        with self._lock:
            self._require_open(session)
            if session in self._catalogs:
                raise ValueError("catalog provider is already registered")
            self._catalogs[session] = provider

        def unregister() -> None:
            with self._lock:
                if self._catalogs.get(session) is provider:
                    del self._catalogs[session]

        return unregister

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            attached = list(self._attached.values())
            self._attached.clear()
            for pending in self._pending.values():
                pending.cancel = True
            self._pending.clear()
            self._handlers.clear()
            self._catalogs.clear()
            unregister = tuple(self._unregisters)
            self._unregisters.clear()
        for runtime in attached:
            _close_runtime(runtime)
        for callback in unregister:
            callback()

    def _attach(self, spec: RuntimeAttachSpec, publish: Callable[..., None]) -> str:
        with self._lock:
            if self._closed:
                return FAILED
            if spec.session in self._attached or spec.session in self._pending:
                return ALREADY_ATTACHED
            if len(self._attached) + len(self._pending) >= self.capacity:
                return CAPACITY
            self._pending[spec.session] = _PendingAttach()
        runtime = None
        try:
            runtime = self.factory(spec)
            if runtime is None or not hasattr(runtime, "start"):
                raise RuntimeError("runtime factory returned nothing")
            runtime.start(publish, _RuntimeFacade(self, spec.session))
        except Exception:
            cancelled = self._drop(spec.session)
            _close_runtime(runtime)
            return DETACHED if cancelled else FAILED
        with self._lock:
            pending = self._pending.pop(spec.session, None)
            cancelled = (
                self._closed or pending is None or pending.cancel
            )
            if not cancelled:
                self._attached[spec.session] = runtime
        if cancelled:
            self._drop(spec.session)
            _close_runtime(runtime)
            return DETACHED
        return ATTACHED

    def _detach(self, session: SessionKey) -> str:
        with self._lock:
            pending = self._pending.get(session)
            if pending is not None:
                pending.cancel = True
                return DETACHED
            runtime = self._attached.pop(session, None)
            self._handlers.pop(session, None)
            self._catalogs.pop(session, None)
        if runtime is None:
            return NOT_ATTACHED
        try:
            runtime.close()
        except Exception:
            return FAILED
        return DETACHED

    def _drop(self, session: SessionKey) -> bool:
        with self._lock:
            pending = self._pending.pop(session, None)
            self._handlers.pop(session, None)
            self._catalogs.pop(session, None)
            return pending is not None and pending.cancel

    def _require_open(self, session: SessionKey) -> None:
        if session not in self._pending and session not in self._attached:
            raise RuntimeError("runtime session is not attached")

    def _dispatcher(self, kind: ControlKind) -> ControlHandler:
        def dispatch(command: ControlCommand) -> None:
            self._dispatch(kind, command)

        return dispatch

    def _dispatch(self, kind: ControlKind, command: ControlCommand) -> None:
        session = SessionKey(command.agent_id, command.session_id, command.project_id)
        with self._lock:
            if session in self._pending:
                raise ControlUndelivered("runtime is still attaching")
            handler = self._handlers.get(session, {}).get(kind)
        if handler is None:
            raise ControlUndelivered("runtime is not attached")
        handler(command)

    def _catalog(
        self, agent_id: str, session_id: str, project_id: str
    ) -> Mapping[str, Any] | None:
        session = SessionKey(agent_id, session_id, project_id)
        with self._lock:
            if session in self._pending:
                return None
            provider = self._catalogs.get(session)
        if provider is None:
            return None
        return provider(agent_id, session_id, project_id)

    def _spec_from_frame(self, value: Mapping[str, Any]) -> RuntimeAttachSpec:
        if any(name in value for name in _FORBIDDEN_PATH_FIELDS):
            raise ValueError("runtime attach must not carry a working directory")
        session = _session_from_frame(value)
        source_version = value.get("source_version")
        if (
            not isinstance(source_version, str)
            or not source_version.strip()
            or len(source_version) > MAX_SOURCE_VERSION_CHARS
            or any(ord(character) < 32 for character in source_version)
        ):
            raise ValueError("source_version is invalid")
        kind = value.get("runtime_kind", RUNTIME_KIND_CODEX)
        if not isinstance(kind, str) or kind not in RUNTIME_KINDS:
            raise ValueError("runtime kind is invalid")
        if kind == RUNTIME_KIND_CODEX:
            return self._codex_spec(value, session, source_version.strip())
        return self._relay_spec(value, session, source_version.strip(), kind)

    def _codex_spec(
        self,
        value: Mapping[str, Any],
        session: SessionKey,
        source_version: str,
    ) -> RuntimeAttachSpec:
        if "relay_socket" in value:
            raise ValueError("codex runtime does not use a relay socket")
        app_server_socket = _confine_socket(
            value.get("app_server_socket"), self.runtime_root
        )
        tui_proxy_socket = _confine_socket(
            value.get("tui_proxy_socket"), self.runtime_root
        )
        if app_server_socket in {tui_proxy_socket, self.bridge_socket}:
            raise ValueError("runtime sockets must differ")
        if tui_proxy_socket == self.bridge_socket:
            raise ValueError("runtime sockets must differ")
        if not app_server_socket.is_socket():
            raise ValueError("app-server socket is unavailable")
        if tui_proxy_socket.exists() or tui_proxy_socket.is_symlink():
            raise ValueError("TUI proxy socket already exists")
        return RuntimeAttachSpec(
            session=session,
            source_version=source_version,
            app_server_socket=app_server_socket,
            tui_proxy_socket=tui_proxy_socket,
            runtime_kind=RUNTIME_KIND_CODEX,
        )

    def _relay_spec(
        self,
        value: Mapping[str, Any],
        session: SessionKey,
        source_version: str,
        kind: str,
    ) -> RuntimeAttachSpec:
        if "app_server_socket" in value or "tui_proxy_socket" in value:
            raise ValueError("relay runtime does not use Codex sockets")
        relay_socket = _confine_socket(value.get("relay_socket"), self.runtime_root)
        if relay_socket == self.bridge_socket:
            raise ValueError("runtime sockets must differ")
        if not relay_socket.is_socket():
            raise ValueError("relay socket is unavailable")
        return RuntimeAttachSpec(
            session=session,
            source_version=source_version,
            runtime_kind=kind,
            relay_socket=relay_socket,
        )


def _session_from_frame(value: Mapping[str, Any]) -> SessionKey:
    return SessionKey(
        agent_id=_identity(value.get("agent_id"), "agent_id"),
        session_id=_identity(value.get("session_id"), "session_id"),
        project_id=_identity(value.get("project_id"), "project_id"),
    )


def _identity(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    if len(value) > MAX_IDENTITY_CHARS or any(
        character.isspace() or ord(character) < 32 for character in value
    ):
        raise ValueError(f"{field_name} is invalid")
    return value


def _confine_socket(value: object, root: Path) -> Path:
    """Keep the named path inside the runtime directory.

    Codex app-server publishes its listen path as a symlink to a daemon socket
    outside that directory. Confinement therefore resolves the parent only.
    Callers still require the final path to be a socket, which follows that
    one symlink.
    """
    if not isinstance(value, str) or not value or len(value) > MAX_SOCKET_CHARS:
        raise ValueError("runtime socket path is invalid")
    if "\x00" in value:
        raise ValueError("runtime socket path is invalid")
    path = Path(value)
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        raise ValueError("runtime socket path must be absolute")
    parent = path.parent.resolve()
    if not parent.is_relative_to(root):
        raise ValueError("runtime socket path is outside the runtime directory")
    return parent / path.name


def _close_runtime(runtime: Any) -> None:
    if runtime is None:
        return
    close = getattr(runtime, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        return

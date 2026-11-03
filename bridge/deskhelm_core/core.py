"""The single-threaded Bridge core (ADR 0043).

The core owns session facts, focus, idempotency receipts, approval tickets,
the tier policy, and the projections that clients subscribe to. It performs
no I/O and takes no locks. The server feeds it one input at a time and
delivers the frames it returns.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
import json
from typing import Any

from deskhelm_protocol import (
    ACCEPTED_CODES,
    PROTOCOL_VERSION,
    ApprovalSupport,
    CapabilityMatrix,
    Command,
    CommandKind,
    EffectKind,
    EffectOutcome,
    Fact,
    FactKind,
    MessageType,
    OpenRequest,
    PromptWhileBusy,
    ProtocolViolation,
    RequestKind,
    ResultCode,
    Role,
    RuntimeIdentity,
    SessionKey,
    Stream,
    Tier,
    TurnStatus,
)
from deskhelm_protocol.model import MAX_OPEN_REQUESTS, enum_field, identity_field


@dataclass(frozen=True, slots=True)
class Send:
    connection: str
    frame: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Close:
    connection: str
    code: str


Output = Send | Close


class Activity(StrEnum):
    STARTING = "starting"
    IDLE = "idle"
    WORKING = "working"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_INPUT = "waiting_input"


class Lifecycle(StrEnum):
    ACTIVE = "active"
    DISCONNECTED = "disconnected"


_BUSY = frozenset({Activity.WORKING, Activity.WAITING_APPROVAL, Activity.WAITING_INPUT})

_TIER_COMMANDS = {
    Tier.OPERATOR: frozenset(CommandKind),
    Tier.VOICE: frozenset({CommandKind.SUBMIT_PROMPT, CommandKind.APPROVE, CommandKind.REJECT}),
    Tier.DEVICE: frozenset(
        {CommandKind.FOCUS, CommandKind.INTERRUPT, CommandKind.APPROVE, CommandKind.REJECT}
    ),
    Tier.OBSERVER: frozenset(),
}


@dataclass(frozen=True, slots=True)
class CoreLimits:
    max_sessions: int = 32
    max_hosts: int = 32
    max_clients: int = 16
    max_receipts: int = 1024
    receipt_retention_ms: int = 300_000
    effect_deadline_ms: int = 5_000
    disconnected_ttl_ms: int = 120_000
    delta_history: int = 256
    window_events: int = 200
    window_bytes: int = 256 * 1024
    max_tickets: int = 16


class _Refused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class Ticket:
    approval_id: str
    provider_ref: str
    kind: RequestKind
    summary: str
    expires_at: int
    answerable: bool
    reserved: bool = False
    # How the vendor resolved it while a DeskHelm decision was in flight.
    resolved_by: str = ""

    def view(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "kind": self.kind.value,
            "summary": self.summary,
            "expires_at": self.expires_at,
            "answerable": self.answerable,
        }


@dataclass(slots=True)
class SessionRecord:
    key: SessionKey
    owner: str | None
    runtime: RuntimeIdentity
    capabilities: CapabilityMatrix
    opened_at: int
    lifecycle: Lifecycle = Lifecycle.ACTIVE
    activity: Activity = Activity.STARTING
    ready: bool = False
    turn_id: str | None = None
    turn_correlation: str | None = None
    last_outcome: dict[str, Any] | None = None
    tickets: dict[str, Ticket] = field(default_factory=dict)
    decided: dict[str, int] = field(default_factory=dict)
    attention: bool = False
    host_seq: int = 0
    disconnected_at: int | None = None
    catalog: dict[str, Any] | None = None
    # Counts the catalogs the host pushed, so clients know when to read again.
    catalog_rev: int = 0
    window: deque[dict[str, Any]] = field(default_factory=deque)
    window_bytes: int = 0
    # The view the console last carried, so an unchanged session sends no delta.
    sent_view: dict[str, Any] | None = None

    def ticket_for(self, provider_ref: str) -> Ticket | None:
        return next(
            (ticket for ticket in self.tickets.values() if ticket.provider_ref == provider_ref),
            None,
        )

    def view(self) -> dict[str, Any]:
        return {
            **self.key.to_dict(),
            "runtime": self.runtime.to_dict(),
            "capabilities": self.capabilities.to_dict(),
            "lifecycle": self.lifecycle.value,
            "activity": self.activity.value,
            "turn_id": self.turn_id,
            "last_outcome": self.last_outcome,
            "approvals": [ticket.view() for ticket in self.tickets.values()],
            "attention": self.attention,
            "opened_at": self.opened_at,
            "catalog_rev": self.catalog_rev,
        }


@dataclass(slots=True)
class _Connection:
    connection_id: str
    role: Role | None = None
    client_id: str = ""
    tier: Tier | None = None
    session: SessionKey | None = None
    streams: set[Stream] = field(default_factory=set)
    interaction_filter: frozenset[SessionKey] | None = None


@dataclass(slots=True)
class _Receipt:
    fingerprint: str
    retained_until: int
    result: dict[str, Any] | None = None
    waiters: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _InFlight:
    effect_id: str
    command: Command
    session: SessionKey
    deadline: int
    approval_id: str | None
    scope: tuple[str, str]


class Core:
    """Decide every Bridge outcome from one input at a time."""

    def __init__(self, instance_id: str, limits: CoreLimits | None = None) -> None:
        self.instance_id = identity_field(instance_id, "instance_id")
        self.limits = limits or CoreLimits()
        self._connections: dict[str, _Connection] = {}
        self._sessions: dict[SessionKey, SessionRecord] = {}
        self._focus: SessionKey | None = None
        self._receipts: dict[tuple[str, str], _Receipt] = {}
        self._in_flight: dict[str, _InFlight] = {}
        self._counter = 0
        self._console_seq = 0
        self._interaction_seq = 0
        self._deltas: deque[tuple[int, list[dict[str, Any]]]] = deque(
            maxlen=self.limits.delta_history
        )
        self._out: list[Output] = []
        self._dirty: set[SessionKey] = set()
        self._removed: list[SessionKey] = []
        self._focus_dirty = False

    # Inputs ------------------------------------------------------------

    def connect(self, connection: str) -> list[Output]:
        self._connections[connection] = _Connection(connection)
        return []

    def receive(self, connection: str, frame: dict[str, Any], now: int) -> list[Output]:
        state = self._connections.get(connection)
        if state is None:
            return []
        try:
            if state.role is None:
                self._hello(state, frame)
            elif state.role is Role.HOST:
                self._host_frame(state, frame, now)
            else:
                self._client_frame(state, frame, now)
        except ProtocolViolation as error:
            self._refuse(state, "protocol_violation", str(error), now)
        except _Refused as error:
            self._refuse(state, error.code, str(error), now)
        return self._finish()

    def disconnect(self, connection: str, now: int) -> list[Output]:
        state = self._connections.pop(connection, None)
        if state is not None:
            self._forget(state, now)
        return self._finish()

    def tick(self, now: int) -> list[Output]:
        for flight in [flight for flight in self._in_flight.values() if flight.deadline <= now]:
            self._settle(flight, ResultCode.DISPATCH_FAILED, delivered=True, now=now)
        for record in list(self._sessions.values()):
            expired = [
                approval_id
                for approval_id, ticket in record.tickets.items()
                if ticket.expires_at <= now and not ticket.reserved
            ]
            for approval_id in expired:
                self._resolved(record, record.tickets.pop(approval_id), "expired", now)
            record.decided = {
                approval_id: expires_at
                for approval_id, expires_at in record.decided.items()
                if expires_at > now
            }
            if expired:
                self._recompute(record)
            if (
                record.lifecycle is Lifecycle.DISCONNECTED
                and record.disconnected_at is not None
                and now - record.disconnected_at >= self.limits.disconnected_ttl_ms
            ):
                self._remove_session(record, now)
        for scope in [
            scope
            for scope, receipt in self._receipts.items()
            if receipt.result is not None and receipt.retained_until <= now
        ]:
            del self._receipts[scope]
        return self._finish()

    # Inspection --------------------------------------------------------

    def session(self, key: SessionKey) -> dict[str, Any] | None:
        record = self._sessions.get(key)
        return None if record is None else record.view()

    @property
    def focus(self) -> SessionKey | None:
        return self._focus

    @property
    def receipt_count(self) -> int:
        return len(self._receipts)

    # Handshake ---------------------------------------------------------

    def _hello(self, state: _Connection, frame: dict[str, Any]) -> None:
        if frame.get("type") != MessageType.HELLO:
            raise ProtocolViolation("the first frame must be hello")
        if frame.get("protocol_version") != PROTOCOL_VERSION:
            raise _Refused("unsupported_version", "the Bridge speaks protocol version 2")
        role = enum_field(Role, frame.get("role"), "role")
        client_id = identity_field(frame.get("client_id"), "client_id")
        if role is Role.CLIENT:
            tier = enum_field(Tier, frame.get("tier"), "tier")
            limit = self.limits.max_clients
        else:
            if "tier" in frame:
                raise ProtocolViolation("a host has no tier")
            tier = None
            limit = self.limits.max_hosts
        if sum(1 for other in self._connections.values() if other.role is role) >= limit:
            raise _Refused("capacity", f"too many {role.value} connections")
        state.role, state.client_id, state.tier = role, client_id, tier
        self._send(
            state.connection_id,
            {
                "type": MessageType.HELLO_OK.value,
                "protocol_version": PROTOCOL_VERSION,
                "connection_id": state.connection_id,
                "instance_id": self.instance_id,
            },
        )

    # Hosts -------------------------------------------------------------

    def _host_frame(self, state: _Connection, frame: dict[str, Any], now: int) -> None:
        kind = frame.get("type")
        if kind == MessageType.SESSION_OPEN:
            self._session_open(state, frame, now)
            return
        record = self._owned(state)
        if kind == MessageType.FACT:
            self._fact(record, Fact.from_dict(frame), now)
        elif kind == MessageType.EFFECT_RESULT:
            self._effect_result(record, frame, now)
        elif kind == MessageType.SESSION_CLOSE:
            self._remove_session(record, now)
            state.session = None
        else:
            raise ProtocolViolation(f"a host cannot send {kind}")

    def _owned(self, state: _Connection) -> SessionRecord:
        record = self._sessions.get(state.session) if state.session else None
        if record is None or record.owner != state.connection_id:
            raise ProtocolViolation("open a session first")
        return record

    def _session_open(self, state: _Connection, frame: dict[str, Any], now: int) -> None:
        if state.session is not None:
            raise ProtocolViolation("this connection already opened a session")
        key = SessionKey.from_dict(frame.get("session"))
        runtime = RuntimeIdentity.from_dict(frame.get("runtime"))
        capabilities = CapabilityMatrix.from_dict(frame.get("capabilities"))
        raw_requests = frame.get("open_requests", [])
        if not isinstance(raw_requests, list) or len(raw_requests) > MAX_OPEN_REQUESTS:
            raise ProtocolViolation("open_requests must be a short list")
        requests = [OpenRequest.from_dict(item) for item in raw_requests]
        record = self._sessions.get(key)
        if record is not None and record.lifecycle is Lifecycle.ACTIVE:
            self._send(state.connection_id, _open_result("rejected", "already_active"))
            raise _Refused("already_active", "the session is open on another connection")
        if record is None and len(self._sessions) >= self.limits.max_sessions:
            self._send(state.connection_id, _open_result("rejected", "capacity"))
            raise _Refused("capacity", "the Bridge holds its maximum number of sessions")
        if record is None:
            record = SessionRecord(
                key=key,
                owner=state.connection_id,
                runtime=runtime,
                capabilities=capabilities,
                opened_at=now,
            )
            self._sessions[key] = record
        else:
            # A replacement host reopens a disconnected session.
            record.owner = state.connection_id
            record.runtime, record.capabilities = runtime, capabilities
            record.lifecycle, record.disconnected_at = Lifecycle.ACTIVE, None
            record.host_seq, record.ready = 0, False
            record.tickets.clear()
            record.attention = False
        state.session = key
        for request in requests:
            self._open_ticket(record, request, now, announce=False)
        self._recompute(record)
        self._send(state.connection_id, _open_result("opened", ""))

    def _fact(self, record: SessionRecord, fact: Fact, now: int) -> None:
        if fact.seq != record.host_seq + 1:
            raise ProtocolViolation("fact sequence has a gap")
        record.host_seq = fact.seq
        meta, content = fact.meta, fact.content
        if fact.kind is FactKind.READY:
            record.ready = True
        elif fact.kind is FactKind.TURN_STARTED:
            record.ready = True
            record.turn_id = self._new_id("t")
            record.turn_correlation = meta.get("command_id") or record.turn_id
        elif fact.kind is FactKind.MESSAGE:
            payload = {"role": meta["role"], "phase": meta["phase"], "text": content.get("text", "")}
            correlation = meta.get("command_id") or meta["message_ref"]
            self._interaction(record, "message", payload, correlation, now)
        elif fact.kind is FactKind.TOOL:
            payload = {
                "tool_call_id": meta["tool_ref"],
                "name": meta["label"],
                "phase": meta["phase"],
                "text": content.get("text", ""),
                "stream": meta.get("stream"),
                "exit_code": meta.get("exit_code"),
            }
            if meta.get("replace"):
                payload["replace"] = True
            self._interaction(record, "tool", payload, meta["tool_ref"], now)
        elif fact.kind is FactKind.REQUEST_OPENED:
            self._open_ticket(record, OpenRequest.from_dict(meta), now, announce=True)
        elif fact.kind is FactKind.REQUEST_RESOLVED:
            ticket = record.ticket_for(meta["provider_ref"])
            if ticket is not None:
                if ticket.reserved:
                    ticket.resolved_by = meta["by"]
                else:
                    del record.tickets[ticket.approval_id]
                    record.decided[ticket.approval_id] = ticket.expires_at
                    self._resolved(record, ticket, meta["by"], now)
        elif fact.kind is FactKind.TURN_TERMINAL:
            self._turn_terminal(record, meta, content, now)
        elif fact.kind is FactKind.STATUS:
            if record.attention != meta["attention"]:
                record.attention = meta["attention"]
                payload = {"reason": "approval", "active": record.attention}
                self._interaction(record, "attention", payload, "", now)
        elif fact.kind is FactKind.CATALOG:
            record.catalog = content["catalog"]
            record.catalog_rev += 1
        elif fact.kind is FactKind.CAPABILITIES:
            # A driver may learn what its vendor allows only after the handshake.
            record.capabilities = CapabilityMatrix.from_dict(meta["capabilities"])
        elif fact.kind is FactKind.SESSION_ENDED:
            self._remove_session(record, now)
            return
        self._recompute(record)

    def _open_ticket(
        self, record: SessionRecord, request: OpenRequest, now: int, *, announce: bool
    ) -> None:
        if record.ticket_for(request.provider_ref) is not None:
            return
        if len(record.tickets) >= self.limits.max_tickets or request.expires_at <= now:
            return
        ticket = Ticket(
            approval_id=self._new_id("a"),
            provider_ref=request.provider_ref,
            kind=request.kind,
            summary=request.summary,
            expires_at=request.expires_at,
            answerable=request.answerable,
        )
        record.tickets[ticket.approval_id] = ticket
        if not announce:
            return
        if request.kind is RequestKind.APPROVAL:
            payload = {
                "request_id": ticket.approval_id,
                "summary": ticket.summary,
                "expires_at": ticket.expires_at,
            }
            self._interaction(record, "approval_request", payload, ticket.approval_id, now)
        else:
            payload = {"prompt": ticket.summary}
            self._interaction(record, "user_input_required", payload, ticket.approval_id, now)

    def _turn_terminal(
        self, record: SessionRecord, meta: dict[str, Any], content: dict[str, Any], now: int
    ) -> None:
        status = TurnStatus(meta["status"])
        failure = meta.get("failure")
        record.last_outcome = {
            "status": status.value,
            "failure": failure,
            "reusable": meta.get("reusable", True),
            "turn_id": record.turn_id,
        }
        correlation = record.turn_correlation or ""
        record.turn_id = record.turn_correlation = None
        record.attention = False
        for approval_id in [
            approval_id for approval_id, ticket in record.tickets.items() if not ticket.reserved
        ]:
            self._resolved(record, record.tickets.pop(approval_id), "cancelled", now)
        message = content.get("message", "")
        if status is TurnStatus.COMPLETED:
            payload = {"message": message, "error_code": ""}
            self._interaction(record, "task_completed", payload, correlation, now)
        else:
            code = "interrupted" if status is TurnStatus.INTERRUPTED else failure or "agent_failed"
            payload = {"message": message, "error_code": code}
            self._interaction(record, "task_failed", payload, correlation, now)

    def _effect_result(self, record: SessionRecord, frame: dict[str, Any], now: int) -> None:
        effect_id = identity_field(frame.get("effect_id"), "effect_id")
        outcome = enum_field(EffectOutcome, frame.get("outcome"), "outcome")
        flight = self._in_flight.get(effect_id)
        if flight is None or flight.session != record.key:
            return
        if outcome is EffectOutcome.ACCEPTED:
            self._settle(flight, ResultCode.DISPATCHED, delivered=True, now=now)
        elif outcome is EffectOutcome.UNDELIVERED:
            self._settle(flight, ResultCode.UNDELIVERED, delivered=False, now=now)
        else:
            self._settle(flight, ResultCode.UNSUPPORTED, delivered=False, now=now)

    # Clients -----------------------------------------------------------

    def _client_frame(self, state: _Connection, frame: dict[str, Any], now: int) -> None:
        kind = frame.get("type")
        if kind == MessageType.SUBSCRIBE:
            self._subscribe(state, frame)
        elif kind == MessageType.COMMAND:
            self._command(state, frame.get("command"), now)
        elif kind == MessageType.QUERY:
            self._query(state, frame)
        else:
            raise ProtocolViolation(f"a client cannot send {kind}")

    def _subscribe(self, state: _Connection, frame: dict[str, Any]) -> None:
        stream = enum_field(Stream, frame.get("stream"), "stream")
        if stream in state.streams:
            raise ProtocolViolation("already subscribed to this stream")
        if stream is Stream.INTERACTION:
            self._subscribe_interaction(state, frame)
        else:
            self._subscribe_console(state, frame)
        state.streams.add(stream)

    def _subscribe_console(self, state: _Connection, frame: dict[str, Any]) -> None:
        after = frame.get("after")
        if isinstance(after, dict) and after.get("instance_id") == self.instance_id:
            seq = after.get("seq")
            oldest = self._deltas[0][0] if self._deltas else self._console_seq + 1
            if isinstance(seq, int) and not isinstance(seq, bool) and oldest - 1 <= seq <= self._console_seq:
                for delta_seq, changes in self._deltas:
                    if delta_seq > seq:
                        self._send(state.connection_id, self._delta_frame(delta_seq, changes))
                return
        self._send(
            state.connection_id,
            {
                "type": MessageType.SNAPSHOT.value,
                "stream": Stream.CONSOLE.value,
                "instance_id": self.instance_id,
                "seq": self._console_seq,
                "state": {
                    "focus": None if self._focus is None else self._focus.to_dict(),
                    "sessions": [record.view() for record in self._sessions.values()],
                },
            },
        )

    def _subscribe_interaction(self, state: _Connection, frame: dict[str, Any]) -> None:
        if state.tier is not Tier.OPERATOR:
            raise _Refused("not_permitted", "only the operator tier reads interaction content")
        raw = frame.get("sessions")
        if raw is not None:
            if not isinstance(raw, list) or len(raw) > self.limits.max_sessions:
                raise ProtocolViolation("sessions must be a short list")
            state.interaction_filter = frozenset(SessionKey.from_dict(item) for item in raw)
        window = sorted(
            (
                event
                for record in self._sessions.values()
                if _wanted(state, record.key)
                for event in record.window
            ),
            key=lambda event: event["sequence"],
        )
        self._send(
            state.connection_id,
            {
                "type": MessageType.SNAPSHOT.value,
                "stream": Stream.INTERACTION.value,
                "instance_id": self.instance_id,
                "seq": self._interaction_seq,
                "events": len(window),
            },
        )
        for event in window:
            self._send(
                state.connection_id,
                {
                    "type": MessageType.INTERACTION.value,
                    "seq": event["sequence"],
                    "replay": True,
                    "event": event,
                },
            )

    def _command(self, state: _Connection, raw: Any, now: int) -> None:
        try:
            command = Command.from_dict(raw)
        except ProtocolViolation:
            command_id = raw.get("command_id") if isinstance(raw, dict) else None
            self._result(state.connection_id, command_id if isinstance(command_id, str) else "", ResultCode.INVALID_COMMAND)
            return
        connection = state.connection_id
        if command.issued_by != state.client_id:
            self._result(connection, command.command_id, ResultCode.ISSUER_MISMATCH)
            return
        if now >= command.expires_at:
            self._result(connection, command.command_id, ResultCode.EXPIRED)
            return
        if state.tier is None or command.kind not in _TIER_COMMANDS[state.tier]:
            self._result(connection, command.command_id, ResultCode.NOT_PERMITTED)
            return
        scope = (command.issued_by, command.idempotency_key)
        fingerprint = command.fingerprint()
        receipt = self._receipts.get(scope)
        if receipt is not None:
            if receipt.fingerprint != fingerprint:
                self._result(connection, command.command_id, ResultCode.IDEMPOTENCY_CONFLICT)
            elif receipt.result is not None:
                self._send(connection, {**receipt.result, "duplicate": True})
            else:
                receipt.waiters.append(connection)
            return
        record = self._sessions.get(command.target)
        if record is None:
            self._result(connection, command.command_id, ResultCode.TARGET_NOT_FOUND)
            return
        if record.lifecycle is not Lifecycle.ACTIVE or record.owner is None:
            self._result(connection, command.command_id, ResultCode.TARGET_INACTIVE)
            return
        if len(self._receipts) >= self.limits.max_receipts:
            self._result(connection, command.command_id, ResultCode.IDEMPOTENCY_CAPACITY)
            return
        retained_until = max(command.expires_at, now + self.limits.receipt_retention_ms)
        if command.kind is CommandKind.FOCUS:
            self._set_focus(record.key)
            result = _result_frame(command.command_id, ResultCode.FOCUSED)
            self._receipts[scope] = _Receipt(fingerprint, retained_until, result=result)
            self._send(connection, {**result, "duplicate": False})
            return
        plan = self._plan_effect(record, command, now)
        if isinstance(plan, ResultCode):
            self._result(connection, command.command_id, plan)
            return
        kind, payload, approval_id = plan
        effect_id = self._new_id("e")
        self._receipts[scope] = _Receipt(fingerprint, retained_until, waiters=[connection])
        self._in_flight[effect_id] = _InFlight(
            effect_id, command, record.key, now + self.limits.effect_deadline_ms, approval_id, scope
        )
        if approval_id is not None:
            record.tickets[approval_id].reserved = True
        self._send(
            record.owner,
            {
                "type": MessageType.EFFECT.value,
                "effect_id": effect_id,
                "kind": kind.value,
                "payload": {"command_id": command.command_id, **payload},
            },
        )

    def _plan_effect(
        self, record: SessionRecord, command: Command, now: int
    ) -> tuple[EffectKind, dict[str, Any], str | None] | ResultCode:
        capabilities = record.capabilities
        if command.kind is CommandKind.SUBMIT_PROMPT:
            if record.activity in _BUSY and capabilities.prompt_while_busy is PromptWhileBusy.REJECT:
                return ResultCode.UNSUPPORTED
            return EffectKind.SUBMIT, {"text": command.payload["text"]}, None
        if command.kind is CommandKind.INTERRUPT:
            if not capabilities.interrupt:
                return ResultCode.UNSUPPORTED
            return EffectKind.INTERRUPT, {}, None
        if command.kind is CommandKind.CONFIGURE_SESSION:
            if not set(command.payload) <= capabilities.configure:
                return ResultCode.UNSUPPORTED
            return EffectKind.CONFIGURE, dict(command.payload), None
        approval_id = command.payload["approval_id"]
        if approval_id in record.decided:
            return ResultCode.APPROVAL_ALREADY_DECIDED
        ticket = record.tickets.get(approval_id)
        if ticket is None or ticket.kind is not RequestKind.APPROVAL or ticket.expires_at <= now:
            return ResultCode.APPROVAL_NOT_FOUND
        if ticket.reserved:
            return ResultCode.APPROVAL_ALREADY_DECIDED
        if not ticket.answerable or capabilities.approvals is not ApprovalSupport.ANSWERABLE:
            return ResultCode.UNSUPPORTED
        decision = "approve" if command.kind is CommandKind.APPROVE else "reject"
        return (
            EffectKind.RESPOND,
            {"provider_ref": ticket.provider_ref, "decision": decision},
            ticket.approval_id,
        )

    def _settle(self, flight: _InFlight, code: ResultCode, *, delivered: bool, now: int) -> None:
        """Answer a dispatched command. An undelivered one leaves no trace."""
        self._in_flight.pop(flight.effect_id, None)
        record = self._sessions.get(flight.session)
        if flight.approval_id is not None and record is not None:
            ticket = record.tickets.get(flight.approval_id)
            if ticket is not None:
                if delivered or ticket.resolved_by:
                    del record.tickets[flight.approval_id]
                    record.decided[flight.approval_id] = ticket.expires_at
                    if delivered:
                        decision = "approve" if flight.command.kind is CommandKind.APPROVE else "reject"
                        unknown = code is not ResultCode.DISPATCHED
                        self._resolved(record, ticket, "deskhelm", now, decision=decision, unknown=unknown)
                    else:
                        self._resolved(record, ticket, ticket.resolved_by, now)
                else:
                    ticket.reserved = False
                self._recompute(record)
        result = _result_frame(flight.command.command_id, code)
        receipt = self._receipts.get(flight.scope)
        if receipt is None:
            return
        for index, connection in enumerate(receipt.waiters):
            self._send(connection, {**result, "duplicate": index > 0})
        receipt.waiters = []
        if delivered:
            receipt.result = result
        else:
            del self._receipts[flight.scope]

    def _query(self, state: _Connection, frame: dict[str, Any]) -> None:
        query_id = identity_field(frame.get("query_id"), "query_id")
        if frame.get("query") != "catalog":
            raise ProtocolViolation("the only query is catalog")
        key = SessionKey.from_dict(frame.get("session"))
        if state.tier is not Tier.OPERATOR:
            status, catalog = "not_permitted", {}
        else:
            record = self._sessions.get(key)
            catalog = None if record is None else record.catalog
            status = "unavailable" if catalog is None else "ok"
        self._send(
            state.connection_id,
            {
                "type": MessageType.QUERY_RESULT.value,
                "query_id": query_id,
                "status": status,
                "catalog": catalog or {},
            },
        )

    # Shared helpers ----------------------------------------------------

    def _recompute(self, record: SessionRecord) -> None:
        kinds = {ticket.kind for ticket in record.tickets.values()}
        if RequestKind.APPROVAL in kinds or record.attention:
            activity = Activity.WAITING_APPROVAL
        elif RequestKind.INPUT in kinds:
            activity = Activity.WAITING_INPUT
        elif record.turn_id is not None:
            activity = Activity.WORKING
        elif record.ready:
            activity = Activity.IDLE
        else:
            activity = Activity.STARTING
        record.activity = activity
        self._dirty.add(record.key)

    def _interaction(
        self,
        record: SessionRecord,
        kind: str,
        payload: dict[str, Any],
        correlation: str,
        now: int,
    ) -> None:
        self._interaction_seq += 1
        event = {
            "event_id": f"{self.instance_id}-{self._interaction_seq}",
            "kind": kind,
            **record.key.to_dict(),
            "sequence": self._interaction_seq,
            "occurred_at": now,
            "correlation_id": correlation,
            "payload": payload,
        }
        if kind == "message" and payload.get("phase") == "complete" and payload.get("text"):
            # A complete message carries its whole text, so its deltas leave the
            # window instead of crowding out earlier activity.
            self._drop_deltas(record, correlation, payload.get("role"))
        size = len(json.dumps(event, ensure_ascii=False))
        record.window.append(event)
        record.window_bytes += size
        while record.window and (
            len(record.window) > self.limits.window_events
            or record.window_bytes > self.limits.window_bytes
        ):
            dropped = record.window.popleft()
            record.window_bytes -= len(json.dumps(dropped, ensure_ascii=False))
        frame = {"type": MessageType.INTERACTION.value, "seq": self._interaction_seq, "event": event}
        for state in self._connections.values():
            if Stream.INTERACTION in state.streams and _wanted(state, record.key):
                self._send(state.connection_id, frame)

    def _resolved(
        self, record: SessionRecord, ticket: Ticket, by: str, now: int, *, decision: str = "", unknown: bool = False
    ) -> None:
        """Tell every surface how an approval ended, so they all say the same."""
        if ticket.kind is not RequestKind.APPROVAL:
            return
        payload: dict[str, Any] = {"request_id": ticket.approval_id, "by": by}
        if decision:
            payload["decision"] = decision
        if unknown:
            payload["unknown"] = True
        self._interaction(record, "approval_resolved", payload, ticket.approval_id, now)

    def _drop_deltas(self, record: SessionRecord, correlation: str, role: object) -> None:
        kept: deque[dict[str, Any]] = deque()
        for event in record.window:
            payload = event["payload"]
            if (
                event["kind"] == "message"
                and event["correlation_id"] == correlation
                and payload.get("phase") == "delta"
                and payload.get("role") == role
            ):
                record.window_bytes -= len(json.dumps(event, ensure_ascii=False))
            else:
                kept.append(event)
        record.window = kept

    def _set_focus(self, key: SessionKey | None) -> None:
        if self._focus != key:
            self._focus = key
            self._focus_dirty = True

    def _remove_session(self, record: SessionRecord, now: int) -> None:
        for flight in [flight for flight in self._in_flight.values() if flight.session == record.key]:
            self._settle(flight, ResultCode.DISPATCH_FAILED, delivered=True, now=now)
        self._sessions.pop(record.key, None)
        self._dirty.discard(record.key)
        self._removed.append(record.key)
        if self._focus == record.key:
            self._set_focus(None)
        for state in self._connections.values():
            if state.session == record.key:
                state.session = None

    def _forget(self, state: _Connection, now: int) -> None:
        for receipt in self._receipts.values():
            receipt.waiters = [waiter for waiter in receipt.waiters if waiter != state.connection_id]
        if state.role is not Role.HOST or state.session is None:
            return
        record = self._sessions.get(state.session)
        if record is None or record.owner != state.connection_id:
            return
        for flight in [flight for flight in self._in_flight.values() if flight.session == record.key]:
            # The effect may already have reached the vendor.
            self._settle(flight, ResultCode.DISPATCH_FAILED, delivered=True, now=now)
        record.owner = None
        record.lifecycle = Lifecycle.DISCONNECTED
        record.disconnected_at = now
        record.tickets.clear()
        record.attention = False
        record.turn_id = record.turn_correlation = None
        if self._focus == record.key:
            self._set_focus(None)
        self._recompute(record)

    def _refuse(self, state: _Connection, code: str, message: str, now: int) -> None:
        self._send(state.connection_id, {"type": MessageType.ERROR.value, "code": code, "message": message})
        self._out.append(Close(state.connection_id, code))
        self._connections.pop(state.connection_id, None)
        self._forget(state, now)

    def _result(self, connection: str, command_id: str, code: ResultCode) -> None:
        self._send(connection, {**_result_frame(command_id, code), "duplicate": False})

    def _send(self, connection: str | None, frame: dict[str, Any]) -> None:
        if connection is not None:
            self._out.append(Send(connection, frame))

    def _new_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self.instance_id}-{self._counter}"

    def _delta_frame(self, seq: int, changes: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "type": MessageType.DELTA.value,
            "stream": Stream.CONSOLE.value,
            "instance_id": self.instance_id,
            "seq": seq,
            "changes": changes,
        }

    def _finish(self) -> list[Output]:
        changes: list[dict[str, Any]] = [
            {"op": "session_removed", **key.to_dict()} for key in self._removed
        ]
        for key in sorted(self._dirty, key=lambda item: (item.agent_id, item.session_id, item.project_id)):
            record = self._sessions.get(key)
            if record is not None:
                view = record.view()
                if view != record.sent_view:
                    record.sent_view = view
                    changes.append({"op": "session", "session": view})
        if self._focus_dirty:
            changes.append({"op": "focus", "focus": None if self._focus is None else self._focus.to_dict()})
        if changes:
            self._console_seq += 1
            self._deltas.append((self._console_seq, changes))
            frame = self._delta_frame(self._console_seq, changes)
            for state in self._connections.values():
                if Stream.CONSOLE in state.streams:
                    self._send(state.connection_id, frame)
        out = self._out
        self._out = []
        self._dirty = set()
        self._removed = []
        self._focus_dirty = False
        return out


def _wanted(state: _Connection, key: SessionKey) -> bool:
    return state.interaction_filter is None or key in state.interaction_filter


def _open_result(status: str, code: str) -> dict[str, Any]:
    return {"type": MessageType.SESSION_OPEN_RESULT.value, "status": status, "code": code}


def _result_frame(command_id: str, code: ResultCode) -> dict[str, Any]:
    return {
        "type": MessageType.COMMAND_RESULT.value,
        "command_id": command_id,
        "status": "accepted" if code in ACCEPTED_CODES else "rejected",
        "code": code.value,
    }


__all__ = [
    "Activity",
    "Close",
    "Core",
    "CoreLimits",
    "Lifecycle",
    "Output",
    "Send",
]

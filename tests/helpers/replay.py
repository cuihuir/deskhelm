"""Replay vendor transcripts through the real adapters.

Transcript format ``deskhelm-replay-v1`` is NDJSON. The first line is a header:

    {"type": "header", "format": "deskhelm-replay-v1", "runtime": "grok",
     "scenario": "simple", "source_type": "synthetic", "notes": "..."}

``source_type`` is ``synthetic`` or ``recorded``. Every later line is a step:

- ``inbound``: a frame the vendor sends to the adapter.
- ``outbound``: a frame the adapter must send next. Keys missing from the
  expected frame are ignored and ``"<any>"`` matches any value. A string that
  starts with ``$`` captures the actual value the first time and must match
  it afterwards; inbound frames substitute captured values.
- ``control``: a DeskHelm control. Kind is ``submit_prompt`` (with ``text``),
  ``interrupt``, ``approve``, or ``reject``. Approvals decide the session's
  single pending request.
- ``events``: the kinds of interaction events published since the previous
  ``events`` step, in order. ``error_code`` checks the last event's task code.

Claude, Grok, and Codex replay through their protocol v2 drivers and an
in-process Bridge core; the Codex driver wraps the app-server adapter that v1
also uses. Only the vendor transport is replaced.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from queue import Empty, Queue
import tempfile
import time
from threading import Condition, Lock
from typing import Any

from deskhelm_core import Core, Send
from deskhelm_host.vendor import VendorClosed
from deskhelm_protocol import EffectKind, FactKind, SessionKey


TRANSCRIPT_FORMAT = "deskhelm-replay-v1"
STEP_TYPES = frozenset({"inbound", "outbound", "control", "events"})
SOURCE_TYPES = frozenset({"synthetic", "recorded"})
STEP_TIMEOUT_SECONDS = 2.0


class ReplayMismatch(AssertionError):
    """The adapter diverged from the transcript."""


@dataclass(frozen=True)
class Transcript:
    path: Path
    header: dict[str, Any]
    steps: tuple[dict[str, Any], ...]


def load_transcript(path: Path) -> Transcript:
    lines = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not lines:
        raise ValueError(f"{path.name}: transcript is empty")
    header, *steps = lines
    if header.get("type") != "header" or header.get("format") != TRANSCRIPT_FORMAT:
        raise ValueError(f"{path.name}: header must name {TRANSCRIPT_FORMAT}")
    for key in ("runtime", "scenario", "source_type", "notes"):
        value = header.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{path.name}: header needs {key}")
    if header["source_type"] not in SOURCE_TYPES:
        raise ValueError(f"{path.name}: unknown source_type")
    for index, step in enumerate(steps, start=2):
        if step.get("type") not in STEP_TYPES:
            raise ValueError(f"{path.name}:{index}: unknown step type")
    return Transcript(path, header, tuple(steps))


class Bindings:
    """Values captured from ``$name`` placeholders."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def match(self, expected: Any, actual: Any, where: str) -> None:
        if expected == "<any>":
            return
        if isinstance(expected, str) and expected.startswith("$"):
            if expected not in self.values:
                self.values[expected] = actual
            elif self.values[expected] != actual:
                raise ReplayMismatch(
                    f"{where}: {expected} was {self.values[expected]!r}, got {actual!r}"
                )
            return
        if isinstance(expected, dict):
            if not isinstance(actual, dict):
                raise ReplayMismatch(f"{where}: expected an object, got {actual!r}")
            for key, value in expected.items():
                if key not in actual:
                    raise ReplayMismatch(f"{where}: missing key {key!r} in {actual!r}")
                self.match(value, actual[key], f"{where}.{key}")
            return
        if isinstance(expected, list):
            if not isinstance(actual, list) or len(actual) != len(expected):
                raise ReplayMismatch(f"{where}: expected {expected!r}, got {actual!r}")
            for index, (left, right) in enumerate(zip(expected, actual)):
                self.match(left, right, f"{where}[{index}]")
            return
        if expected != actual:
            raise ReplayMismatch(f"{where}: expected {expected!r}, got {actual!r}")

    def substitute(self, value: Any) -> Any:
        if isinstance(value, str) and value.startswith("$"):
            if value not in self.values:
                raise ReplayMismatch(f"{value} is used before it is captured")
            return self.values[value]
        if isinstance(value, dict):
            return {key: self.substitute(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.substitute(item) for item in value]
        return value


class _ReplayRun:
    """Walk one transcript; subclasses own the vendor transport and controls."""

    def __init__(self, transcript: Transcript) -> None:
        self.transcript = transcript
        self.bindings = Bindings()
        self._events: list[tuple[str, str | None]] = []
        self._events_lock = Lock()
        self._checked = 0

    def play(self) -> None:
        try:
            for index, step in enumerate(self.transcript.steps, start=2):
                where = f"{self.transcript.path.name}:{index} {step['type']}"
                kind = step["type"]
                if kind == "inbound":
                    self.send(self.bindings.substitute(step["frame"]))
                elif kind == "outbound":
                    self.bindings.match(step["frame"], self.receive(where), where)
                elif kind == "control":
                    self.settle(where)
                    self.control(step, index, where)
                else:
                    self._check_events(step, where)
            self._check_finished()
        finally:
            self.close()

    def record_event(self, kind: str, error_code: str | None) -> None:
        with self._events_lock:
            self._events.append((kind, error_code))

    def send(self, frame: dict[str, Any]) -> None:
        raise NotImplementedError

    def settle(self, where: str) -> None:
        """Wait until the adapter has handled every inbound frame sent so far."""

    def receive(self, where: str) -> dict[str, Any]:
        raise NotImplementedError

    def pending_outbound(self) -> dict[str, Any] | None:
        raise NotImplementedError

    def control(self, step: dict[str, Any], index: int, where: str) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def _check_events(self, step: dict[str, Any], where: str) -> None:
        expected = list(step["kinds"])
        target = self._checked + len(expected)
        deadline = time.monotonic() + STEP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            with self._events_lock:
                if len(self._events) >= target:
                    break
            time.sleep(0.01)
        with self._events_lock:
            window = self._events[self._checked:target]
        kinds = [kind for kind, _ in window]
        if kinds != expected:
            raise ReplayMismatch(f"{where}: expected events {expected}, got {kinds}")
        code = step.get("error_code")
        if code is not None and window[-1][1] != code:
            raise ReplayMismatch(f"{where}: expected error_code {code!r}, got {window[-1][1]!r}")
        self._checked = target

    def _check_finished(self) -> None:
        extra = self.pending_outbound()
        if extra is not None:
            raise ReplayMismatch(
                f"{self.transcript.path.name}: unexpected outbound frame {extra!r}"
            )
        with self._events_lock:
            unchecked = [kind for kind, _ in self._events[self._checked:]]
        if unchecked:
            raise ReplayMismatch(
                f"{self.transcript.path.name}: unchecked events {unchecked}"
            )


class _FakeVendor:
    """Stands in for VendorProcess; the transcript feeds and reads its lines."""

    def __init__(self) -> None:
        self.batches: Queue[list[dict[str, Any]] | None] = Queue()
        self.written: Queue[dict[str, Any]] = Queue()
        self._closed = False

    def start(self) -> None:
        return None

    def alive(self) -> bool:
        return not self._closed

    def write_line(self, value: dict[str, Any]) -> None:
        if self._closed:
            raise VendorClosed()
        self.written.put(json.loads(json.dumps(value)))

    def read_batches(self):
        while True:
            try:
                batch = self.batches.get(timeout=0.5)
            except Empty:
                yield []
                continue
            if batch is None:
                return
            yield batch

    def wait(self) -> int:
        return 0

    def terminate(self, grace: float = 2.0) -> None:
        if not self._closed:
            self._closed = True
            self.batches.put(None)


class _DriverReplay(_ReplayRun):
    """A protocol v2 driver connected to an in-process Bridge core."""

    def __init__(self, transcript: Transcript, driver_factory) -> None:
        super().__init__(transcript)
        self.key = SessionKey(transcript.header["runtime"], "replay", "demo")
        self.vendor = _FakeVendor()
        self._directory = tempfile.TemporaryDirectory()
        self.driver = driver_factory(Path(self._directory.name), self.vendor)
        self.core = Core("replay")
        self._lock = Lock()
        self._results: dict[str, dict[str, Any]] = {}
        self._results_ready = Condition(self._lock)
        self._effects: list[dict[str, Any]] = []
        self._seq = 0
        now = _now()
        with self._lock:
            for connection in ("host", "replay"):
                self.core.connect(connection)
            self._apply(self.core.receive("host", {"type": "hello", "protocol_version": 2, "role": "host", "client_id": "host"}, now))
            self._apply(
                self.core.receive(
                    "host",
                    {
                        "type": "session_open",
                        "session": self.key.to_dict(),
                        "runtime": self.driver.runtime.to_dict(),
                        "capabilities": self.driver.capabilities.to_dict(),
                    },
                    now,
                )
            )
            self._apply(self.core.receive("replay", {"type": "hello", "protocol_version": 2, "role": "client", "client_id": "replay", "tier": "operator"}, now))
            self._apply(self.core.receive("replay", {"type": "subscribe", "stream": "interaction"}, now))
        self.driver.start(self._fact)

    def _fact(self, kind: FactKind, meta: dict | None = None, content: dict | None = None) -> None:
        with self._lock:
            self._seq += 1
            frame = {"type": "fact", "seq": self._seq, "kind": kind.value, "meta": meta or {}, "content": content or {}}
            self._apply(self.core.receive("host", frame, _now()))
        # Effects come only from commands, and control() performs them. A
        # driver emits facts holding its own lock, so performing an effect
        # here could deadlock on that lock.

    def _apply(self, outputs) -> None:
        for output in outputs:
            if not isinstance(output, Send):
                raise ReplayMismatch(f"the core closed {output.connection}: {output.code}")
            frame = output.frame
            if output.connection == "host" and frame["type"] == "effect":
                self._effects.append(frame)
            elif output.connection == "replay" and frame["type"] == "interaction":
                event = frame["event"]
                self.record_event(event["kind"], event["payload"].get("error_code"))
            elif output.connection == "replay" and frame["type"] == "command_result":
                self._results[frame["command_id"]] = frame
                self._results_ready.notify_all()

    def _drain(self) -> None:
        while True:
            with self._lock:
                if not self._effects:
                    return
                effect = self._effects.pop(0)
            outcome = self.driver.perform(EffectKind(effect["kind"]), effect["payload"])
            with self._lock:
                result = {"type": "effect_result", "effect_id": effect["effect_id"], "outcome": outcome.value}
                self._apply(self.core.receive("host", result, _now()))

    def send(self, frame: dict[str, Any]) -> None:
        self.vendor.batches.put([frame])

    def receive(self, where: str) -> dict[str, Any]:
        try:
            return self.vendor.written.get(timeout=STEP_TIMEOUT_SECONDS)
        except Empty as error:
            raise ReplayMismatch(f"{where}: the driver sent nothing") from error

    def pending_outbound(self) -> dict[str, Any] | None:
        try:
            return self.vendor.written.get_nowait()
        except Empty:
            return None

    def control(self, step: dict[str, Any], index: int, where: str) -> None:
        kind = step["kind"]
        payload: dict[str, Any] = {}
        if kind == "submit_prompt":
            payload = {"text": step["text"]}
        elif kind in {"approve", "reject"}:
            with self._lock:
                approvals = self.core.session(self.key)["approvals"]
            if len(approvals) != 1:
                raise ReplayMismatch(f"{where}: expected one pending approval, found {len(approvals)}")
            payload = {"approval_id": approvals[0]["approval_id"]}
        elif kind == "answer":
            with self._lock:
                questions = [ticket for ticket in self.core.session(self.key)["approvals"] if ticket["kind"] == "input"]
            if len(questions) != 1:
                raise ReplayMismatch(f"{where}: expected one open question, found {len(questions)}")
            payload = {"approval_id": questions[0]["approval_id"], "answers": step["answers"]}
        command_id = f"replay-{index}"
        deadline = time.monotonic() + STEP_TIMEOUT_SECONDS
        while True:
            now = _now()
            frame = {
                "type": "command",
                "command": {
                    "command_id": command_id,
                    "kind": kind,
                    **self.key.to_dict(),
                    "issued_by": "replay",
                    "issued_at": now,
                    "expires_at": now + 60_000,
                    "idempotency_key": f"{command_id}-{now}",
                    "payload": payload,
                },
            }
            with self._lock:
                self._results.pop(command_id, None)
                self._apply(self.core.receive("replay", frame, now))
            self._drain()
            with self._lock:
                self._results_ready.wait_for(lambda: command_id in self._results, STEP_TIMEOUT_SECONDS)
                code = self._results.get(command_id, {}).get("code")
            # A driver that is not ready yet answers undelivered; that result
            # is not retained, so the command may be sent again.
            if code != "undelivered" or time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        if code != "dispatched":
            raise ReplayMismatch(f"{where}: control answered {code}")

    def close(self) -> None:
        self.vendor.terminate()
        self.driver.wait()
        self._directory.cleanup()


class _QueueTransport:
    """An app-server transport whose frames come from the transcript.

    The adapter reads and handles frames on one thread, so its next
    receive_message call proves every frame it took before is handled.
    """

    def __init__(self) -> None:
        self.incoming: Queue[dict[str, Any]] = Queue()
        self.outgoing: Queue[dict[str, Any]] = Queue()
        self._handled = Condition()
        self._returned = 0
        self._handled_count = 0

    def connect(self) -> None:
        return None

    def send_message(self, message: dict[str, Any]) -> None:
        self.outgoing.put(message)

    def receive_message(self, timeout_seconds: float) -> dict[str, Any] | None:
        with self._handled:
            self._handled_count = self._returned
            self._handled.notify_all()
        try:
            message = self.incoming.get(timeout=timeout_seconds)
        except Empty:
            return None
        with self._handled:
            self._returned += 1
        return message

    def wait_until_handled(self, count: int, timeout: float) -> bool:
        with self._handled:
            return self._handled.wait_for(
                lambda: self._handled_count >= count, timeout
            )

    def close(self) -> None:
        return None


class _CodexDriverReplay(_DriverReplay):
    """The v2 Codex driver around its app-server adapter, fed by the transcript."""

    def __init__(self, transcript: Transcript) -> None:
        self._transport = _QueueTransport()
        self._sent = 0
        super().__init__(transcript, self._codex_driver)

    def _codex_driver(self, cwd: Path, vendor: _FakeVendor):
        from deskhelm_codex_adapter.driver import CodexSession

        # A long discovery interval keeps the outbound order deterministic.
        return CodexSession(
            self.key,
            socket_path=Path("/tmp/deskhelm-codex-replay.sock"),
            runtime_version="replay",
            refresh_catalog=False,
            transport_factory=lambda: self._transport,
            request_timeout_seconds=STEP_TIMEOUT_SECONDS,
            discovery_interval_seconds=3600,
            reconnect_initial_seconds=0.01,
            reconnect_max_seconds=0.02,
        )

    def send(self, frame: dict[str, Any]) -> None:
        self._sent += 1
        self._transport.incoming.put(frame)

    def settle(self, where: str) -> None:
        if not self._transport.wait_until_handled(self._sent, STEP_TIMEOUT_SECONDS):
            raise ReplayMismatch(f"{where}: the adapter did not handle earlier frames")

    def receive(self, where: str) -> dict[str, Any]:
        try:
            return self._transport.outgoing.get(timeout=STEP_TIMEOUT_SECONDS)
        except Empty as error:
            raise ReplayMismatch(f"{where}: the adapter sent nothing") from error

    def pending_outbound(self) -> dict[str, Any] | None:
        try:
            return self._transport.outgoing.get_nowait()
        except Empty:
            return None

    def close(self) -> None:
        self.driver.close()
        self._directory.cleanup()


def _claude_driver(cwd: Path, vendor: _FakeVendor):
    from deskhelm_claude_adapter.driver import ClaudeSession

    return ClaudeSession(cwd, runtime_version="replay", vendor=vendor)


def _grok_driver(cwd: Path, vendor: _FakeVendor):
    from deskhelm_acp_adapter import AcpSession, GrokFlavor

    return AcpSession(GrokFlavor(cwd / "leader.sock"), cwd, runtime_version="replay", vendor=vendor)


def _now() -> int:
    return int(time.time() * 1000)


def replay_transcript(transcript: Transcript) -> None:
    runtime = transcript.header["runtime"]
    if runtime == "codex":
        run: _ReplayRun = _CodexDriverReplay(transcript)
    elif runtime == "claude":
        run = _DriverReplay(transcript, _claude_driver)
    elif runtime == "grok":
        run = _DriverReplay(transcript, _grok_driver)
    else:
        raise ValueError(f"no replay runner for runtime {runtime!r}")
    run.play()

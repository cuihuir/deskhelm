"""The host's connection to the Bridge; the connection is the session lease."""

from __future__ import annotations

from pathlib import Path
import random
import select
import socket
import sys
from threading import Event, Lock, Thread
from typing import Any, Callable

from deskhelm_protocol import (
    PROTOCOL_VERSION,
    EffectKind,
    EffectOutcome,
    FactKind,
    FrameDecoder,
    MessageType,
    SessionKey,
    WireError,
    encode_frame,
)

from .driver import DriverSession

_POLL_SECONDS = 0.5


class LinkRefused(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(f"the Bridge refused this host ({code})")
        self.code = code


class BridgeLink:
    """Open one session on the Bridge and keep it open across Bridge restarts."""

    def __init__(
        self,
        socket_path: Path,
        session: SessionKey,
        driver: DriverSession,
        *,
        client_id: str,
        ensure_bridge: Callable[[Path], object] | None = None,
        reconnect_initial: float = 0.2,
        reconnect_max: float = 5.0,
        io_timeout: float = 5.0,
    ) -> None:
        self.socket_path = socket_path
        self.session = session
        self.driver = driver
        self.client_id = client_id
        self.ensure_bridge = ensure_bridge
        self.reconnect_initial = reconnect_initial
        self.reconnect_max = reconnect_max
        self.io_timeout = io_timeout
        self.opened = Event()
        self._stop = Event()
        self._lock = Lock()
        self._sock: socket.socket | None = None
        self._seq = 0
        self._thread: Thread | None = None

    def start(self) -> None:
        self._thread = Thread(target=self._run, name="deskhelm-host-link", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            sock = self._sock
            if sock is not None:
                try:
                    sock.sendall(encode_frame({"type": MessageType.SESSION_CLOSE.value}))
                except OSError:
                    pass
        self._drop()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def emit(
        self,
        kind: FactKind,
        meta: dict[str, Any] | None = None,
        content: dict[str, Any] | None = None,
    ) -> None:
        """Send a fact. While the Bridge is unreachable facts are dropped; the
        session state is rebuilt from the driver when the session reopens."""
        with self._lock:
            if self._sock is None or not self.opened.is_set():
                return
            self._send_fact_locked(kind, meta or {}, content or {})

    # Connection loop ---------------------------------------------------

    def _run(self) -> None:
        delay = self.reconnect_initial
        opened_once = False
        while not self._stop.is_set():
            sock: socket.socket | None = None
            try:
                # Only the first open may start a Bridge. A Bridge that later
                # disappears was stopped on purpose or by a crash; restarting
                # it is not a reconnecting host's decision.
                if self.ensure_bridge is not None and not opened_once:
                    self.ensure_bridge(self.socket_path)
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(self.io_timeout)
                sock.connect(str(self.socket_path))
                decoder = FrameDecoder()
                self._open(sock, decoder)
                opened_once = True
                delay = self.reconnect_initial
                self._serve(sock, decoder)
            except LinkRefused as error:
                print(f"deskhelm host: {error}", file=sys.stderr)
            except (OSError, WireError, ValueError, RuntimeError):
                pass
            finally:
                self._drop()
                # A connect or handshake that failed never stored the socket.
                if sock is not None:
                    sock.close()
            # Jitter keeps many hosts from reconnecting in the same instant.
            if self._stop.wait(delay * random.uniform(0.5, 1.0)):
                return
            delay = min(self.reconnect_max, delay * 2)

    def _open(self, sock: socket.socket, decoder: FrameDecoder) -> None:
        sock.sendall(
            encode_frame(
                {
                    "type": MessageType.HELLO.value,
                    "protocol_version": PROTOCOL_VERSION,
                    "role": "host",
                    "client_id": self.client_id,
                }
            )
        )
        _expect(sock, decoder, MessageType.HELLO_OK)
        state = self.driver.state()
        sock.sendall(
            encode_frame(
                {
                    "type": MessageType.SESSION_OPEN.value,
                    "session": self.session.to_dict(),
                    "runtime": self.driver.runtime.to_dict(),
                    "capabilities": self.driver.capabilities.to_dict(),
                    "open_requests": [
                        {
                            "provider_ref": request.provider_ref,
                            "kind": request.kind.value,
                            "summary": request.summary,
                            "expires_at": request.expires_at,
                            "answerable": request.answerable,
                            **({"detail": request.detail} if request.detail else {}),
                        }
                        for request in state.open_requests
                    ],
                }
            )
        )
        result = _expect(sock, decoder, MessageType.SESSION_OPEN_RESULT)
        if result.get("status") != "opened":
            raise LinkRefused(str(result.get("code") or "rejected"))
        with self._lock:
            self._sock, self._seq = sock, 0
            self.opened.set()
            if state.catalog is not None:
                self._send_fact_locked(FactKind.CATALOG, {}, {"catalog": state.catalog})
            if state.ready:
                self._send_fact_locked(FactKind.READY, {}, {})
            if state.turn_ref:
                self._send_fact_locked(FactKind.TURN_STARTED, {"turn_ref": state.turn_ref}, {})
            if state.attention:
                self._send_fact_locked(FactKind.STATUS, {"attention": True}, {})

    def _serve(self, sock: socket.socket, decoder: FrameDecoder) -> None:
        while not self._stop.is_set():
            readable, _, _ = select.select([sock], [], [], _POLL_SECONDS)
            if not readable:
                continue
            data = sock.recv(65536)
            if not data:
                return
            for frame in decoder.feed(data):
                kind = frame.get("type")
                if kind == MessageType.EFFECT:
                    self._perform(frame)
                elif kind == MessageType.ERROR:
                    raise LinkRefused(str(frame.get("code") or "error"))

    def _perform(self, frame: dict[str, Any]) -> None:
        effect_id = frame.get("effect_id")
        try:
            kind = EffectKind(frame.get("kind"))
            payload = frame.get("payload")
            if not isinstance(payload, dict):
                raise ValueError("effect payload must be an object")
            outcome = self.driver.perform(kind, payload)
        except ValueError:
            outcome = EffectOutcome.UNSUPPORTED
        except Exception:
            # The outcome is unknown; the Bridge reports dispatch_failed at
            # its deadline instead of guessing.
            return
        with self._lock:
            if self._sock is None:
                return
            self._sock.sendall(
                encode_frame(
                    {
                        "type": MessageType.EFFECT_RESULT.value,
                        "effect_id": effect_id,
                        "outcome": outcome.value,
                    }
                )
            )

    def _send_fact_locked(
        self, kind: FactKind, meta: dict[str, Any], content: dict[str, Any]
    ) -> None:
        sock = self._sock
        if sock is None:
            return
        self._seq += 1
        frame = {
            "type": MessageType.FACT.value,
            "seq": self._seq,
            "kind": kind.value,
            "meta": meta,
            "content": content,
        }
        try:
            sock.sendall(encode_frame(frame))
        except (OSError, WireError):
            _shutdown(sock)

    def _drop(self) -> None:
        with self._lock:
            sock, self._sock = self._sock, None
            self.opened.clear()
        if sock is not None:
            _shutdown(sock)
            sock.close()


def _expect(sock: socket.socket, decoder: FrameDecoder, kind: MessageType) -> dict[str, Any]:
    while True:
        data = sock.recv(65536)
        if not data:
            raise OSError("the Bridge closed the connection")
        for frame in decoder.feed(data):
            if frame.get("type") == MessageType.ERROR:
                raise LinkRefused(str(frame.get("code") or "error"))
            if frame.get("type") == kind:
                return frame


def _shutdown(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass

"""Unix socket server for the protocol v2 Bridge.

I/O threads only move bytes. Every decision happens on one core thread that
feeds the Core one input at a time, so a slow peer never holds a lock that
another session needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import fcntl
import os
from pathlib import Path
from queue import Empty, Full, Queue
import select
import socket
from threading import Event, Lock, Thread
import time
from typing import Any
import uuid

from deskhelm_protocol import FrameDecoder, WireError, encode_frame
from deskhelm_protocol.model import MessageType

from .core import Close, Core, CoreLimits, Send

DEFAULT_MAX_CONNECTIONS = 64
DEFAULT_HANDSHAKE_SECONDS = 5.0
DEFAULT_WRITER_QUEUE_FRAMES = 512
DEFAULT_INBOX_ITEMS = 4096
DEFAULT_TICK_SECONDS = 0.2
_POLL_SECONDS = 0.5
_CLOSE = object()


@dataclass(slots=True)
class _Peer:
    connection_id: str
    sock: socket.socket
    outbox: Queue[Any]
    closing: Event = field(default_factory=Event)


class BridgeServer:
    """Serve one Core on a Unix socket."""

    def __init__(
        self,
        socket_path: Path,
        *,
        instance_id: str | None = None,
        limits: CoreLimits | None = None,
        max_connections: int = DEFAULT_MAX_CONNECTIONS,
        handshake_seconds: float = DEFAULT_HANDSHAKE_SECONDS,
        writer_queue_frames: int = DEFAULT_WRITER_QUEUE_FRAMES,
        tick_seconds: float = DEFAULT_TICK_SECONDS,
    ) -> None:
        if max_connections < 1 or writer_queue_frames < 1:
            raise ValueError("server limits must be at least 1")
        if handshake_seconds <= 0 or tick_seconds <= 0:
            raise ValueError("server intervals must be positive")
        self.socket_path = socket_path
        self.core = Core(instance_id or uuid.uuid4().hex[:12], limits)
        self.max_connections = max_connections
        self.handshake_seconds = handshake_seconds
        self.writer_queue_frames = writer_queue_frames
        self.tick_seconds = tick_seconds
        self._inbox: Queue[tuple[Any, ...]] = Queue(maxsize=DEFAULT_INBOX_ITEMS)
        self._peers: dict[str, _Peer] = {}
        self._peers_lock = Lock()
        self._stop = Event()
        self._listener: socket.socket | None = None
        self._lock_fd: int | None = None
        self._threads: list[Thread] = []

    @property
    def instance_id(self) -> str:
        return self.core.instance_id

    def start(self) -> None:
        self.socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._lock_fd = _acquire_lock(self.socket_path.with_name(self.socket_path.name + ".lock"))
        # Holding the lock proves any existing socket file is stale.
        self.socket_path.unlink(missing_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        previous = os.umask(0o077)
        try:
            listener.bind(str(self.socket_path))
        finally:
            os.umask(previous)
        listener.listen(self.max_connections)
        listener.settimeout(_POLL_SECONDS)
        self._listener = listener
        for target, name in ((self._accept_loop, "accept"), (self._core_loop, "core")):
            thread = Thread(target=target, name=f"deskhelm-v2-{name}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def close(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        if self._listener is not None:
            self._listener.close()
        with self._peers_lock:
            peers = list(self._peers.values())
        for peer in peers:
            _shutdown(peer.sock)
        for thread in self._threads:
            thread.join(timeout=2)
        self.socket_path.unlink(missing_ok=True)
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    def wait(self) -> None:
        while not self._stop.wait(_POLL_SECONDS):
            pass

    # Threads -----------------------------------------------------------

    def _accept_loop(self) -> None:
        listener = self._listener
        assert listener is not None
        while not self._stop.is_set():
            try:
                sock, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with self._peers_lock:
                full = len(self._peers) >= self.max_connections
                if not full:
                    peer = _Peer(uuid.uuid4().hex[:12], sock, Queue(maxsize=self.writer_queue_frames))
                    self._peers[peer.connection_id] = peer
            if full:
                sock.close()
                continue
            self._inbox.put(("connect", peer.connection_id))
            for target, name in ((self._read_loop, "read"), (self._write_loop, "write")):
                Thread(target=target, args=(peer,), name=f"deskhelm-v2-{name}", daemon=True).start()

    def _read_loop(self, peer: _Peer) -> None:
        decoder = FrameDecoder()
        deadline = time.monotonic() + self.handshake_seconds
        greeted = False
        try:
            while not self._stop.is_set() and not peer.closing.is_set():
                timeout = _POLL_SECONDS if greeted else max(0.0, deadline - time.monotonic())
                readable, _, _ = select.select([peer.sock], [], [], min(timeout, _POLL_SECONDS))
                if not readable:
                    if not greeted and time.monotonic() >= deadline:
                        self._fail(peer, "handshake_timeout", "no hello arrived in time")
                        return
                    continue
                data = peer.sock.recv(65536)
                if not data:
                    return
                for frame in decoder.feed(data):
                    greeted = True
                    self._inbox.put(("frame", peer.connection_id, frame))
        except WireError as error:
            self._fail(peer, "invalid_frame", str(error))
        except OSError:
            return
        finally:
            peer.closing.set()
            self._enqueue(peer, _CLOSE)
            self._inbox.put(("disconnect", peer.connection_id))

    def _write_loop(self, peer: _Peer) -> None:
        try:
            while not self._stop.is_set():
                try:
                    item = peer.outbox.get(timeout=_POLL_SECONDS)
                except Empty:
                    if peer.closing.is_set() and peer.outbox.empty():
                        return
                    continue
                if item is _CLOSE:
                    return
                peer.sock.sendall(item)
        except OSError:
            return
        finally:
            peer.closing.set()
            _shutdown(peer.sock)
            peer.sock.close()
            with self._peers_lock:
                self._peers.pop(peer.connection_id, None)
            self._inbox.put(("disconnect", peer.connection_id))

    def _core_loop(self) -> None:
        next_tick = time.monotonic() + self.tick_seconds
        while not self._stop.is_set():
            try:
                item = self._inbox.get(timeout=max(0.0, next_tick - time.monotonic()))
            except Empty:
                item = None
            now = int(time.time() * 1000)
            if item is not None:
                kind, connection = item[0], item[1]
                if kind == "connect":
                    outputs = self.core.connect(connection)
                elif kind == "frame":
                    outputs = self.core.receive(connection, item[2], now)
                else:
                    outputs = self.core.disconnect(connection, now)
                self._dispatch(outputs)
            if time.monotonic() >= next_tick:
                self._dispatch(self.core.tick(now))
                next_tick = time.monotonic() + self.tick_seconds

    # Delivery ----------------------------------------------------------

    def _dispatch(self, outputs) -> None:
        for output in outputs:
            with self._peers_lock:
                peer = self._peers.get(output.connection)
            if peer is None:
                continue
            if isinstance(output, Close):
                self._enqueue(peer, _CLOSE)
                peer.closing.set()
                continue
            assert isinstance(output, Send)
            try:
                data = encode_frame(output.frame)
            except WireError:
                self._fail(peer, "frame_too_large", "a frame exceeded the size limit")
                continue
            self._enqueue(peer, data)

    def _enqueue(self, peer: _Peer, item: Any) -> None:
        try:
            peer.outbox.put_nowait(item)
        except Full:
            # A full queue is terminal; the peer must reconnect and resubscribe.
            peer.closing.set()
            _shutdown(peer.sock)

    def _fail(self, peer: _Peer, code: str, message: str) -> None:
        frame = {"type": MessageType.ERROR.value, "code": code, "message": message}
        self._enqueue(peer, encode_frame(frame))
        self._enqueue(peer, _CLOSE)
        peer.closing.set()


def _acquire_lock(path: Path) -> int:
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        os.close(descriptor)
        raise RuntimeError("another Bridge already serves this socket") from error
    return descriptor


def _shutdown(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass

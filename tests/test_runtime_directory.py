from io import StringIO
from pathlib import Path
import os
import socket
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest import mock

from deskhelm_bridge.client import (
    attach_agent_runtime,
    detach_agent_runtime,
    query_agent_runtime_directory,
    send_event,
)
from deskhelm_bridge.control import ControlCommand, ControlKind, InterruptPayload
from deskhelm_bridge.control_result import ControlResultCode, ControlResultStatus
from deskhelm_bridge.control_router import ControlRouter
from deskhelm_bridge.event import AgentEvent, AgentState, ProtocolError
from deskhelm_bridge.interaction import (
    ApprovalRequestPayload,
    InteractionEvent,
    InteractionKind,
)
from deskhelm_bridge.runtime_directory import AgentRuntimeDirectory
from deskhelm_bridge.server import run_bridge
from deskhelm_bridge.session_registry import SessionKey, SessionRegistry


class _RecordingRuntime:
    def __init__(self) -> None:
        self.commands: list[ControlCommand] = []
        self.facade = None
        self.closed = False

    def start(self, publish, router) -> None:
        del publish
        self.facade = router
        router.register_handler(ControlKind.INTERRUPT, self.commands.append)
        router.register_catalog_provider(
            lambda agent_id, session_id, project_id: {"notice": "catalog_unavailable"}
        )

    def close(self) -> None:
        self.closed = True


class RuntimeDirectoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._bridge_threads: dict[str, Thread] = {}

    def test_two_sessions_keep_separate_handlers_and_catalogs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            directory_runtime, router, registry, runtimes = self._directory(root)
            first = self._attach(directory_runtime, root, "one")
            second = self._attach(directory_runtime, root, "two")
            self.assertEqual(first, "attached")
            self.assertEqual(second, "attached")
            self.assertEqual(self._attach(directory_runtime, root, "one"), "already_attached")

            registry.register(SessionKey("codex", "one", "demo"))
            registry.register(SessionKey("codex", "two", "demo"))
            first_result = router.route(self._command("one", "command-1"))
            second_result = router.route(self._command("two", "command-2"))

            self.assertEqual(first_result.code, ControlResultCode.DISPATCHED)
            self.assertEqual(second_result.code, ControlResultCode.DISPATCHED)
            self.assertEqual(runtimes[0].commands[0].session_id, "one")
            self.assertEqual(runtimes[1].commands[0].session_id, "two")
            provider = router.catalog_provider()
            self.assertIsNotNone(provider)
            self.assertEqual(
                provider("codex", "one", "demo")["notice"], "catalog_unavailable"
            )
            self.assertIsNone(provider("codex", "missing", "demo"))
            registry.register(SessionKey("codex", "missing", "demo"))
            missing = router.route(self._command("missing", "command-3"))
            self.assertEqual(missing.status, ControlResultStatus.REJECTED)
            self.assertEqual(missing.code, ControlResultCode.HANDLER_UNAVAILABLE)

            self.assertTrue(runtimes[0].facade.resolve_approval("missing-approval") is False)
            router.observe_interaction(
                InteractionEvent(
                    kind=InteractionKind.APPROVAL_REQUEST,
                    agent_id="codex",
                    session_id="one",
                    project_id="demo",
                    source="test",
                    source_version="1",
                    sequence=1,
                    correlation_id="request-1",
                    payload=ApprovalRequestPayload(
                        "request-1", "summary", 9_000_000_000_000
                    ),
                )
            )
            self.assertTrue(runtimes[0].facade.resolve_approval("request-1"))
            self.assertFalse(runtimes[0].facade.resolve_approval("request-1"))

            self.assertEqual(directory_runtime.detach_frame(self._identity("one")), "detached")
            self.assertTrue(runtimes[0].closed)
            self.assertEqual(
                directory_runtime.detach_frame(self._identity("one")), "not_attached"
            )
            directory_runtime.close()
            self.assertTrue(runtimes[1].closed)
            directory_runtime.close()

    def test_pending_dispatch_fails_and_detach_closes_after_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entered = Event()
            release = Event()

            class _BlockingRuntime:
                def __init__(self) -> None:
                    self.closed = False

                def start(self, publish, router) -> None:
                    del publish
                    router.register_handler(ControlKind.INTERRUPT, lambda command: None)
                    entered.set()
                    if not release.wait(2):
                        raise TimeoutError("runtime start was not released")

                def close(self) -> None:
                    self.closed = True

            runtime_holder: list[_BlockingRuntime] = []

            def factory(spec) -> _BlockingRuntime:
                del spec
                runtime = _BlockingRuntime()
                runtime_holder.append(runtime)
                return runtime

            directory_runtime, router, registry, _runtimes = self._directory(
                root, factory=factory
            )
            registry.register(SessionKey("codex", "one", "demo"))
            status: list[str] = []

            def attach() -> None:
                status.append(
                    directory_runtime.attach_frame(
                        self._frame(root / "deskhelm", "one"), lambda event: None
                    )
                )

            worker = Thread(target=attach)
            worker.start()
            self.assertTrue(entered.wait(2))
            pending = router.route(self._command("one", "while-attaching"))
            self.assertEqual(pending.code, ControlResultCode.HANDLER_UNAVAILABLE)
            self.assertEqual(directory_runtime.detach_frame(self._identity("one")), "detached")
            release.set()
            worker.join(timeout=2)
            self.assertEqual(status, ["detached"])
            self.assertTrue(runtime_holder[0].closed)

    def test_app_server_symlink_to_an_outside_socket_can_attach(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            runtime_root = root / "deskhelm"
            directory_runtime, _router, _registry, runtimes = self._directory(root)
            target = outside / "daemon.sock"
            self._listen(target)
            link = runtime_root / "app.sock"
            link.symlink_to(target)
            frame = self._frame(runtime_root, "linked")
            frame["app_server_socket"] = str(link)
            self.assertEqual(
                directory_runtime.attach_frame(frame, lambda event: None),
                "attached",
            )
            self.assertEqual(len(runtimes), 1)
            proxy_link = runtime_root / "proxy-link.sock"
            proxy_link.symlink_to(target)
            rejected = self._frame(runtime_root, "rejected")
            rejected["tui_proxy_socket"] = str(proxy_link)
            self.assertEqual(
                directory_runtime.attach_frame(rejected, lambda event: None),
                "invalid",
            )

    def test_invalid_sockets_and_capacity_do_not_start_a_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            directory_runtime, _router, _registry, runtimes = self._directory(
                root, capacity=1
            )
            runtime_root = root / "deskhelm"
            self.assertEqual(
                directory_runtime.attach_frame(
                    {"protocol_version": 1, "cwd": "/tmp"}, lambda event: None
                ),
                "invalid",
            )
            relative = self._frame(runtime_root, "relative")
            relative["app_server_socket"] = "relative.sock"
            self.assertEqual(
                directory_runtime.attach_frame(relative, lambda event: None), "invalid"
            )
            escaped = self._frame(runtime_root, "escaped")
            escaped["app_server_socket"] = str(outside / "app.sock")
            self.assertEqual(
                directory_runtime.attach_frame(escaped, lambda event: None), "invalid"
            )
            self.assertEqual(runtimes, [])
            self.assertEqual(
                directory_runtime.attach_frame(self._frame(runtime_root, "one"), lambda event: None),
                "attached",
            )
            self.assertEqual(
                directory_runtime.attach_frame(self._frame(runtime_root, "two"), lambda event: None),
                "capacity",
            )
            self.assertEqual(len(runtimes), 1)
            with self.assertRaises(ProtocolError):
                directory_runtime.attach_frame(
                    {"protocol_version": 2}, lambda event: None
                )

    def test_bridge_accepts_one_attach_and_a_plain_bridge_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_root = root / "deskhelm"
            runtime_root.mkdir()
            bridge_socket = runtime_root / "bridge.sock"
            app_socket = runtime_root / "app.sock"
            proxy_socket = runtime_root / "proxy.sock"
            self._listen(app_socket)
            created: list[_RecordingRuntime] = []

            def factory(spec) -> _RecordingRuntime:
                self.assertEqual(spec.app_server_socket, app_socket.resolve())
                self.assertEqual(spec.tui_proxy_socket, proxy_socket.resolve())
                runtime = _RecordingRuntime()
                created.append(runtime)
                return runtime

            started: list[Path] = []
            try:
                with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": directory}):
                    self._serve(bridge_socket, runtime_directory_factory=factory)
                    started.append(bridge_socket)
                    status = query_agent_runtime_directory(
                        bridge_socket, client_id="directory-test"
                    )
                    self.assertTrue(status.enabled)
                    self.assertFalse(status.voice_enabled)
                    self.assertEqual(status.capacity, 32)
                    self.assertEqual(status.attached, 0)
                    attached = attach_agent_runtime(
                        bridge_socket,
                        client_id="directory-test",
                        agent_id="codex",
                        session_id="local-1",
                        project_id="demo",
                        source_version="codex-cli 1",
                        app_server_socket=app_socket,
                        tui_proxy_socket=proxy_socket,
                    )
                    self.assertEqual(attached, "attached")
                    self.assertEqual(
                        query_agent_runtime_directory(
                            bridge_socket, client_id="directory-test"
                        ).attached,
                        1,
                    )
                    self.assertEqual(
                        detach_agent_runtime(
                            bridge_socket,
                            client_id="directory-test",
                            agent_id="codex",
                            session_id="local-1",
                            project_id="demo",
                        ),
                        "detached",
                    )
                    self.assertTrue(created[0].closed)
                    self._stop(bridge_socket)
                    started.remove(bridge_socket)

                plain_socket = root / "plain.sock"
                self._serve(plain_socket)
                started.append(plain_socket)
                with self.assertRaises(ProtocolError):
                    query_agent_runtime_directory(
                        plain_socket, client_id="directory-test"
                    )
                self._stop(plain_socket)
                started.remove(plain_socket)
            finally:
                for path in started:
                    self._stop(path)

    def test_relay_runtime_attaches_and_rejects_a_mixed_socket_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_root = root / "deskhelm"
            seen: list[object] = []

            def factory(spec):
                seen.append(spec)
                return _RecordingRuntime()

            directory_runtime, _router, _registry, _created = self._directory(
                root, factory=factory
            )
            relay = runtime_root / "agent.sock"
            self._listen(relay)
            frame = {
                "protocol_version": 1,
                "message_type": "agent_runtime_attach",
                "agent_id": "claude",
                "session_id": "local-claude",
                "project_id": "demo",
                "source_version": "claude-code 1",
                "runtime_kind": "claude_stream",
                "relay_socket": str(relay),
            }
            self.assertEqual(
                directory_runtime.attach_frame(frame, lambda event: None),
                "attached",
            )
            spec = seen[0]
            self.assertEqual(spec.runtime_kind, "claude_stream")
            self.assertEqual(spec.relay_socket.name, "agent.sock")
            self.assertIsNone(spec.app_server_socket)
            mixed = dict(frame)
            mixed["session_id"] = "local-mixed"
            mixed["app_server_socket"] = str(runtime_root / "app.sock")
            self.assertEqual(
                directory_runtime.attach_frame(mixed, lambda event: None),
                "invalid",
            )
            unknown = dict(frame)
            unknown["session_id"] = "local-unknown"
            unknown["runtime_kind"] = "gemini"
            self.assertEqual(
                directory_runtime.attach_frame(unknown, lambda event: None),
                "invalid",
            )
            missing = dict(frame)
            missing["session_id"] = "local-missing"
            missing["relay_socket"] = str(runtime_root / "missing.sock")
            self.assertEqual(
                directory_runtime.attach_frame(missing, lambda event: None),
                "invalid",
            )
            outside = root / "outside.sock"
            self._listen(outside)
            escaped = dict(frame)
            escaped["session_id"] = "local-escaped"
            escaped["runtime_kind"] = "grok_stdio"
            escaped["relay_socket"] = str(outside)
            self.assertEqual(
                directory_runtime.attach_frame(escaped, lambda event: None),
                "invalid",
            )
            codex = self._frame(runtime_root, "codex-with-relay")
            codex["relay_socket"] = str(relay)
            self.assertEqual(
                directory_runtime.attach_frame(codex, lambda event: None),
                "invalid",
            )
            self.assertEqual(len(seen), 1)

    def test_directory_cannot_replace_a_single_runtime(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            run_bridge(
                socket_path=Path("/tmp/deskhelm-runtime-directory-unused.sock"),
                slot_count=1,
                stream=StringIO(),
                agent_runtime=_RecordingRuntime(),
                runtime_directory_factory=lambda spec: _RecordingRuntime(),
            )

    def _directory(
        self,
        root: Path,
        *,
        capacity: int = 4,
        factory=None,
    ):
        runtime_root = root / "deskhelm"
        runtime_root.mkdir(exist_ok=True)
        bridge_socket = runtime_root / "bridge.sock"
        created: list[_RecordingRuntime] = []

        def default_factory(spec) -> _RecordingRuntime:
            del spec
            runtime = _RecordingRuntime()
            created.append(runtime)
            return runtime

        registry = SessionRegistry(slot_count=8)
        router = ControlRouter(session_registry=registry)
        directory = AgentRuntimeDirectory(
            control_router=router,
            factory=default_factory if factory is None else factory,
            runtime_root=runtime_root,
            bridge_socket=bridge_socket,
            capacity=capacity,
        )
        directory.install()
        self.addCleanup(directory.close)
        return directory, router, registry, created

    def _attach(self, directory: AgentRuntimeDirectory, root: Path, session_id: str) -> str:
        return directory.attach_frame(
            self._frame(root / "deskhelm", session_id), lambda event: None
        )

    def _frame(self, runtime_root: Path, session_id: str) -> dict[str, object]:
        app_socket = runtime_root / f"{session_id}-app.sock"
        proxy_socket = runtime_root / f"{session_id}-proxy.sock"
        if not app_socket.exists():
            self._listen(app_socket)
        return {
            "protocol_version": 1,
            "message_type": "agent_runtime_attach",
            "agent_id": "codex",
            "session_id": session_id,
            "project_id": "demo",
            "source_version": "codex-cli 1",
            "app_server_socket": str(app_socket),
            "tui_proxy_socket": str(proxy_socket),
        }

    def _identity(self, session_id: str) -> dict[str, object]:
        return {
            "protocol_version": 1,
            "message_type": "agent_runtime_detach",
            "agent_id": "codex",
            "session_id": session_id,
            "project_id": "demo",
        }

    def _command(self, session_id: str, idempotency_key: str) -> ControlCommand:
        issued_at = int(time.time() * 1000)
        return ControlCommand(
            kind=ControlKind.INTERRUPT,
            agent_id="codex",
            session_id=session_id,
            project_id="demo",
            issued_by="tester",
            issued_at=issued_at,
            expires_at=issued_at + 10_000,
            idempotency_key=idempotency_key,
            payload=InterruptPayload(),
        )

    def _listen(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path))
        server.listen(1)
        self.addCleanup(server.close)

    def _serve(self, socket_path: Path, **kwargs: object) -> None:
        errors: list[BaseException] = []

        def serve() -> None:
            try:
                run_bridge(
                    socket_path=socket_path,
                    slot_count=4,
                    stream=StringIO(),
                    color=False,
                    live=False,
                    max_events=1,
                    **kwargs,
                )
            except BaseException as error:
                errors.append(error)

        thread = Thread(target=serve, daemon=True)
        self._bridge_threads[str(socket_path)] = thread
        thread.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if self._accepts(socket_path):
                self.addCleanup(lambda path=socket_path: self._stop(path))
                return
            if not thread.is_alive():
                break
            time.sleep(0.02)
        self._stop(socket_path)
        detail = repr(errors[0]) if errors else "bridge did not start listening"
        self.fail(detail)

    def _accepts(self, socket_path: Path) -> bool:
        if not socket_path.exists():
            return False
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.settimeout(0.2)
            client.connect(str(socket_path))
            return True
        except OSError:
            return False
        finally:
            client.close()

    def _stop(self, socket_path: Path, thread: Thread | None = None) -> None:
        tracked = self._bridge_threads.pop(str(socket_path), None)
        thread = tracked if thread is None else thread
        try:
            send_event(
                AgentEvent(agent_id="codex", slot=1, state=AgentState.IDLE),
                socket_path,
            )
        except (ConnectionError, OSError):
            pass
        if thread is not None:
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()

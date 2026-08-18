import os
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

from deskhelm_bridge.client import send_event, send_negotiated_event
from deskhelm_bridge.event import AgentEvent, AgentState


ROOT = Path(__file__).resolve().parents[1]
PYTHONPATH = str(ROOT / "bridge")


class BridgeEndToEndTests(unittest.TestCase):
    def test_emit_updates_bridge_slot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "bridge.sock"
            environment = {**os.environ, "PYTHONPATH": PYTHONPATH}
            bridge = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "deskhelm_bridge",
                    "bridge",
                    "--plain",
                    "--max-events",
                    "1",
                    "--socket",
                    str(socket_path),
                ],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 3
                while not socket_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(socket_path.exists(), "bridge socket was not created")

                emitted = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "deskhelm_bridge",
                        "emit",
                        "--socket",
                        str(socket_path),
                        "--agent-id",
                        "demo:backend",
                        "--slot",
                        "1",
                        "--state",
                        "running_tool",
                        "--label",
                        "backend",
                    ],
                    cwd=ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                self.assertEqual(emitted.returncode, 0, emitted.stderr)
                stdout, stderr = bridge.communicate(timeout=3)
            finally:
                if bridge.poll() is None:
                    bridge.terminate()
                    bridge.wait(timeout=3)

            self.assertEqual(bridge.returncode, 0, stderr)
            self.assertIn("slot=1", stdout)
            self.assertIn("state=running_tool", stdout)

    def test_stalled_connection_does_not_block_legacy_publisher(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "bridge.sock"
            bridge, environment = self._start_bridge(socket_path, max_events=1)
            stalled = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                stalled.connect(str(socket_path))
                stalled.sendall(b"{")

                emitted = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "deskhelm_bridge",
                        "emit",
                        "--socket",
                        str(socket_path),
                        "--agent-id",
                        "demo:concurrent",
                        "--slot",
                        "0",
                        "--state",
                        "thinking",
                    ],
                    cwd=ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                self.assertEqual(emitted.returncode, 0, emitted.stderr)
                stdout, stderr = bridge.communicate(timeout=3)
            finally:
                stalled.close()
                self._stop_bridge(bridge)

            self.assertEqual(bridge.returncode, 0, stderr)
            self.assertIn("agent=demo:concurrent", stdout)

    def test_incomplete_first_frame_is_closed_after_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "bridge.sock"
            bridge, _ = self._start_bridge(
                socket_path,
                extra_args=["--max-connections", "2", "--max-subscribers", "0"],
            )
            stalled = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            stalled.settimeout(4)
            try:
                stalled.connect(str(socket_path))
                stalled.sendall(b"{")

                self.assertEqual(stalled.recv(1), b"")
                send_event(
                    AgentEvent(
                        agent_id="demo:after-timeout",
                        slot=0,
                        state=AgentState.IDLE,
                    ),
                    socket_path,
                )
                time.sleep(0.05)
                bridge.terminate()
                stdout, stderr = bridge.communicate(timeout=3)
            finally:
                stalled.close()
                self._stop_bridge(bridge)

            self.assertEqual(bridge.returncode, -15, stderr)
            self.assertIn("agent=demo:after-timeout", stdout)

    def test_negotiated_publisher_receives_hello_and_sends_agent_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "bridge.sock"
            bridge, _ = self._start_bridge(socket_path, max_events=1)
            try:
                hello = send_negotiated_event(
                    AgentEvent(
                        agent_id="demo:negotiated",
                        slot=2,
                        state=AgentState.WAITING_USER,
                    ),
                    socket_path,
                    client_id="test-publisher",
                )
                self.assertEqual(hello.selected_version, 1)
                self.assertEqual(hello.accepted_capabilities, ("agent_event_v1",))
                self.assertEqual(hello.max_frame_bytes, 1024 * 1024)
                stdout, stderr = bridge.communicate(timeout=3)
            finally:
                self._stop_bridge(bridge)

            self.assertEqual(bridge.returncode, 0, stderr)
            self.assertIn("agent=demo:negotiated", stdout)
            self.assertIn("state=waiting_user", stdout)

    def test_controller_receives_correlated_safe_rejections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "bridge.sock"
            bridge, environment = self._start_bridge(socket_path, max_events=1)
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            reader = None
            try:
                client.connect(str(socket_path))
                reader = client.makefile("rb")
                self._send_json(
                    client,
                    {
                        "protocol_version": 1,
                        "message_type": "client_hello",
                        "client_id": "test-controller",
                        "role": "controller",
                        "supported_versions": [1],
                        "capabilities": ["control_command_v1"],
                    },
                )
                hello = json.loads(reader.readline())
                self.assertEqual(
                    hello["accepted_capabilities"], ["control_command_v1"]
                )
                self.assertEqual(
                    hello["limits"]["control_idempotency_entries"], 1024
                )

                now_ms = int(time.time() * 1000)
                command = {
                    "protocol_version": 1,
                    "message_type": "control_command",
                    "command_id": "controller-missing-target",
                    "kind": "focus",
                    "agent_id": "codex",
                    "session_id": "missing-session",
                    "project_id": "deskhelm",
                    "issued_by": "test-controller",
                    "issued_at": now_ms,
                    "expires_at": now_ms + 30_000,
                    "idempotency_key": "missing-target-1",
                    "payload": {},
                }
                self._send_json(client, command)
                missing = json.loads(reader.readline())
                self.assertEqual(missing["message_type"], "control_result")
                self.assertEqual(missing["command_id"], command["command_id"])
                self.assertEqual(missing["code"], "target_not_found")

                spoofed = dict(command)
                spoofed["command_id"] = "controller-spoofed-issuer"
                spoofed["idempotency_key"] = "spoofed-issuer-1"
                spoofed["issued_by"] = "another-controller"
                self._send_json(client, spoofed)
                mismatch = json.loads(reader.readline())
                self.assertEqual(mismatch["code"], "issuer_mismatch")

                emitted = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "deskhelm_bridge",
                        "emit",
                        "--socket",
                        str(socket_path),
                        "--agent-id",
                        "demo:legacy",
                        "--slot",
                        "0",
                        "--state",
                        "idle",
                    ],
                    cwd=ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                self.assertEqual(emitted.returncode, 0, emitted.stderr)
                bridge.communicate(timeout=3)
            finally:
                if reader is not None:
                    reader.close()
                client.close()
                self._stop_bridge(bridge)

            self.assertEqual(bridge.returncode, 0)

    def test_adapter_lifecycle_makes_full_session_control_targetable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "bridge.sock"
            bridge, _ = self._start_bridge(socket_path)
            publisher = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            controller = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            publisher.settimeout(3)
            controller.settimeout(3)
            publisher_reader = None
            controller_reader = None
            try:
                publisher.connect(str(socket_path))
                publisher_reader = publisher.makefile("rb")
                self._send_json(
                    publisher,
                    {
                        "protocol_version": 1,
                        "message_type": "client_hello",
                        "client_id": "codex-adapter",
                        "role": "publisher",
                        "supported_versions": [1],
                        "capabilities": [
                            "adapter_session_v1",
                            "agent_event_v1",
                            "interaction_event_v1",
                        ],
                    },
                )
                publisher_hello = json.loads(publisher_reader.readline())
                self.assertEqual(
                    publisher_hello["accepted_capabilities"],
                    [
                        "adapter_session_v1",
                        "agent_event_v1",
                        "interaction_event_v1",
                    ],
                )

                register = self._adapter_session("register")
                self._send_json(publisher, register)
                registered = json.loads(publisher_reader.readline())
                self.assertEqual(registered["message_type"], "adapter_session_result")
                self.assertEqual(registered["action"], "register")
                self.assertEqual(registered["slot"], 1)

                state_event = AgentEvent(
                    agent_id="codex", slot=1, state=AgentState.THINKING
                ).to_dict()
                state_event["message_type"] = "agent_event"
                self._send_json(publisher, state_event)

                controller.connect(str(socket_path))
                controller_reader = controller.makefile("rb")
                self._send_json(
                    controller,
                    {
                        "protocol_version": 1,
                        "message_type": "client_hello",
                        "client_id": "lifecycle-controller",
                        "role": "controller",
                        "supported_versions": [1],
                        "capabilities": ["control_command_v1"],
                    },
                )
                json.loads(controller_reader.readline())

                self._send_json(
                    controller,
                    self._focus_command("focus-active", "focus-active-1"),
                )
                focused = json.loads(controller_reader.readline())
                self.assertEqual(focused["code"], "focused")

                self._send_json(publisher, self._adapter_session("disconnect"))
                disconnected = json.loads(publisher_reader.readline())
                self.assertEqual(disconnected["action"], "disconnect")
                self.assertEqual(disconnected["slot"], 1)

                self._send_json(
                    controller,
                    self._focus_command("focus-inactive", "focus-inactive-1"),
                )
                inactive = json.loads(controller_reader.readline())
                self.assertEqual(inactive["code"], "target_inactive")

                restored_register = self._adapter_session("register")
                restored_register["occurred_at"] += 10_000
                self._send_json(publisher, restored_register)
                restored = json.loads(publisher_reader.readline())
                self.assertEqual(restored["action"], "register")
                self.assertEqual(restored["slot"], 1)

                self._send_json(
                    controller,
                    self._focus_command("focus-restored", "focus-restored-1"),
                )
                refocused = json.loads(controller_reader.readline())
                self.assertEqual(refocused["code"], "focused")

                self._send_json(publisher, self._adapter_session("release"))
                released = json.loads(publisher_reader.readline())
                self.assertEqual(released["action"], "release")
                self.assertIsNone(released["slot"])

                self._send_json(
                    controller,
                    self._focus_command("focus-released", "focus-released-1"),
                )
                missing = json.loads(controller_reader.readline())
                self.assertEqual(missing["code"], "target_not_found")
            finally:
                if publisher_reader is not None:
                    publisher_reader.close()
                if controller_reader is not None:
                    controller_reader.close()
                publisher.close()
                controller.close()
                self._stop_bridge(bridge)

    def test_lifecycle_publisher_rejects_event_before_registration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "bridge.sock"
            bridge, _ = self._start_bridge(socket_path)
            publisher = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            publisher.settimeout(3)
            reader = None
            try:
                publisher.connect(str(socket_path))
                reader = publisher.makefile("rb")
                self._send_json(
                    publisher,
                    {
                        "protocol_version": 1,
                        "message_type": "client_hello",
                        "client_id": "unregistered-adapter",
                        "role": "publisher",
                        "supported_versions": [1],
                        "capabilities": [
                            "adapter_session_v1",
                            "agent_event_v1",
                        ],
                    },
                )
                json.loads(reader.readline())
                event = AgentEvent(
                    agent_id="codex", slot=1, state=AgentState.THINKING
                ).to_dict()
                event["message_type"] = "agent_event"
                self._send_json(publisher, event)
                error = json.loads(reader.readline())
            finally:
                if reader is not None:
                    reader.close()
                publisher.close()
                self._stop_bridge(bridge)

            self.assertEqual(error["message_type"], "protocol_error")
            self.assertEqual(error["code"], "invalid_frame")

    def test_subscriber_receives_snapshot_then_live_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "bridge.sock"
            bridge, _ = self._start_bridge(socket_path)
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(3)
            reader = None
            try:
                client.connect(str(socket_path))
                reader = client.makefile("rb")
                self._send_subscriber_hello(client, "test-subscriber")

                hello = json.loads(reader.readline())
                snapshot = json.loads(reader.readline())
                self.assertEqual(hello["message_type"], "server_hello")
                self.assertEqual(
                    hello["accepted_capabilities"], ["state_subscription_v1"]
                )
                self.assertEqual(snapshot["message_type"], "state_snapshot")
                self.assertEqual(snapshot["sequence"], 0)
                self.assertEqual(len(snapshot["events"]), 4)

                send_event(
                    AgentEvent(
                        agent_id="demo:subscriber",
                        slot=1,
                        state=AgentState.RUNNING_TOOL,
                    ),
                    socket_path,
                )
                update = json.loads(reader.readline())
            finally:
                if reader is not None:
                    reader.close()
                client.close()
                self._stop_bridge(bridge)

            self.assertEqual(update["message_type"], "state_update")
            self.assertEqual(update["subscription_id"], snapshot["subscription_id"])
            self.assertEqual(update["sequence"], 1)
            self.assertEqual(update["event"]["agent_id"], "demo:subscriber")

    def test_subscriber_limit_reserves_capacity_for_publishers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "bridge.sock"
            bridge, _ = self._start_bridge(
                socket_path,
                extra_args=["--max-connections", "4", "--max-subscribers", "1"],
            )
            first = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            second = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            first.settimeout(3)
            second.settimeout(3)
            first_reader = None
            second_reader = None
            try:
                first.connect(str(socket_path))
                first_reader = first.makefile("rb")
                self._send_subscriber_hello(first, "subscriber-one")
                self.assertEqual(json.loads(first_reader.readline())["message_type"], "server_hello")
                first_reader.readline()

                second.connect(str(socket_path))
                second_reader = second.makefile("rb")
                self._send_subscriber_hello(second, "subscriber-two")
                error = json.loads(second_reader.readline())
                self.assertEqual(error["code"], "subscriber_capacity")

                send_event(
                    AgentEvent(
                        agent_id="demo:reserved-publisher",
                        slot=0,
                        state=AgentState.IDLE,
                    ),
                    socket_path,
                )
                update = json.loads(first_reader.readline())
            finally:
                if first_reader is not None:
                    first_reader.close()
                if second_reader is not None:
                    second_reader.close()
                first.close()
                second.close()
                self._stop_bridge(bridge)

            self.assertEqual(update["event"]["agent_id"], "demo:reserved-publisher")

    def test_interaction_subscriber_receives_only_rich_interaction_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "bridge.sock"
            bridge, _ = self._start_bridge(socket_path)
            subscriber = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            publisher = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            subscriber.settimeout(3)
            publisher.settimeout(3)
            subscriber_reader = None
            publisher_reader = None
            try:
                subscriber.connect(str(socket_path))
                subscriber_reader = subscriber.makefile("rb")
                self._send_subscriber_hello(
                    subscriber,
                    "interaction-subscriber",
                    capability="interaction_subscription_v1",
                )
                subscriber_hello = json.loads(subscriber_reader.readline())
                started = json.loads(subscriber_reader.readline())
                self.assertEqual(
                    subscriber_hello["accepted_capabilities"],
                    ["interaction_subscription_v1"],
                )
                self.assertEqual(
                    started["message_type"], "interaction_subscription_started"
                )
                self.assertEqual(started["sequence"], 0)

                publisher.connect(str(socket_path))
                publisher_reader = publisher.makefile("rb")
                self._send_json(
                    publisher,
                    {
                        "protocol_version": 1,
                        "message_type": "client_hello",
                        "client_id": "combined-publisher",
                        "role": "publisher",
                        "supported_versions": [1],
                        "capabilities": [
                            "agent_event_v1",
                            "interaction_event_v1",
                        ],
                    },
                )
                publisher_hello = json.loads(publisher_reader.readline())
                self.assertEqual(
                    publisher_hello["accepted_capabilities"],
                    ["agent_event_v1", "interaction_event_v1"],
                )

                state_event = AgentEvent(
                    agent_id="demo:combined",
                    slot=2,
                    state=AgentState.RUNNING_TOOL,
                ).to_dict()
                state_event["message_type"] = "agent_event"
                self._send_json(publisher, state_event)

                interaction_event = json.loads(
                    (
                        ROOT
                        / "tests"
                        / "fixtures"
                        / "protocol"
                        / "interaction-v1"
                        / "message-delta.json"
                    ).read_text(encoding="utf-8")
                )
                self._send_json(publisher, interaction_event)
                update = json.loads(subscriber_reader.readline())

                bridge.terminate()
                stdout, stderr = bridge.communicate(timeout=3)
            finally:
                if subscriber_reader is not None:
                    subscriber_reader.close()
                if publisher_reader is not None:
                    publisher_reader.close()
                subscriber.close()
                publisher.close()
                self._stop_bridge(bridge)

            self.assertEqual(bridge.returncode, -15, stderr)
            self.assertEqual(update["message_type"], "interaction_update")
            self.assertEqual(update["subscription_id"], started["subscription_id"])
            self.assertEqual(update["sequence"], 1)
            self.assertEqual(update["event"], interaction_event)
            self.assertIn("agent=demo:combined", stdout)
            self.assertNotIn("任务正在执行", stdout)

    def test_subscriber_cannot_combine_state_and_interaction_planes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "bridge.sock"
            bridge, _ = self._start_bridge(socket_path)
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(3)
            reader = None
            try:
                client.connect(str(socket_path))
                reader = client.makefile("rb")
                self._send_json(
                    client,
                    {
                        "protocol_version": 1,
                        "message_type": "client_hello",
                        "client_id": "conflicting-subscriber",
                        "role": "subscriber",
                        "supported_versions": [1],
                        "capabilities": [
                            "state_subscription_v1",
                            "interaction_subscription_v1",
                        ],
                    },
                )
                error = json.loads(reader.readline())
            finally:
                if reader is not None:
                    reader.close()
                client.close()
                self._stop_bridge(bridge)

            self.assertEqual(error["message_type"], "protocol_error")
            self.assertEqual(error["code"], "capability_conflict")

    def test_legacy_connection_continues_after_invalid_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / "bridge.sock"
            bridge, _ = self._start_bridge(socket_path, max_events=2)
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                client.connect(str(socket_path))
                self._send_json(
                    client,
                    {"agent_id": "legacy:first", "slot": 0, "state": "thinking"},
                )
                self._send_json(
                    client,
                    {"agent_id": "legacy:invalid", "slot": 0, "state": "unknown"},
                )
                self._send_json(
                    client,
                    {"agent_id": "legacy:second", "slot": 1, "state": "completed"},
                )
                stdout, stderr = bridge.communicate(timeout=3)
            finally:
                client.close()
                self._stop_bridge(bridge)

            self.assertEqual(bridge.returncode, 0, stderr)
            self.assertIn("agent=legacy:first", stdout)
            self.assertIn("agent=legacy:second", stdout)
            self.assertIn("error='unknown' is not a valid AgentState", stdout)

    @staticmethod
    def _send_json(client: socket.socket, value: dict[str, object]) -> None:
        client.sendall(
            (json.dumps(value, separators=(",", ":")) + "\n").encode("utf-8")
        )

    @staticmethod
    def _send_subscriber_hello(
        client: socket.socket,
        client_id: str,
        *,
        capability: str = "state_subscription_v1",
    ) -> None:
        BridgeEndToEndTests._send_json(
            client,
            {
                "protocol_version": 1,
                "message_type": "client_hello",
                "client_id": client_id,
                "role": "subscriber",
                "supported_versions": [1],
                "capabilities": [capability],
            },
        )

    @staticmethod
    def _adapter_session(action: str) -> dict[str, object]:
        value = json.loads(
            (
                ROOT
                / "tests"
                / "fixtures"
                / "protocol"
                / "adapter-session-v1"
                / f"{action}.json"
            ).read_text(encoding="utf-8")
        )
        return value

    @staticmethod
    def _focus_command(command_id: str, idempotency_key: str) -> dict[str, object]:
        now_ms = int(time.time() * 1000)
        return {
            "protocol_version": 1,
            "message_type": "control_command",
            "command_id": command_id,
            "kind": "focus",
            "agent_id": "codex",
            "session_id": "session-42",
            "project_id": "deskhelm",
            "issued_by": "lifecycle-controller",
            "issued_at": now_ms,
            "expires_at": now_ms + 30_000,
            "idempotency_key": idempotency_key,
            "payload": {},
        }

    @staticmethod
    def _start_bridge(
        socket_path: Path,
        *,
        max_events: int | None = None,
        extra_args: list[str] | None = None,
    ) -> tuple[subprocess.Popen[str], dict[str, str]]:
        environment = {**os.environ, "PYTHONPATH": PYTHONPATH}
        command = [
            sys.executable,
            "-m",
            "deskhelm_bridge",
            "bridge",
            "--plain",
            "--socket",
            str(socket_path),
        ]
        if max_events is not None:
            command.extend(["--max-events", str(max_events)])
        if extra_args is not None:
            command.extend(extra_args)
        bridge = subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 3
        while not socket_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        if not socket_path.exists():
            BridgeEndToEndTests._stop_bridge(bridge)
            raise AssertionError("bridge socket was not created")
        return bridge, environment

    @staticmethod
    def _stop_bridge(bridge: subprocess.Popen[str]) -> None:
        if bridge.poll() is None:
            bridge.terminate()
            bridge.wait(timeout=3)
        if bridge.stdout is not None and not bridge.stdout.closed:
            bridge.stdout.close()
        if bridge.stderr is not None and not bridge.stderr.closed:
            bridge.stderr.close()


if __name__ == "__main__":
    unittest.main()

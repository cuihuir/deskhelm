from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
for kind in ("claude", "grok"):
    adapter = ROOT / "adapters" / kind
    if str(adapter) not in sys.path:
        sys.path.insert(0, str(adapter))

from deskhelm_bridge.control import ControlCommand, ControlUndelivered
from deskhelm_bridge.runtime_directory import (
    RUNTIME_KIND_CLAUDE,
    RUNTIME_KIND_GROK,
    RuntimeAttachSpec,
)
from deskhelm_bridge.session_registry import SessionKey
from deskhelm_claude_adapter.runtime import ClaudeRelayRuntime
from deskhelm_grok_adapter.runtime import GrokRelayRuntime


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "protocol"


class _Facade:
    def register_handler(self, kind, handler):
        del kind, handler
        return lambda: None

    def register_catalog_provider(self, provider):
        del provider
        return lambda: None


class RelayRuntimeDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.relay_path = Path(directory.name) / "relay.sock"
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(self.listener.close)
        self.listener.bind(str(self.relay_path))
        self.listener.listen(1)
        self.published: list = []

    def test_grok_prompt_before_the_session_is_ready_is_undelivered_at_once(self) -> None:
        runtime, _ = self._start(GrokRelayRuntime, RUNTIME_KIND_GROK)

        started = time.monotonic()
        with self.assertRaises(ControlUndelivered):
            runtime.submit_prompt(_command("submit-prompt.json"))

        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(self.published, [])

    def test_grok_prompt_after_the_session_is_ready_is_written(self) -> None:
        runtime, peer = self._start(GrokRelayRuntime, RUNTIME_KIND_GROK)
        peer.sendall(_line({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": 1}}))
        peer.sendall(
            _line({"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "vendor-session"}})
        )
        _wait_for(lambda: runtime._decoder.session_id_vendor == "vendor-session")
        command = _command("submit-prompt.json")

        runtime.submit_prompt(command)
        frame = json.loads(peer.makefile("rb").readline())

        self.assertEqual(frame["method"], "session/prompt")
        self.assertEqual(len(self.published), 1)
        self.assertEqual(self.published[0].payload.text, command.payload.text)

    def test_grok_decision_on_a_closed_relay_keeps_the_request_pending(self) -> None:
        runtime, peer = self._start(GrokRelayRuntime, RUNTIME_KIND_GROK)
        peer.sendall(
            _line(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "session/request_permission",
                    "params": {
                        "toolCall": {"kind": "execute"},
                        "options": [
                            {"optionId": "allow-once", "kind": "allow_once"},
                            {"optionId": "reject-once", "kind": "reject_once"},
                        ],
                    },
                }
            )
        )
        _wait_for(lambda: bool(self.published))
        request_id = self.published[-1].payload.request_id
        runtime.close()
        command = _command("approve.json")
        approve = replace(command, payload=replace(command.payload, request_id=request_id))

        with self.assertRaises(ControlUndelivered):
            runtime.approve(approve)

        self.assertIn(request_id, runtime._decoder.pending_permissions)

    def test_claude_prompt_on_a_closed_relay_is_undelivered(self) -> None:
        runtime, _ = self._start(ClaudeRelayRuntime, RUNTIME_KIND_CLAUDE)
        runtime.close()

        with self.assertRaises(ControlUndelivered):
            runtime.submit_prompt(_command("submit-prompt.json"))

        self.assertEqual(self.published, [])

    def _start(self, runtime_class, kind: str):
        spec = RuntimeAttachSpec(
            session=SessionKey("agent", "local-1", "demo"),
            source_version="test",
            runtime_kind=kind,
            relay_socket=self.relay_path,
        )
        runtime = runtime_class(spec)
        runtime.start(self.published.append, _Facade())
        self.addCleanup(runtime.close)
        peer, _ = self.listener.accept()
        peer.settimeout(2)
        self.addCleanup(peer.close)
        return runtime, peer


def _command(name: str) -> ControlCommand:
    value = json.loads((FIXTURE_DIR / "control-v1" / name).read_text(encoding="utf-8"))
    return ControlCommand.from_dict(value)


def _line(value: dict) -> bytes:
    return json.dumps(value).encode("utf-8") + b"\n"


def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition was not met in time")
        time.sleep(0.01)


if __name__ == "__main__":
    unittest.main()

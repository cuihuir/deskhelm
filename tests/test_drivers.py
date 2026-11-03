from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from helpers.replay import _FakeVendor  # noqa: E402

from deskhelm_acp_adapter import AcpSession, GrokFlavor  # noqa: E402
from deskhelm_claude_adapter.driver import MAX_PENDING_APPROVALS, ClaudeSession  # noqa: E402
from deskhelm_protocol import EffectKind, EffectOutcome, FactKind  # noqa: E402


FIXTURES = TESTS / "fixtures" / "adapters"


class _Recorder:
    def __init__(self) -> None:
        self.facts: list[tuple[FactKind, dict, dict]] = []

    def __call__(self, kind, meta=None, content=None) -> None:
        self.facts.append((kind, meta or {}, content or {}))

    def kinds(self) -> list[str]:
        return [kind.value for kind, _, _ in self.facts]

    def of(self, kind: FactKind) -> list[tuple[dict, dict]]:
        return [(meta, content) for fact_kind, meta, content in self.facts if fact_kind is kind]

    def wait_for(self, kind: FactKind, count: int = 1) -> None:
        deadline = time.monotonic() + 2
        while len(self.of(kind)) < count:
            if time.monotonic() >= deadline:
                raise AssertionError(f"no {kind.value} fact; saw {self.kinds()}")
            time.sleep(0.01)


def _written(vendor: _FakeVendor) -> list[dict]:
    lines = []
    while not vendor.written.empty():
        lines.append(vendor.written.get_nowait())
    return lines


class _DriverCase(unittest.TestCase):
    def start(self, factory):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.vendor = _FakeVendor()
        self.clock = [1_000_000]
        self.driver = factory(Path(directory.name), self.vendor, lambda: self.clock[0])
        self.facts = _Recorder()
        self.driver.start(self.facts)
        self.addCleanup(self.driver.close)

    def feed(self, *frames: dict) -> None:
        self.vendor.batches.put(list(frames))


class ClaudeDriverTests(_DriverCase):
    def setUp(self) -> None:
        self.start(lambda cwd, vendor, clock: ClaudeSession(cwd, vendor=vendor, clock=clock))
        self.feed({"type": "system", "subtype": "init", "model": "example-model", "skills": ["review"]})
        self.facts.wait_for(FactKind.READY)

    def control_request(self, request_id: str, tool: str = "Bash") -> dict:
        return {
            "type": "control_request",
            "request_id": request_id,
            "request": {"subtype": "can_use_tool", "tool_name": tool, "input": {"command": "secret-argument"}},
        }

    def test_approval_overflow_is_declined_instead_of_dropped(self) -> None:
        self.feed(*(self.control_request(f"req-{index}") for index in range(MAX_PENDING_APPROVALS + 1)))
        self.facts.wait_for(FactKind.REQUEST_OPENED, MAX_PENDING_APPROVALS)
        deadline = time.monotonic() + 2
        while self.vendor.written.empty() and time.monotonic() < deadline:
            time.sleep(0.01)

        declined = _written(self.vendor)
        self.assertEqual(len(self.facts.of(FactKind.REQUEST_OPENED)), MAX_PENDING_APPROVALS)
        self.assertEqual(declined[0]["response"]["request_id"], f"req-{MAX_PENDING_APPROVALS}")
        self.assertEqual(declined[0]["response"]["response"]["behavior"], "deny")

    def test_unknown_control_request_is_answered(self) -> None:
        self.feed({"type": "control_request", "request_id": "req-x", "request": {"subtype": "hook_callback"}})
        frame = self.vendor.written.get(timeout=2)

        self.assertEqual(frame["response"], {"subtype": "error", "request_id": "req-x", "error": "unsupported request"})

    def test_expired_approval_is_declined_and_resolved(self) -> None:
        self.feed(self.control_request("req-1"))
        self.facts.wait_for(FactKind.REQUEST_OPENED)
        self.clock[0] += 10 * 60 * 1000
        self.facts.wait_for(FactKind.REQUEST_RESOLVED)

        self.assertEqual(self.facts.of(FactKind.REQUEST_RESOLVED)[0][0], {"provider_ref": "req-1", "by": "expired"})
        self.assertEqual(self.vendor.written.get(timeout=2)["response"]["response"]["behavior"], "deny")

    def test_interrupted_turn_and_usage_limit_are_classified(self) -> None:
        self.driver.perform(EffectKind.SUBMIT, {"command_id": "c1", "text": "work"})
        self.driver.perform(EffectKind.INTERRUPT, {"command_id": "c2"})
        self.feed({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": ""})
        self.facts.wait_for(FactKind.TURN_TERMINAL)
        self.driver.perform(EffectKind.SUBMIT, {"command_id": "c3", "text": "more"})
        self.feed({"type": "result", "subtype": "success", "is_error": True, "result": "Claude AI usage limit reached"})
        self.facts.wait_for(FactKind.TURN_TERMINAL, 2)

        first, second = (meta for meta, _ in self.facts.of(FactKind.TURN_TERMINAL))
        self.assertEqual((first["status"], first["turn_ref"]), ("interrupted", "c1"))
        self.assertEqual((second["status"], second["failure"]), ("failed", "usage_limit"))

    def test_vendor_exit_settles_the_turn_and_ends_the_session(self) -> None:
        self.driver.perform(EffectKind.SUBMIT, {"command_id": "c1", "text": "work"})
        self.feed(self.control_request("req-1"))
        self.facts.wait_for(FactKind.REQUEST_OPENED)
        self.vendor.terminate()
        self.facts.wait_for(FactKind.SESSION_ENDED)

        terminal = self.facts.of(FactKind.TURN_TERMINAL)[0][0]
        self.assertEqual((terminal["status"], terminal["failure"]), ("failed", "process_exit"))
        self.assertEqual(self.facts.of(FactKind.REQUEST_RESOLVED)[0][0]["by"], "cancelled")

    def test_echo_is_skipped_and_tool_names_are_bare_labels(self) -> None:
        self.driver.perform(EffectKind.SUBMIT, {"command_id": "c1", "text": "hello"})
        self.feed(
            {"type": "user", "message": {"role": "user", "content": "hello"}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash; rm -rf /", "input": {}}]}},
        )
        self.facts.wait_for(FactKind.TOOL)

        users = [meta for meta, _ in self.facts.of(FactKind.MESSAGE) if meta["role"] == "user"]
        self.assertEqual(len(users), 1)
        self.assertEqual(self.facts.of(FactKind.TOOL)[0][0]["label"], "tool")
        self.assertNotIn("secret-argument", json.dumps(self.facts.facts))

    def test_closed_vendor_makes_submit_undelivered(self) -> None:
        self.vendor.terminate()

        outcome = self.driver.perform(EffectKind.SUBMIT, {"command_id": "c1", "text": "hello"})
        self.assertIs(outcome, EffectOutcome.UNDELIVERED)

    def test_permission_request_publishes_only_the_tool_name(self) -> None:
        # The field set Claude Code 2.1.312 sends; every value is synthetic.
        tool_input = {"file_path": "/workspace/private/notes.txt", "content": "draft"}
        self.feed(
            {
                "type": "control_request",
                "request_id": "req-9",
                "request": {
                    "subtype": "can_use_tool",
                    "tool_name": "Write",
                    "display_name": "Write",
                    "input": tool_input,
                    "description": "/workspace/private/notes.txt",
                    "permission_suggestions": [
                        {"type": "setMode", "mode": "acceptEdits", "destination": "session"},
                        {"type": "addDirectories", "directories": ["/workspace/private"], "destination": "session"},
                    ],
                    "decision_reason": "Path is outside allowed working directories",
                    "decision_reason_type": "workingDir",
                    "tool_use_id": "toolu_example",
                },
            }
        )
        self.facts.wait_for(FactKind.REQUEST_OPENED)
        outcome = self.driver.perform(EffectKind.RESPOND, {"command_id": "a", "provider_ref": "req-9", "decision": "approve"})

        self.assertIs(outcome, EffectOutcome.ACCEPTED)
        self.assertEqual(self.facts.of(FactKind.REQUEST_OPENED)[0][0]["summary"], "请求使用工具 Write")
        self.assertNotIn("/workspace", json.dumps(self.facts.facts))
        self.assertEqual(
            self.vendor.written.get(timeout=2)["response"],
            {"subtype": "success", "request_id": "req-9", "response": {"behavior": "allow", "updatedInput": tool_input}},
        )

    def test_question_tool_is_declined_without_an_approval(self) -> None:
        self.feed(self.control_request("req-q", tool="AskUserQuestion"))
        frame = self.vendor.written.get(timeout=2)

        self.assertEqual((frame["response"]["request_id"], frame["response"]["response"]["behavior"]), ("req-q", "deny"))
        self.assertEqual(self.facts.of(FactKind.REQUEST_OPENED), [])
        self.assertEqual(self.driver.state().open_requests, ())


class AcpDriverTests(_DriverCase):
    def setUp(self) -> None:
        self.start(
            lambda cwd, vendor, clock: AcpSession(
                GrokFlavor(cwd / "leader.sock"), cwd, runtime_version="test", vendor=vendor, clock=clock
            )
        )
        _written(self.vendor)
        self.feed(
            {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}}},
            {"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "vendor-session"}},
        )
        self.facts.wait_for(FactKind.READY)

    def permission(self, rpc_id: int, options: list[dict]) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "session/request_permission",
            "params": {"toolCall": {"title": "Execute `rm -rf ~/x`", "kind": "execute"}, "options": options},
        }

    def test_yolo_session_reports_unanswerable_approvals(self) -> None:
        self.feed(
            {"jsonrpc": "2.0", "method": "_x.ai/sessions/changed", "params": {"upserted": [
                {"sessionId": "vendor-session", "yolo": True, "cwd": "/home/someone/project"}]}}
        )
        self.facts.wait_for(FactKind.CAPABILITIES)

        meta = self.facts.of(FactKind.CAPABILITIES)[0][0]
        self.assertEqual(meta["capabilities"]["approvals"], "none")
        self.assertEqual(self.driver.capabilities.approvals.value, "none")
        self.assertNotIn("/home/someone", json.dumps(self.facts.facts))

    def test_unknown_agent_request_gets_an_error(self) -> None:
        self.feed({"jsonrpc": "2.0", "id": 41, "method": "fs/read_text_file", "params": {"path": "/etc/hosts"}})

        frame = self.vendor.written.get(timeout=2)
        self.assertEqual((frame["id"], frame["error"]["code"]), (41, -32601))

    def test_second_prompt_during_a_turn_is_unsupported(self) -> None:
        first = self.driver.perform(EffectKind.SUBMIT, {"command_id": "c1", "text": "one"})
        second = self.driver.perform(EffectKind.SUBMIT, {"command_id": "c2", "text": "two"})

        self.assertEqual((first, second), (EffectOutcome.ACCEPTED, EffectOutcome.UNSUPPORTED))

    def test_decision_without_a_one_time_option_is_not_sent(self) -> None:
        self.feed(self.permission(31, [{"optionId": "always", "kind": "allow_always"}]))
        self.facts.wait_for(FactKind.REQUEST_OPENED)
        meta = self.facts.of(FactKind.REQUEST_OPENED)[0][0]

        outcome = self.driver.perform(EffectKind.RESPOND, {"command_id": "a", "provider_ref": meta["provider_ref"], "decision": "approve"})
        self.assertIs(outcome, EffectOutcome.UNSUPPORTED)
        self.assertFalse(meta["answerable"])
        self.assertEqual(meta["summary"], "请求使用工具 execute")
        self.assertEqual(self.driver.state().open_requests[0].provider_ref, meta["provider_ref"])

    def test_repeated_tool_output_is_published_once_and_diffs_never(self) -> None:
        lines = [json.loads(line) for line in (FIXTURES / "grok-stdio" / "synthetic-tool-content.jsonl").read_text().splitlines()]
        repeat = json.loads(json.dumps(lines[1]))
        repeat["params"]["update"]["status"] = "completed"
        self.feed(lines[0], lines[1], repeat, lines[2])
        deadline = time.monotonic() + 2
        while len(self.facts.of(FactKind.TOOL)) < 4 and time.monotonic() < deadline:
            time.sleep(0.01)

        phases = [meta["phase"] for meta, _ in self.facts.of(FactKind.TOOL)]
        self.assertEqual(phases, ["start", "output", "complete", "complete"])
        self.assertNotIn("<workspace>", json.dumps(self.facts.facts))

    def test_a_tool_keeps_its_first_label(self) -> None:
        self.driver.perform(EffectKind.SUBMIT, {"command_id": "c1", "text": "read it"})
        self.feed(
            {"jsonrpc": "2.0", "method": "session/update", "params": {"update": {
                "sessionUpdate": "tool_call", "toolCallId": "t1", "title": "Read", "kind": "read"}}},
            {"jsonrpc": "2.0", "method": "session/update", "params": {"update": {
                "sessionUpdate": "tool_call_update", "toolCallId": "t1", "title": "Read /home/someone/notes.txt",
                "kind": "read", "status": "completed"}}},
        )
        self.facts.wait_for(FactKind.TOOL, 2)

        self.assertEqual([meta["label"] for meta, _ in self.facts.of(FactKind.TOOL)], ["Read", "Read"])
        self.assertNotIn("/home/someone", json.dumps(self.facts.facts))

    def test_permission_overflow_declines_once(self) -> None:
        options = [{"optionId": "always-allow", "kind": "allow_always"},
                   {"optionId": "allow-once", "kind": "allow_once"},
                   {"optionId": "reject-once", "kind": "reject_once"}]
        self.feed(*(self.permission(100 + index, options) for index in range(9)))
        self.facts.wait_for(FactKind.REQUEST_OPENED, 8)
        frame = self.vendor.written.get(timeout=2)

        self.assertEqual(frame, {"jsonrpc": "2.0", "id": 108, "result": {"outcome": {"outcome": "selected", "optionId": "reject-once"}}})

    def test_vendor_exit_settles_the_turn_and_ends_the_session(self) -> None:
        self.driver.perform(EffectKind.SUBMIT, {"command_id": "c1", "text": "work"})
        self.vendor.terminate()
        self.facts.wait_for(FactKind.SESSION_ENDED)

        terminal = self.facts.of(FactKind.TURN_TERMINAL)[0][0]
        self.assertEqual((terminal["status"], terminal["failure"]), ("failed", "process_exit"))


if __name__ == "__main__":
    unittest.main()

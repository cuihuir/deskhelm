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

from helpers.replay import _FakeVendor, _QueueTransport  # noqa: E402

from deskhelm_acp_adapter import AcpSession, GrokFlavor  # noqa: E402
from deskhelm_claude_adapter.command import claude_argv  # noqa: E402
from deskhelm_claude_adapter.driver import MAX_PENDING_APPROVALS, ClaudeSession  # noqa: E402
from deskhelm_codex_adapter.driver import CodexSession  # noqa: E402
from deskhelm_protocol import EffectKind, EffectOutcome, FactKind, SessionKey  # noqa: E402


FIXTURES = TESTS / "fixtures" / "adapters"


def _lines(name: str, file: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURES / name / file).read_text(encoding="utf-8").splitlines() if line]


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


class ClaudeReadinessTests(_DriverCase):
    def setUp(self) -> None:
        self.start(lambda cwd, vendor, clock: ClaudeSession(cwd, vendor=vendor, clock=clock))
        self.request = self.vendor.written.get(timeout=2)

    def answer(self, request_id: str) -> dict:
        response = {"account": {"email": "someone@example.com"}, "commands": [{"name": "review"}]}
        return {"type": "control_response", "response": {"subtype": "success", "request_id": request_id, "response": response}}

    def test_the_answer_to_initialize_makes_a_new_session_ready(self) -> None:
        # Claude Code 2.1.312 sends system/init only with the first prompt.
        self.assertEqual(self.request["request"], {"subtype": "initialize"})
        self.feed(self.answer("another-request"), {"type": "control_request", "request_id": "req-x", "request": {"subtype": "hook_callback"}})
        self.vendor.written.get(timeout=2)
        self.assertFalse(self.driver.state().ready)

        self.feed(self.answer(self.request["request_id"]))
        self.facts.wait_for(FactKind.READY)

        self.assertTrue(self.driver.state().ready)
        self.assertNotIn("someone@example.com", repr(self.facts.facts))

    def test_init_with_the_first_prompt_adds_the_catalog_without_a_second_ready(self) -> None:
        self.feed(self.answer(self.request["request_id"]))
        self.facts.wait_for(FactKind.READY)
        self.driver.perform(EffectKind.SUBMIT, {"command_id": "c1", "text": "hello"})
        self.feed({"type": "system", "subtype": "init", "model": "example-model"})
        self.facts.wait_for(FactKind.CATALOG)

        self.assertEqual(len(self.facts.of(FactKind.READY)), 1)
        self.assertEqual(self.driver.state().catalog["model"], "example-model")


class ClaudeDriverTests(_DriverCase):
    def setUp(self) -> None:
        self.start(lambda cwd, vendor, clock: ClaudeSession(cwd, vendor=vendor, clock=clock))
        self.assertEqual(self.vendor.written.get(timeout=2)["request"], {"subtype": "initialize"})
        self.feed({"type": "system", "subtype": "init", "model": "example-model", "skills": ["review"]})
        self.facts.wait_for(FactKind.READY)

    def control_request(self, request_id: str, tool: str = "Bash") -> dict:
        return {
            "type": "control_request",
            "request_id": request_id,
            "request": {"subtype": "can_use_tool", "tool_name": tool, "input": {"command": "secret-argument"}},
        }

    def delta(self, text: str) -> dict:
        return {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}}

    def replies(self) -> list[str]:
        return [content["text"] for meta, content in self.facts.of(FactKind.MESSAGE)
                if meta["role"] == "assistant" and meta["phase"] == "complete"]

    def test_a_reply_cut_off_mid_stream_completes_with_what_streamed(self) -> None:
        self.driver.perform(EffectKind.SUBMIT, {"command_id": "c1", "text": "write"})
        self.feed(self.delta("Hello "), self.delta("there"), {"type": "result", "subtype": "success", "result": ""})
        self.facts.wait_for(FactKind.TURN_TERMINAL)

        self.assertEqual(self.replies(), ["Hello there"])

    def test_a_long_reply_completes_whole(self) -> None:
        text = "x" * 20_000
        self.driver.perform(EffectKind.SUBMIT, {"command_id": "c1", "text": "write"})
        self.feed({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}},
                  {"type": "result", "subtype": "success", "result": text})
        self.facts.wait_for(FactKind.TURN_TERMINAL)

        self.assertEqual(self.replies(), [text])

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

    def test_the_synthetic_stream_keeps_thoughts_and_errors_private_and_arguments_in_content(self) -> None:
        lines = _lines("claude-stream", "synthetic-turn.jsonl")
        self.driver.perform(EffectKind.SUBMIT, {"command_id": "c1", "text": "Reply pong"})
        self.feed(*lines[:5])
        self.facts.wait_for(FactKind.TURN_TERMINAL)
        self.driver.perform(EffectKind.SUBMIT, {"command_id": "c2", "text": "again"})
        self.feed(lines[6])
        self.facts.wait_for(FactKind.TURN_TERMINAL, 2)

        catalog = self.facts.of(FactKind.CATALOG)[-1][1]["catalog"]
        tools = self.facts.of(FactKind.TOOL)
        first, second = (meta for meta, _ in self.facts.of(FactKind.TURN_TERMINAL))
        self.assertEqual((catalog["model"], catalog["skills"][0]["name"], catalog["mcp_servers"][0]["name"]),
                         ("example-model", "review", "docs"))
        self.assertEqual([(meta["label"], meta["phase"]) for meta, _ in tools],
                         [("Read", "start"), ("Read", "output"), ("Read", "complete")])
        self.assertEqual(tools[0][1], {"text": "secret-argument"})
        self.assertNotIn("exit_code", tools[2][0])
        self.assertEqual((first["status"], second["status"], second["failure"]), ("completed", "failed", "provider"))
        self.assertNotIn("secret-argument", json.dumps([meta for _, meta, _ in self.facts.facts]))
        blob = json.dumps(self.facts.facts)
        for private in ("hidden thought", "private vendor boom"):
            self.assertNotIn(private, blob)

    def test_a_refused_tool_fails_with_error_output_and_no_exit_code(self) -> None:
        self.driver.perform(EffectKind.SUBMIT, {"command_id": "c1", "text": "delete it"})
        bash = {"command": "rm hello.py", "description": "Delete hello.py"}
        self.feed(
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": bash}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": ""}]}},
        )
        self.facts.wait_for(FactKind.TOOL, 3)

        start, output, complete = self.facts.of(FactKind.TOOL)
        self.assertEqual(start, ({"tool_ref": "t1", "label": "Bash", "phase": "start"}, {"text": "rm hello.py"}))
        self.assertEqual(output[0]["stream"], "stderr")
        self.assertNotIn("exit_code", complete[0])

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

    def test_the_synthetic_turn_keeps_thoughts_private_and_arguments_in_content(self) -> None:
        lines = _lines("grok-stdio", "synthetic-turn.jsonl")
        self.driver.perform(EffectKind.SUBMIT, {"command_id": "c1", "text": "Reply pong"})
        self.feed(*lines[:7])
        self.facts.wait_for(FactKind.TURN_TERMINAL)

        catalog = self.facts.of(FactKind.CATALOG)[-1][1]["catalog"]
        tools = self.facts.of(FactKind.TOOL)
        self.assertIn("promptCapabilities.embeddedContext", catalog["agent_capabilities"])
        self.assertIn("sessionCapabilities.list", catalog["agent_capabilities"])
        self.assertFalse(any(name == "auth" or name.startswith("_") for name in catalog["agent_capabilities"]))
        self.assertEqual(catalog["model"], "example-model")
        self.assertEqual([meta["label"] for meta, _ in tools], ["Read", "Read", "Read"])
        self.assertEqual(tools[0][1], {"text": "secret-argument"})
        self.assertNotIn("exit_code", tools[-1][0])
        self.assertEqual(self.facts.of(FactKind.TURN_TERMINAL)[0][0]["status"], "completed")
        self.assertNotIn("secret-argument", json.dumps([meta for _, meta, _ in self.facts.facts]))
        blob = json.dumps(self.facts.facts)
        for private in ("hidden thought", "vendor-session"):
            self.assertNotIn(private, blob)

    def test_labels_stay_bare_and_titles_go_to_content(self) -> None:
        self.driver.perform(EffectKind.SUBMIT, {"command_id": "c1", "text": "work"})
        self.feed(*_lines("grok-stdio", "synthetic-permissions.jsonl"))
        self.facts.wait_for(FactKind.REQUEST_OPENED, 2)
        self.facts.wait_for(FactKind.TOOL, 2)

        summaries = [meta["summary"] for meta, _ in self.facts.of(FactKind.REQUEST_OPENED)]
        tools = self.facts.of(FactKind.TOOL)
        self.assertEqual(summaries, ["请求使用工具 execute", "请求使用工具 edit"])
        self.assertEqual([meta["label"] for meta, _ in tools], ["read", "run_terminal_command"])
        # A descriptive vendor title is content; a bare name is only the label.
        self.assertEqual([content for _, content in tools], [{"text": "Read package.json"}, {}])
        metas = json.dumps([meta for _, meta, _ in self.facts.facts])
        for private in ("rm -rf", "example-probe", "package.json", "<workspace>", "notes.txt"):
            self.assertNotIn(private, metas)
        for unpublished in ("rm -rf", "<workspace>", "notes.txt"):
            self.assertNotIn(unpublished, json.dumps(self.facts.facts))

    def test_a_tool_takes_its_command_from_raw_input_and_fails_without_an_exit_code(self) -> None:
        # The shape Grok 1.0.60 sent in a local check; every value is synthetic.
        self.driver.perform(EffectKind.SUBMIT, {"command_id": "c1", "text": "work"})
        self.feed(
            {"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "vendor-session", "update": {
                "sessionUpdate": "tool_call", "toolCallId": "tool-x", "title": "run_terminal_command",
                "rawInput": {"command": "false", "description": "Fail on purpose"}}}},
            {"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "vendor-session", "update": {
                "sessionUpdate": "tool_call_update", "toolCallId": "tool-x", "status": "failed"}}},
        )
        self.facts.wait_for(FactKind.TOOL, 3)

        start, output, complete = self.facts.of(FactKind.TOOL)
        self.assertEqual(start, ({"tool_ref": "tool-x", "label": "run_terminal_command", "phase": "start"}, {"text": "false"}))
        self.assertEqual((output[0]["stream"], output[1]), ("stderr", {"text": ""}))
        self.assertNotIn("exit_code", complete[0])

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

    def update(self, **fields) -> dict:
        return {"jsonrpc": "2.0", "method": "session/update", "params": {"update": fields}}

    def content(self, text: str) -> list[dict]:
        return [{"type": "content", "content": {"type": "text", "text": text}}]

    def test_whole_tool_content_is_sent_as_new_text_or_a_replacement(self) -> None:
        # Grok sends a description first, then the whole output so far on each update.
        self.feed(
            self.update(sessionUpdate="tool_call", toolCallId="t1", title="Bash", kind="execute"),
            self.update(sessionUpdate="tool_call_update", toolCallId="t1", content=self.content("Run three tock echoes")),
            self.update(sessionUpdate="tool_call_update", toolCallId="t1", content=self.content("tock 1\n")),
            self.update(sessionUpdate="tool_call_update", toolCallId="t1", content=self.content("tock 1\ntock 2\n")),
            self.update(sessionUpdate="tool_call_update", toolCallId="t1", status="completed",
                        content=self.content("tock 1\ntock 2\n")),
        )
        self.facts.wait_for(FactKind.TOOL, 5)

        outputs = [(content["text"], meta.get("replace", False)) for meta, content in self.facts.of(FactKind.TOOL)
                   if meta["phase"] == "output"]
        self.assertEqual(outputs, [("Run three tock echoes", False), ("tock 1\n", True), ("tock 2\n", False)])

    def test_a_reply_after_a_tool_call_is_a_new_message_with_its_whole_text(self) -> None:
        self.driver.perform(EffectKind.SUBMIT, {"command_id": "c1", "text": "run the tests"})
        prompt = next(frame for frame in iter(lambda: self.vendor.written.get(timeout=2), None)
                      if frame.get("method") == "session/prompt")
        self.feed(
            self.update(sessionUpdate="agent_message_chunk", content={"type": "text", "text": "I'll run the tests."}),
            self.update(sessionUpdate="tool_call", toolCallId="t1", title="Bash", kind="execute"),
            self.update(sessionUpdate="agent_message_chunk", content={"type": "text", "text": "All 12 "}),
            self.update(sessionUpdate="agent_message_chunk", content={"type": "text", "text": "passed."}),
            {"jsonrpc": "2.0", "id": prompt["id"], "result": {"stopReason": "end_turn"}},
        )
        self.facts.wait_for(FactKind.TURN_TERMINAL)

        replies = [(meta["message_ref"], content["text"]) for meta, content in self.facts.of(FactKind.MESSAGE)
                   if meta["role"] == "assistant" and meta["phase"] == "complete"]
        self.assertEqual([text for _, text in replies], ["I'll run the tests.", "All 12 passed."])
        self.assertNotEqual(replies[0][0], replies[1][0])

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


class VendorCommandTests(unittest.TestCase):
    def test_vendor_commands_carry_no_prompt(self) -> None:
        claude = claude_argv()
        grok = GrokFlavor(Path("/tmp/deskhelm-grok-leader.sock")).argv()

        self.assertEqual(claude[claude.index("--input-format") + 1], "stream-json")
        self.assertEqual(claude[claude.index("--permission-prompt-tool") + 1], "stdio")
        self.assertEqual(claude[claude.index("--permission-mode") + 1], "default")
        self.assertEqual((grok[-1], "--no-leader" in grok), ("stdio", True))
        self.assertIn("/tmp/deskhelm-grok-leader.sock", grok)
        for argv in (claude, grok):
            self.assertNotIn("Reply pong", " ".join(argv))


def _codex(transport: _QueueTransport) -> CodexSession:
    return CodexSession(
        SessionKey("codex", "local-1", "demo"),
        socket_path=Path("/tmp/deskhelm-codex-test.sock"),
        runtime_version="test",
        refresh_catalog=False,
        transport_factory=lambda: transport,
        discovery_interval_seconds=3600,
        reconnect_initial_seconds=0.01,
        reconnect_max_seconds=0.02,
    )


class CodexDriverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.transport = _QueueTransport()
        self.injected = 0
        self.facts = _Recorder()
        self.driver = _codex(self.transport)
        self.driver.start(self.facts)
        self.addCleanup(self.driver.close)
        self.answer("initialize", {})
        self.answer("thread/loaded/list", {"data": ["thread-1"], "nextCursor": None})
        self.facts.wait_for(FactKind.READY)

    def outgoing(self, wanted) -> dict:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if not self.transport.outgoing.empty():
                message = self.transport.outgoing.get_nowait()
                if wanted(message):
                    return message
            time.sleep(0.01)
        raise AssertionError("the adapter did not send the expected frame")

    def inject(self, *frames: dict) -> None:
        for frame in frames:
            self.injected += 1
            self.transport.incoming.put(frame)
        self.assertTrue(self.transport.wait_until_handled(self.injected, 2))

    def answer(self, method: str, result: dict) -> None:
        request = self.outgoing(lambda message: message.get("method") == method)
        self.inject({"id": request["id"], "result": result})

    def turn_started(self, turn_id: str = "turn-1") -> dict:
        return {"method": "turn/started", "params": {"threadId": "thread-1", "turn": {"id": turn_id, "items": [], "status": "inProgress"}}}

    def test_a_tui_turn_keeps_tool_labels_fixed_and_commands_in_content(self) -> None:
        command = {"type": "commandExecution", "id": "item-1", "command": "rm -rf build", "status": "inProgress"}
        self.inject(
            self.turn_started("turn-k"),
            {"method": "item/started", "params": {"threadId": "thread-1", "item": command}},
            {"method": "item/completed", "params": {"threadId": "thread-1", "item": {
                **command, "aggregatedOutput": "removed", "exitCode": 0, "status": "completed"}}},
            {"method": "turn/completed", "params": {"threadId": "thread-1", "turn": {"id": "turn-k", "items": [], "status": "completed"}}},
        )
        self.facts.wait_for(FactKind.TURN_TERMINAL)

        tools = self.facts.of(FactKind.TOOL)
        self.assertNotIn("command_id", self.facts.of(FactKind.TURN_STARTED)[0][0])
        self.assertEqual([meta["label"] for meta, _ in tools], ["command", "command", "command"])
        self.assertEqual((tools[0][1]["text"], tools[1][1]["text"]), ("rm -rf build", "removed"))
        self.assertNotIn("rm -rf", json.dumps([meta for meta, _ in tools]))
        self.assertEqual(self.facts.of(FactKind.TURN_TERMINAL)[0][0]["status"], "completed")

    def test_a_prompt_opens_the_turn_under_its_command(self) -> None:
        outcome = self.driver.perform(EffectKind.SUBMIT, {"command_id": "c1", "text": "Reply pong"})
        self.outgoing(lambda message: message.get("method") == "turn/start")

        message = self.facts.of(FactKind.MESSAGE)[0][0]
        started = self.facts.of(FactKind.TURN_STARTED)[0][0]
        self.assertIs(outcome, EffectOutcome.ACCEPTED)
        self.assertEqual((message["role"], message["command_id"]), ("user", "c1"))
        self.assertEqual((started["turn_ref"], started["command_id"]), ("c1", "c1"))

    def test_a_refused_steer_is_a_notice_and_the_turn_goes_on(self) -> None:
        self.driver.perform(EffectKind.SUBMIT, {"command_id": "c1", "text": "first"})
        self.answer("turn/start", {"turn": {"id": "turn-1", "items": [], "status": "inProgress"}})
        self.driver.perform(EffectKind.SUBMIT, {"command_id": "c2", "text": "and this"})
        steer = self.outgoing(lambda message: message.get("method") == "turn/steer")
        self.inject({"id": steer["id"], "error": {"code": -32600, "message": "steer refused"}})

        notices = [meta for meta, _ in self.facts.of(FactKind.MESSAGE) if meta["role"] == "system"]
        self.assertEqual(notices[0]["command_id"], "c2")
        self.assertEqual(self.facts.of(FactKind.TURN_TERMINAL), [])
        self.assertEqual(self.driver.state().turn_ref, "c1")

    def test_an_approval_is_answered_on_its_request(self) -> None:
        self.inject(
            {"method": "item/commandExecution/requestApproval", "id": "rpc-1", "params": {
                "threadId": "thread-1", "turnId": "turn-1", "itemId": "item-1", "startedAtMs": 1,
                "command": "make test", "reason": "private reason"}}
        )
        self.facts.wait_for(FactKind.REQUEST_OPENED)
        opened = self.facts.of(FactKind.REQUEST_OPENED)[0][0]

        outcome = self.driver.perform(
            EffectKind.RESPOND, {"command_id": "a1", "provider_ref": opened["provider_ref"], "decision": "approve"}
        )
        reply = self.outgoing(lambda message: message.get("id") == "rpc-1")

        self.assertIs(outcome, EffectOutcome.ACCEPTED)
        self.assertEqual(reply["result"], {"decision": "accept"})
        self.assertEqual((opened["summary"], opened["answerable"]), ("Codex command execution approval", True))
        self.assertEqual(self.driver.state().open_requests, ())
        self.assertNotIn("private", json.dumps(self.facts.facts))

    def test_a_prompt_answers_a_single_codex_question(self) -> None:
        self.inject(
            self.turn_started(),
            {"method": "item/tool/requestUserInput", "id": 77, "params": {
                "threadId": "thread-1", "turnId": "turn-1", "itemId": "input-1", "isBlocking": True,
                "autoResolutionMs": None, "questions": [{"id": "choice", "header": "Private header",
                "question": "Private question", "isSecret": False, "options": None}]}},
        )
        self.facts.wait_for(FactKind.REQUEST_OPENED)
        opened = self.facts.of(FactKind.REQUEST_OPENED)[0][0]

        outcome = self.driver.perform(EffectKind.SUBMIT, {"command_id": "c9", "text": "option one"})
        reply = self.outgoing(lambda message: message.get("id") == 77)

        self.assertEqual((opened["kind"], opened["answerable"]), ("input", False))
        self.assertIs(outcome, EffectOutcome.ACCEPTED)
        self.assertEqual(reply["result"], {"answers": {"choice": {"answers": ["option one"]}}})
        self.assertEqual(self.facts.of(FactKind.REQUEST_RESOLVED)[0][0], {"provider_ref": opened["provider_ref"], "by": "deskhelm"})
        self.assertNotIn("Private", json.dumps(self.facts.facts))

    def test_a_model_choice_waits_for_a_fresh_catalog(self) -> None:
        stale = self.driver.perform(EffectKind.CONFIGURE, {"command_id": "m1", "model": "example-model"})
        self.answer("thread/read", {"thread": {"id": "thread-1", "ephemeral": False, "cwd": "/tmp/deskhelm-catalog",
                                               "model": "example-model", "turns": []}})
        self.answer("model/list", {"data": [{"id": "example-model", "model": "example-model", "hidden": False,
                                             "displayName": "Example", "isDefault": True, "defaultReasoningEffort": "medium",
                                             "supportedReasoningEfforts": [{"reasoningEffort": "medium", "description": "balanced"}]}],
                                   "nextCursor": None})
        self.answer("mcpServerStatus/list", {"data": [], "nextCursor": None})
        self.answer("skills/list", {"data": []})
        self.facts.wait_for(FactKind.CATALOG)

        chosen = self.driver.perform(EffectKind.CONFIGURE, {"command_id": "m2", "model": "example-model", "effort": "medium"})
        unknown = self.driver.perform(EffectKind.CONFIGURE, {"command_id": "m3", "model": "missing-model"})
        self.assertEqual((stale, chosen, unknown), (EffectOutcome.UNDELIVERED, EffectOutcome.ACCEPTED, EffectOutcome.UNSUPPORTED))
        self.assertTrue(self.facts.of(FactKind.CATALOG)[0][1]["catalog"]["available"])

    def test_closing_ends_the_open_turn_and_the_session(self) -> None:
        self.driver.perform(EffectKind.SUBMIT, {"command_id": "c1", "text": "work"})
        self.driver.close()

        terminal = self.facts.of(FactKind.TURN_TERMINAL)[0][0]
        self.assertEqual((terminal["status"], terminal["failure"], terminal["turn_ref"]), ("failed", "process_exit", "c1"))
        self.assertEqual(self.facts.kinds()[-1], "session_ended")


class CodexStartupTests(unittest.TestCase):
    def test_prompts_before_a_thread_is_attached_are_undelivered(self) -> None:
        facts = _Recorder()
        driver = _codex(_QueueTransport())
        driver.start(facts)
        self.addCleanup(driver.close)

        outcome = driver.perform(EffectKind.SUBMIT, {"command_id": "c1", "text": "hello"})
        self.assertIs(outcome, EffectOutcome.UNDELIVERED)
        self.assertEqual(facts.kinds(), [])
        self.assertIsNone(driver.state().turn_ref)


if __name__ == "__main__":
    unittest.main()

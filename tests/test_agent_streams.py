from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
for kind in ("claude", "grok"):
    adapter = ROOT / "adapters" / kind
    if str(adapter) not in sys.path:
        sys.path.insert(0, str(adapter))

from deskhelm_bridge.interaction import InteractionKind, MessagePhase
from deskhelm_bridge.session_registry import SessionKey
from deskhelm_claude_adapter.command import claude_argv
from deskhelm_claude_adapter.stream import ClaudeEventDecoder
from deskhelm_grok_adapter.command import grok_argv
from deskhelm_grok_adapter.stream import GrokEventDecoder


def _lines(name: str) -> list[dict]:
    path = ROOT / "tests" / "fixtures" / "adapters" / name / "synthetic-turn.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class ClaudeStreamTests(unittest.TestCase):
    def test_synthetic_turn_publishes_text_tools_and_hides_arguments(self) -> None:
        decoder = ClaudeEventDecoder(SessionKey("claude", "local-1", "demo"), "claude-code 1")
        lines = _lines("claude-stream")
        self.assertEqual(decoder.feed(lines[0]), [])
        self.assertEqual(decoder.catalog["model"], "example-model")
        self.assertEqual(decoder.catalog["skills"][0]["name"], "review")
        self.assertEqual(
            decoder.catalog["agent_capabilities"],
            ["interrupt_receipt_v1", "msg_lifecycle_v1"],
        )
        delta = decoder.feed(lines[1])
        self.assertEqual(delta[0].payload.text, "pon")
        self.assertEqual(delta[0].payload.phase, MessagePhase.DELTA)
        assistant = decoder.feed(lines[2])
        blob = "".join(event.to_json() for event in assistant)
        self.assertNotIn("hidden thought", blob)
        self.assertNotIn("secret-argument", blob)
        self.assertEqual(assistant[0].payload.text, "pong")
        self.assertEqual(assistant[0].payload.phase, MessagePhase.COMPLETE)
        self.assertEqual(assistant[1].kind, InteractionKind.TOOL)
        self.assertEqual(assistant[1].payload.name, "Read")
        tool = decoder.feed(lines[3])
        self.assertEqual(tool[0].payload.name, "Read")
        self.assertEqual(tool[0].payload.text, "file text")
        self.assertEqual(tool[1].payload.exit_code, 0)
        done = decoder.feed(lines[4])
        self.assertEqual(done[-1].kind, InteractionKind.TASK_COMPLETED)
        self.assertNotIn("pong", done[-1].to_json())

    def test_vendor_error_and_dropped_stream_use_fixed_messages(self) -> None:
        decoder = ClaudeEventDecoder(SessionKey("claude", "local-1", "demo"), "claude-code 1")
        decoder.note_outbound_prompt("Reply with the single word pong")
        failed = decoder.feed(_lines("claude-stream")[6])
        self.assertEqual(failed[-1].kind, InteractionKind.TASK_FAILED)
        self.assertEqual(failed[-1].payload.message, "这一轮失败")
        self.assertNotIn("private vendor boom", failed[-1].to_json())

        dropped = ClaudeEventDecoder(SessionKey("claude", "local-1", "demo"), "claude-code 1")
        dropped.note_outbound_prompt("ping")
        ended = dropped.stream_ended()
        self.assertEqual(ended[0].payload.message, "这一轮没有完成")

    def test_tool_approval_keeps_the_request_and_not_the_input(self) -> None:
        decoder = ClaudeEventDecoder(SessionKey("claude", "local-1", "demo"), "claude-code 1")
        events = decoder.feed(_lines("claude-stream")[5])
        self.assertEqual(events[0].kind, InteractionKind.APPROVAL_REQUEST)
        self.assertEqual(events[0].payload.summary, "请求使用工具 Bash")
        self.assertNotIn("secret-argument", events[0].to_json())
        self.assertIn("req-1", decoder.pending_approvals)

    def test_each_prompt_keeps_its_own_id_and_the_echo_is_skipped(self) -> None:
        decoder = ClaudeEventDecoder(SessionKey("claude", "local-1", "demo"), "claude-code 1")
        first = decoder.user_message("first instruction", "command-1")
        second = decoder.user_message("second instruction", "command-2")
        echo = decoder.feed(
            {
                "type": "user",
                "message": {"role": "user", "content": "second instruction"},
            }
        )
        other = decoder.feed(
            {
                "type": "user",
                "message": {"role": "user", "content": "typed in the terminal"},
            }
        )

        self.assertEqual(first.correlation_id, "command-1")
        self.assertEqual(second.correlation_id, "command-2")
        self.assertEqual(first.payload.text, "first instruction")
        self.assertEqual(echo, [])
        self.assertEqual(other[0].payload.text, "typed in the terminal")
        self.assertNotEqual(other[0].correlation_id, "command-2")
        self.assertNotEqual(other[0].correlation_id, second.correlation_id)

    def test_command_does_not_carry_a_prompt(self) -> None:
        self.assertNotIn("pong", " ".join(claude_argv()))
        self.assertIn("--input-format", claude_argv())


class GrokStreamTests(unittest.TestCase):
    def test_synthetic_turn_publishes_output_and_declared_capabilities(self) -> None:
        decoder = GrokEventDecoder(SessionKey("grok", "local-1", "demo"), "grok 1")
        lines = _lines("grok-stdio")
        self.assertEqual(decoder.feed(lines[0]), [])
        self.assertIn("loadSession", decoder.catalog["agent_capabilities"])
        self.assertIn("promptCapabilities.embeddedContext", decoder.catalog["agent_capabilities"])
        self.assertIn("sessionCapabilities.list", decoder.catalog["agent_capabilities"])
        self.assertNotIn("auth", decoder.catalog["agent_capabilities"])
        self.assertFalse(any(name.startswith("_") for name in decoder.catalog["agent_capabilities"]))
        self.assertEqual(decoder.feed(lines[1]), [])
        self.assertEqual(decoder.catalog["model"], "example-model")
        self.assertNotEqual(decoder.session_id_vendor, "")
        self.assertEqual(decoder.feed(lines[2]), [])
        message = decoder.feed(lines[3])
        self.assertEqual(message[0].payload.text, "pong")
        tool = decoder.feed(lines[4]) + decoder.feed(lines[5])
        blob = "".join(event.to_json() for event in tool)
        self.assertNotIn("secret-argument", blob)
        self.assertNotIn("hidden thought", blob)
        self.assertEqual(tool[0].payload.name, "Read")
        self.assertEqual(tool[1].payload.text, "file text")
        self.assertEqual(tool[2].payload.name, "Read")
        prompt = decoder.next_prompt("Reply with the single word pong")
        self.assertEqual(prompt["id"], 10)
        done = decoder.feed(lines[6])
        self.assertNotIn("vendor-session", done[-1].to_json())
        self.assertEqual(done[-1].kind, InteractionKind.TASK_COMPLETED)

    def test_permission_selects_a_matching_option(self) -> None:
        decoder = GrokEventDecoder(SessionKey("grok", "local-1", "demo"), "grok 1")
        approval = decoder.feed(_lines("grok-stdio")[7])
        self.assertEqual(approval[0].payload.summary, "请求使用工具 Bash")
        request_id = approval[0].payload.request_id
        allowed = decoder.permission_result(request_id, allow=True)
        self.assertEqual(allowed["result"]["outcome"]["optionId"], "allow-once")
        with self.assertRaises(RuntimeError):
            decoder.permission_result(request_id, allow=False)

    def test_each_prompt_keeps_its_own_id_and_the_echo_is_skipped(self) -> None:
        decoder = GrokEventDecoder(SessionKey("grok", "local-1", "demo"), "grok 1")
        first = decoder.user_message("first instruction", "command-1")
        second = decoder.user_message("second instruction", "command-2")
        echo = decoder.feed(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": "second instruction"},
                    }
                },
            }
        )
        other = decoder.feed(
            {
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": "typed in the terminal"},
                    }
                },
            }
        )

        self.assertEqual(first.correlation_id, "command-1")
        self.assertEqual(second.correlation_id, "command-2")
        self.assertEqual(echo, [])
        self.assertEqual(other[0].payload.text, "typed in the terminal")
        self.assertNotEqual(other[0].correlation_id, second.correlation_id)

    def test_command_does_not_carry_a_prompt(self) -> None:
        argv = grok_argv(Path("/tmp/deskhelm-grok-leader.sock"))
        self.assertNotIn("pong", " ".join(argv))
        self.assertIn("stdio", argv)
        self.assertIn("--no-leader", argv)

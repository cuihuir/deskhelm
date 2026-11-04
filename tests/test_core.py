from __future__ import annotations

from collections import defaultdict
from typing import Any
import unittest

from deskhelm_core import Close, Core, CoreLimits, Send
from deskhelm_protocol import SessionKey


KEY = SessionKey("grok", "session-1", "demo")
OTHER = SessionKey("claude", "session-2", "demo")
RUNTIME = {
    "adapter_id": "deskhelm-test",
    "adapter_version": "1",
    "runtime_name": "test",
    "runtime_version": "1",
}
CAPS = {
    "version": 1,
    "prompt_while_busy": "queue",
    "interrupt": True,
    "approvals": "answerable",
    "approval_overflow": "decline",
    "user_input": "native_ui",
    "resume": False,
    "configure": ["model"],
    "catalog": ["models"],
}


class _Harness:
    def __init__(self, **limits: Any) -> None:
        self.core = Core("test", CoreLimits(**limits))
        self.now = 1_000_000
        self.outbox: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.closed: dict[str, str] = {}
        self.seq: dict[str, int] = {}
        self.sent: list[dict[str, Any]] = []

    def apply(self, outputs) -> None:
        for output in outputs:
            if isinstance(output, Send):
                self.outbox[output.connection].append(output.frame)
            else:
                assert isinstance(output, Close)
                self.closed[output.connection] = output.code

    def connect(self, connection: str) -> None:
        self.apply(self.core.connect(connection))

    def send(self, connection: str, frame: dict[str, Any]) -> None:
        self.sent.append(frame)
        self.apply(self.core.receive(connection, frame, self.now))

    def drop(self, connection: str) -> None:
        self.apply(self.core.disconnect(connection, self.now))

    def tick(self, advance: int = 0) -> None:
        self.now += advance
        self.apply(self.core.tick(self.now))

    def take(self, connection: str, kind: str | None = None) -> list[dict[str, Any]]:
        frames = self.outbox.pop(connection, [])
        return [frame for frame in frames if kind is None or frame["type"] == kind]

    def host(
        self,
        connection: str,
        key: SessionKey = KEY,
        capabilities: dict[str, Any] | None = None,
        open_requests: list[dict[str, Any]] | None = None,
    ) -> None:
        self.connect(connection)
        self.send(connection, {"type": "hello", "protocol_version": 2, "role": "host", "client_id": connection})
        self.send(
            connection,
            {
                "type": "session_open",
                "session": key.to_dict(),
                "runtime": RUNTIME,
                "capabilities": capabilities or CAPS,
                "open_requests": open_requests or [],
            },
        )
        self.seq[connection] = 0

    def fact(self, connection: str, kind: str, meta: dict | None = None, content: dict | None = None) -> None:
        self.seq[connection] += 1
        self.send(
            connection,
            {"type": "fact", "seq": self.seq[connection], "kind": kind, "meta": meta or {}, "content": content or {}},
        )

    def client(self, connection: str, tier: str = "operator", streams: tuple[str, ...] = ("console",)) -> None:
        self.connect(connection)
        self.send(
            connection,
            {"type": "hello", "protocol_version": 2, "role": "client", "client_id": connection, "tier": tier},
        )
        for stream in streams:
            self.send(connection, {"type": "subscribe", "stream": stream})

    def command(
        self,
        connection: str,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        key: SessionKey = KEY,
        command_id: str = "command-1",
        idempotency_key: str | None = None,
        issued_by: str | None = None,
    ) -> None:
        self.send(
            connection,
            {
                "type": "command",
                "command": {
                    "command_id": command_id,
                    "kind": kind,
                    **key.to_dict(),
                    "issued_by": issued_by or connection,
                    "issued_at": self.now,
                    "expires_at": self.now + 60_000,
                    "idempotency_key": idempotency_key or command_id,
                    "payload": payload or {},
                },
            },
        )

    def approval(self, host: str, provider_ref: str = "rpc-7", answerable: bool = True) -> str:
        self.fact(
            host,
            "request_opened",
            {
                "provider_ref": provider_ref,
                "kind": "approval",
                "summary": "请求使用工具 execute",
                "expires_at": self.now + 30_000,
                "answerable": answerable,
            },
        )
        view = self.core.session(KEY)
        return view["approvals"][-1]["approval_id"]


class HandshakeAndSessionTests(unittest.TestCase):
    def test_first_frame_must_be_a_valid_hello(self) -> None:
        harness = _Harness()
        harness.connect("x")
        harness.send("x", {"type": "fact", "seq": 1, "kind": "ready"})
        harness.connect("y")
        harness.send("y", {"type": "hello", "protocol_version": 1, "role": "host", "client_id": "y"})
        harness.connect("z")
        harness.send("z", {"type": "hello", "protocol_version": 2, "role": "host", "client_id": "z", "tier": "operator"})

        self.assertEqual(harness.closed, {"x": "protocol_violation", "y": "unsupported_version", "z": "protocol_violation"})

    def test_opened_session_reaches_console_subscribers(self) -> None:
        harness = _Harness()
        harness.client("web")
        snapshot = harness.take("web", "snapshot")[0]
        harness.host("host")
        harness.fact("host", "ready")

        self.assertEqual(snapshot["state"]["sessions"], [])
        self.assertEqual(harness.take("host", "session_open_result")[0]["status"], "opened")
        deltas = harness.take("web", "delta")
        self.assertEqual(deltas[0]["changes"][0]["session"]["activity"], "starting")
        self.assertEqual(deltas[-1]["changes"][0]["session"]["activity"], "idle")

    def test_an_active_session_cannot_be_opened_twice(self) -> None:
        harness = _Harness()
        harness.host("host")
        harness.host("rival")

        self.assertEqual(harness.take("rival", "session_open_result")[0]["code"], "already_active")
        self.assertEqual(harness.closed["rival"], "already_active")
        self.assertEqual(harness.core.session(KEY)["lifecycle"], "active")

    def test_fact_gap_closes_the_host_and_disconnects_the_session(self) -> None:
        harness = _Harness()
        harness.host("host")
        harness.send("host", {"type": "fact", "seq": 3, "kind": "ready"})

        self.assertEqual(harness.closed["host"], "protocol_violation")
        self.assertEqual(harness.core.session(KEY)["lifecycle"], "disconnected")

    def test_replacement_host_reopens_a_disconnected_session(self) -> None:
        harness = _Harness()
        harness.host("host")
        harness.client("web", streams=("console", "interaction"))
        harness.drop("host")
        harness.take("web")
        harness.host(
            "replacement",
            open_requests=[
                {
                    "provider_ref": "rpc-1",
                    "kind": "approval",
                    "summary": "请求使用工具 edit",
                    "expires_at": harness.now + 10_000,
                    "answerable": True,
                    "detail": "notes.txt",
                }
            ],
        )

        view = harness.core.session(KEY)
        announced = harness.take("web", "interaction")[0]["event"]
        self.assertEqual(view["lifecycle"], "active")
        self.assertEqual(view["activity"], "waiting_approval")
        self.assertEqual(len(view["approvals"]), 1)
        # The rebuilt approval is announced with its detail; its ticket stays content-free.
        self.assertEqual((announced["kind"], announced["payload"]["request_id"], announced["payload"]["detail"]),
                         ("approval_request", view["approvals"][0]["approval_id"], "notes.txt"))
        self.assertNotIn("detail", view["approvals"][0])

    def test_disconnected_session_expires_after_its_ttl(self) -> None:
        harness = _Harness(disconnected_ttl_ms=1_000)
        harness.client("web")
        harness.host("host")
        harness.drop("host")
        harness.take("web")
        harness.tick(1_000)

        removal = harness.take("web", "delta")[0]["changes"][0]
        self.assertEqual(removal["op"], "session_removed")
        self.assertIsNone(harness.core.session(KEY))


class CommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _Harness()
        self.harness.host("host")
        self.harness.fact("host", "ready")
        self.harness.client("web")
        self.harness.take("host")

    def test_result_waits_for_the_host_receipt(self) -> None:
        harness = self.harness
        harness.command("web", "submit_prompt", {"text": "hello"})
        effect = harness.take("host", "effect")[0]
        early = harness.take("web", "command_result")
        harness.send("host", {"type": "effect_result", "effect_id": effect["effect_id"], "outcome": "accepted"})

        self.assertEqual(effect["kind"], "submit")
        self.assertEqual(effect["payload"], {"command_id": "command-1", "text": "hello"})
        self.assertEqual(early, [])
        result = harness.take("web", "command_result")[0]
        self.assertEqual((result["code"], result["duplicate"]), ("dispatched", False))

    def test_duplicates_wait_for_and_then_reuse_the_first_result(self) -> None:
        harness = self.harness
        harness.command("web", "submit_prompt", {"text": "hello"})
        harness.command("web", "submit_prompt", {"text": "hello"})
        effects = harness.take("host", "effect")
        harness.send("host", {"type": "effect_result", "effect_id": effects[0]["effect_id"], "outcome": "accepted"})
        harness.command("web", "submit_prompt", {"text": "hello"})

        self.assertEqual(len(effects), 1)
        results = harness.take("web", "command_result")
        self.assertEqual([(item["code"], item["duplicate"]) for item in results],
                         [("dispatched", False), ("dispatched", True), ("dispatched", True)])

    def test_undelivered_result_is_not_retained(self) -> None:
        harness = self.harness
        harness.command("web", "submit_prompt", {"text": "hello"})
        effect = harness.take("host", "effect")[0]
        harness.send("host", {"type": "effect_result", "effect_id": effect["effect_id"], "outcome": "undelivered"})
        harness.command("web", "submit_prompt", {"text": "hello"})

        self.assertEqual(harness.take("web", "command_result")[0]["code"], "undelivered")
        self.assertEqual(len(harness.take("host", "effect")), 1)

    def test_missing_receipt_is_ambiguous_after_the_deadline(self) -> None:
        harness = self.harness
        harness.command("web", "submit_prompt", {"text": "hello"})
        frame = harness.sent[-1]
        harness.tick(5_000)
        harness.send("web", frame)

        results = harness.take("web", "command_result")
        self.assertEqual([(item["code"], item["duplicate"]) for item in results],
                         [("dispatch_failed", False), ("dispatch_failed", True)])

    def test_envelope_problems_are_reported_without_dispatch(self) -> None:
        harness = self.harness
        harness.command("web", "submit_prompt", {"text": "hi"}, command_id="a", issued_by="someone-else")
        harness.command("web", "submit_prompt", {}, command_id="b")
        harness.command("web", "submit_prompt", {"text": "hi"}, command_id="c", key=OTHER)
        harness.command("web", "submit_prompt", {"text": "hi"}, command_id="d")
        harness.command("web", "submit_prompt", {"text": "changed"}, command_id="e", idempotency_key="d")

        codes = [item["code"] for item in harness.take("web", "command_result")]
        self.assertEqual(codes, ["issuer_mismatch", "invalid_command", "target_not_found", "idempotency_conflict"])

    def test_tiers_limit_which_commands_a_client_may_send(self) -> None:
        harness = self.harness
        for tier in ("observer", "device", "voice"):
            harness.client(tier, tier=tier)
            harness.command(tier, "submit_prompt", {"text": "hi"}, command_id=f"{tier}-prompt")
        voice_effect = harness.take("host", "effect")

        self.assertEqual(harness.take("observer", "command_result")[0]["code"], "not_permitted")
        self.assertEqual(harness.take("device", "command_result")[0]["code"], "not_permitted")
        self.assertEqual(len(voice_effect), 1)

    def test_capabilities_are_enforced(self) -> None:
        harness = _Harness()
        harness.host("host", capabilities={**CAPS, "interrupt": False, "configure": []})
        harness.client("web")
        harness.command("web", "interrupt", command_id="i")
        harness.command("web", "configure_session", {"model": "m"}, command_id="c")

        codes = [item["code"] for item in harness.take("web", "command_result")]
        self.assertEqual(codes, ["unsupported", "unsupported"])
        self.assertEqual(harness.take("host", "effect"), [])

    def test_focus_is_explicit_and_cleared_when_the_host_leaves(self) -> None:
        harness = self.harness
        harness.command("web", "focus", command_id="f")
        focused = harness.core.focus
        harness.command("web", "submit_prompt", {"text": "hello"}, command_id="p")
        harness.take("web")
        harness.drop("host")

        self.assertEqual(focused, KEY)
        self.assertIsNone(harness.core.focus)
        results = harness.take("web", "command_result")
        self.assertEqual(results[0]["code"], "dispatch_failed")
        self.assertEqual(harness.core.session(KEY)["lifecycle"], "disconnected")


class ApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _Harness()
        self.harness.host("host")
        self.harness.fact("host", "ready")
        self.harness.client("web", streams=("console", "interaction"))
        self.harness.take("host")

    def test_decision_names_the_bridge_id_and_the_host_gets_its_ref(self) -> None:
        harness = self.harness
        approval_id = harness.approval("host")
        event = harness.take("web", "interaction")[-1]["event"]
        harness.command("web", "approve", {"approval_id": approval_id})
        effect = harness.take("host", "effect")[0]
        harness.send("host", {"type": "effect_result", "effect_id": effect["effect_id"], "outcome": "accepted"})
        harness.command("web", "reject", {"approval_id": approval_id}, command_id="second")

        self.assertEqual(event["kind"], "approval_request")
        self.assertEqual(event["payload"]["request_id"], approval_id)
        self.assertEqual(effect["payload"]["provider_ref"], "rpc-7")
        self.assertEqual(effect["payload"]["decision"], "approve")
        codes = [item["code"] for item in harness.take("web", "command_result")]
        self.assertEqual(codes, ["dispatched", "approval_already_decided"])
        self.assertEqual(harness.core.session(KEY)["approvals"], [])

    def test_undelivered_decision_restores_the_ticket(self) -> None:
        harness = self.harness
        approval_id = harness.approval("host")
        harness.command("web", "approve", {"approval_id": approval_id})
        effect = harness.take("host", "effect")[0]
        harness.command("web", "reject", {"approval_id": approval_id}, command_id="while-reserved")
        harness.send("host", {"type": "effect_result", "effect_id": effect["effect_id"], "outcome": "undelivered"})
        harness.command("web", "approve", {"approval_id": approval_id}, command_id="retry")

        codes = [item["code"] for item in harness.take("web", "command_result")]
        self.assertEqual(codes, ["approval_already_decided", "undelivered"])
        self.assertEqual(len(harness.take("host", "effect")), 1)

    def test_vendor_resolution_during_an_undelivered_decision_stays_decided(self) -> None:
        harness = self.harness
        approval_id = harness.approval("host")
        harness.command("web", "approve", {"approval_id": approval_id})
        effect = harness.take("host", "effect")[0]
        harness.fact("host", "request_resolved", {"provider_ref": "rpc-7", "by": "native"})
        harness.send("host", {"type": "effect_result", "effect_id": effect["effect_id"], "outcome": "undelivered"})
        harness.command("web", "approve", {"approval_id": approval_id}, command_id="retry")

        codes = [item["code"] for item in harness.take("web", "command_result")]
        self.assertEqual(codes, ["undelivered", "approval_already_decided"])

    def resolutions(self) -> list[dict]:
        return [frame["event"]["payload"] for frame in self.harness.take("web", "interaction")
                if frame["event"]["kind"] == "approval_resolved"]

    def test_each_way_an_approval_ends_is_published(self) -> None:
        harness = self.harness
        decided = harness.approval("host", provider_ref="rpc-1")
        harness.command("web", "reject", {"approval_id": decided})
        effect = harness.take("host", "effect")[0]
        harness.send("host", {"type": "effect_result", "effect_id": effect["effect_id"], "outcome": "accepted"})
        native = harness.approval("host", provider_ref="rpc-2")
        harness.fact("host", "request_resolved", {"provider_ref": "rpc-2", "by": "native"})
        expired = harness.approval("host", provider_ref="rpc-3")
        harness.tick(30_000)

        self.assertEqual(self.resolutions(), [
            {"request_id": decided, "by": "deskhelm", "decision": "reject"},
            {"request_id": native, "by": "native"},
            {"request_id": expired, "by": "expired"},
        ])

    def test_an_approval_its_turn_left_or_an_unknown_decision_is_published(self) -> None:
        harness = self.harness
        harness.fact("host", "turn_started", {"turn_ref": "t1"})
        cancelled = harness.approval("host", provider_ref="rpc-1")
        harness.fact("host", "turn_terminal", {"status": "completed", "turn_ref": "t1"})
        unknown = harness.approval("host", provider_ref="rpc-2")
        harness.command("web", "approve", {"approval_id": unknown})
        harness.tick(6_000)
        vendor = harness.approval("host", provider_ref="rpc-3")
        harness.command("web", "approve", {"approval_id": vendor}, command_id="third")
        effect = harness.take("host", "effect")[-1]
        harness.fact("host", "request_resolved", {"provider_ref": "rpc-3", "by": "native"})
        harness.send("host", {"type": "effect_result", "effect_id": effect["effect_id"], "outcome": "undelivered"})

        self.assertEqual(self.resolutions(), [
            {"request_id": cancelled, "by": "cancelled"},
            {"request_id": unknown, "by": "deskhelm", "decision": "approve", "unknown": True},
            {"request_id": vendor, "by": "native"},
        ])

    def test_notify_only_and_unanswerable_tickets_reject_decisions(self) -> None:
        harness = self.harness
        approval_id = harness.approval("host", answerable=False)
        harness.command("web", "approve", {"approval_id": approval_id})

        self.assertEqual(harness.take("web", "command_result")[0]["code"], "unsupported")

    def test_terminal_turn_clears_tickets_and_reports_interruption(self) -> None:
        harness = self.harness
        harness.fact("host", "turn_started", {"turn_ref": "turn-1", "command_id": "prompt-9"})
        harness.approval("host")
        harness.take("web")
        harness.fact("host", "turn_terminal", {"status": "interrupted", "turn_ref": "turn-1"})

        view = harness.core.session(KEY)
        event = harness.take("web", "interaction")[-1]["event"]
        self.assertEqual((view["activity"], view["approvals"]), ("idle", []))
        self.assertEqual(view["last_outcome"]["status"], "interrupted")
        self.assertEqual((event["kind"], event["payload"]["error_code"], event["correlation_id"]),
                         ("task_failed", "interrupted", "prompt-9"))

    def test_expired_tickets_leave_the_console(self) -> None:
        harness = self.harness
        harness.approval("host")
        harness.tick(30_000)

        self.assertEqual(harness.core.session(KEY)["approvals"], [])
        self.assertEqual(harness.core.session(KEY)["activity"], "idle")


QUESTION = {"id": "0", "title": "Which board?", "options": [{"label": "A"}, {"label": "B"}], "other": True}


def _input_meta(harness: _Harness, provider_ref: str = "call-1", *, blocking: bool = False) -> dict[str, Any]:
    meta = {"provider_ref": provider_ref, "kind": "input", "summary": "Codex requested user input",
            "expires_at": harness.now + 3_600_000, "answerable": True}
    return meta if blocking else {**meta, "blocking": False}


class QuestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _Harness()
        self.harness.host("host", capabilities={**CAPS, "user_input": "answerable"})
        self.harness.fact("host", "ready")
        self.harness.client("web", streams=("console", "interaction"))
        self.harness.client("voice", tier="voice")
        self.harness.take("host")

    def question(self, provider_ref: str = "call-1", *, blocking: bool = False) -> str:
        meta = _input_meta(self.harness, provider_ref, blocking=blocking)
        self.harness.fact("host", "request_opened", meta, {"detail": "Which board?", "questions": [QUESTION]})
        return self.harness.core.session(KEY)["approvals"][-1]["approval_id"]

    def test_a_question_is_answered_by_question_id(self) -> None:
        harness = self.harness
        approval_id = self.question()
        opened = harness.take("web", "interaction")[-1]["event"]
        harness.command("web", "answer", {"approval_id": approval_id, "answers": {"0": ["B"]}})
        effect = harness.take("host", "effect")[0]
        harness.send("host", {"type": "effect_result", "effect_id": effect["effect_id"], "outcome": "accepted"})
        frames = harness.take("web")

        self.assertEqual((opened["kind"], opened["payload"]["request_id"], opened["payload"]["blocking"]),
                         ("user_input_required", approval_id, False))
        self.assertEqual(opened["payload"]["questions"][0]["options"][1], {"label": "B", "description": ""})
        self.assertEqual((effect["kind"], effect["payload"]["provider_ref"], effect["payload"]["answers"]),
                         ("answer", "call-1", {"0": ["B"]}))
        self.assertEqual([frame["event"] for frame in frames if frame["type"] == "interaction"][-1]["payload"],
                         {"request_id": approval_id, "by": "deskhelm", "answers": {"0": ["B"]}})
        self.assertEqual([frame["code"] for frame in frames if frame["type"] == "command_result"], ["dispatched"])
        self.assertEqual(harness.core.session(KEY)["approvals"], [])
        # The voice tier sees only the ticket's summary, never the question or its answer.
        self.assertNotIn("Which board?", repr(harness.take("voice")))

    def test_an_answer_names_the_questions_of_an_input_and_comes_from_the_operator(self) -> None:
        harness = self.harness
        approval_id = self.question()
        approval = harness.approval("host", provider_ref="rpc-9")
        harness.take("web")
        harness.take("voice")
        harness.command("web", "answer", {"approval_id": approval_id, "answers": {"9": ["B"]}}, command_id="a1")
        harness.command("web", "answer", {"approval_id": approval, "answers": {"0": ["B"]}}, command_id="a2")
        harness.command("web", "answer", {"approval_id": approval_id, "answers": {}}, command_id="a3")
        harness.command("voice", "answer", {"approval_id": approval_id, "answers": {"0": ["B"]}}, command_id="a4")

        self.assertEqual([frame["code"] for frame in harness.take("web", "command_result")],
                         ["invalid_command", "approval_not_found", "invalid_command"])
        self.assertEqual([frame["code"] for frame in harness.take("voice", "command_result")], ["not_permitted"])

    def test_a_session_whose_agent_answers_natively_refuses_answers(self) -> None:
        harness = _Harness()
        harness.host("host")
        harness.client("web")
        harness.fact("host", "request_opened", _input_meta(harness), {"questions": [QUESTION]})
        approval_id = harness.core.session(KEY)["approvals"][0]["approval_id"]
        harness.take("web")
        harness.command("web", "answer", {"approval_id": approval_id, "answers": {"0": ["A"]}})

        self.assertEqual(harness.take("web", "command_result")[0]["code"], "unsupported")

    def test_a_question_the_agent_does_not_wait_on_outlives_its_turn(self) -> None:
        harness = self.harness
        harness.fact("host", "turn_started", {"turn_ref": "t1"})
        waiting_on = self.question()
        working = harness.core.session(KEY)["activity"]
        harness.fact("host", "turn_terminal", {"status": "completed", "turn_ref": "t1"})
        after = harness.core.session(KEY)
        harness.fact("host", "turn_started", {"turn_ref": "t2"})
        blocking = self.question("call-2", blocking=True)
        during = harness.core.session(KEY)["activity"]
        harness.fact("host", "turn_terminal", {"status": "completed", "turn_ref": "t2"})
        left = [ticket["approval_id"] for ticket in harness.core.session(KEY)["approvals"]]

        # An agent that asked without waiting is still working; after its turn it waits for the answer.
        self.assertEqual((working, after["activity"]), ("working", "waiting_input"))
        self.assertEqual(during, "waiting_input")
        self.assertEqual(left, [waiting_on])
        self.assertNotIn(blocking, left)

    def test_an_answer_given_in_the_agent_reaches_the_operator(self) -> None:
        harness = self.harness
        approval_id = self.question()
        harness.take("web")
        harness.fact("host", "request_resolved", {"provider_ref": "call-1", "by": "native"}, {"answers": {"0": ["A"]}})

        self.assertEqual(harness.take("web", "interaction")[-1]["event"]["payload"],
                         {"request_id": approval_id, "by": "native", "answers": {"0": ["A"]}})

    def test_questions_and_answers_are_checked_at_the_boundary(self) -> None:
        cases = [
            ("input", {"questions": [QUESTION, QUESTION]}),
            ("input", {"questions": [{"id": "0", "title": "Pick", "options": [], "other": False}]}),
            ("approval", {"questions": [QUESTION]}),
        ]
        closed = []
        for kind, content in cases:
            harness = _Harness()
            harness.host("host")
            harness.fact("host", "request_opened", {**_input_meta(harness), "kind": kind}, content)
            closed.append(harness.closed.get("host"))
        bad_answer = _Harness()
        bad_answer.host("host")
        bad_answer.fact("host", "request_resolved", {"provider_ref": "call-1", "by": "native"}, {"answers": {"0": []}})

        self.assertEqual(closed, ["protocol_violation"] * 3)
        self.assertEqual(bad_answer.closed.get("host"), "protocol_violation")


class ProjectionTests(unittest.TestCase):
    def test_late_interaction_subscriber_gets_the_session_window(self) -> None:
        harness = _Harness(window_events=2)
        harness.host("host")
        for index in range(3):
            harness.fact("host", "message", {"message_ref": f"m{index}", "role": "assistant", "phase": "complete"},
                         {"text": f"reply {index}"})
        harness.client("web", streams=("interaction",))
        frames = [frame for frame in harness.take("web") if frame["type"] != "hello_ok"]
        harness.fact("host", "message", {"message_ref": "m3", "role": "assistant", "phase": "complete"}, {"text": "live"})

        self.assertEqual(frames[0]["type"], "snapshot")
        replayed = [frame["event"]["payload"]["text"] for frame in frames[1:]]
        self.assertEqual(replayed, ["reply 1", "reply 2"])
        live = harness.take("web", "interaction")[0]
        self.assertNotIn("replay", live)

    def test_a_tool_output_replacement_reaches_the_interaction_stream(self) -> None:
        harness = _Harness()
        harness.host("host")
        harness.client("web", streams=("interaction",))
        harness.take("web")
        harness.fact("host", "tool", {"tool_ref": "t1", "label": "command", "phase": "output", "replace": True},
                     {"text": "whole output"})
        harness.fact("host", "tool", {"tool_ref": "t1", "label": "command", "phase": "output"}, {"text": "more"})
        replaced, extended = (frame["event"]["payload"] for frame in harness.take("web", "interaction"))

        self.assertEqual((replaced["replace"], "replace" in extended), (True, False))

    def test_a_request_detail_reaches_only_the_operator_interaction(self) -> None:
        harness = _Harness()
        harness.host("host")
        harness.client("web", streams=("console", "interaction"))
        harness.client("voice", tier="voice")
        harness.take("web")
        harness.take("voice")
        request = {"provider_ref": "rpc-1", "kind": "approval", "summary": "请求使用工具 Bash",
                   "expires_at": harness.now + 30_000, "answerable": True}
        harness.fact("host", "request_opened", request, {"detail": "rm -rf build"})
        harness.fact("host", "request_opened", {**request, "provider_ref": "rpc-2", "kind": "input"})

        web = harness.take("web")
        events = [frame["event"]["payload"] for frame in web if frame["type"] == "interaction"]
        self.assertEqual([(payload.get("detail"), payload.get("prompt")) for payload in events],
                         [("rm -rf build", None), ("", "请求使用工具 Bash")])
        self.assertNotIn("rm -rf build", repr([frame for frame in web if frame["type"] != "interaction"]))
        self.assertNotIn("rm -rf build", repr(harness.take("voice")))

    def test_a_request_detail_is_content_only(self) -> None:
        request = {"provider_ref": "rpc-1", "kind": "approval", "summary": "请求使用工具 Bash",
                   "expires_at": 2_000_000, "answerable": True}
        in_meta = _Harness()
        in_meta.host("host")
        in_meta.fact("host", "request_opened", {**request, "detail": "rm -rf build"})
        other_content = _Harness()
        other_content.host("host")
        other_content.fact("host", "request_opened", request, {"text": "rm -rf build"})
        too_long = _Harness()
        too_long.host("host")
        too_long.fact("host", "request_opened", request, {"detail": "x" * (8 * 1024 + 1)})

        self.assertEqual([harness.closed["host"] for harness in (in_meta, other_content, too_long)],
                         ["protocol_violation"] * 3)

    def test_a_tool_completion_states_its_outcome(self) -> None:
        harness = _Harness()
        harness.host("host")
        harness.client("web", streams=("interaction",))
        harness.take("web")
        harness.fact("host", "tool", {"tool_ref": "t1", "label": "command", "phase": "complete", "status": "declined"})
        declined = harness.take("web", "interaction")[0]["event"]["payload"]
        harness.fact("host", "tool", {"tool_ref": "t2", "label": "command", "phase": "complete", "exit_code": 0})

        self.assertEqual((declined["status"], declined["exit_code"]), ("declined", None))
        self.assertEqual(harness.closed["host"], "protocol_violation")

    def test_only_a_tool_completion_carries_a_status(self) -> None:
        harness = _Harness()
        harness.host("host")
        harness.fact("host", "tool", {"tool_ref": "t1", "label": "command", "phase": "start", "status": "completed"})

        self.assertEqual(harness.closed["host"], "protocol_violation")

    def test_a_completed_message_leaves_its_deltas_out_of_the_window(self) -> None:
        harness = _Harness()
        harness.host("host")
        harness.fact("host", "message", {"message_ref": "u1", "role": "user", "phase": "complete"}, {"text": "go"})
        for piece in ("Hel", "lo"):
            harness.fact("host", "message", {"message_ref": "a1", "role": "assistant", "phase": "delta"}, {"text": piece})
        harness.fact("host", "message", {"message_ref": "a1", "role": "assistant", "phase": "complete"}, {"text": "Hello"})
        for piece in ("cut", " off"):
            harness.fact("host", "message", {"message_ref": "a2", "role": "assistant", "phase": "delta"}, {"text": piece})
        harness.fact("host", "message", {"message_ref": "a2", "role": "assistant", "phase": "complete"}, {"text": ""})
        harness.client("web", streams=("interaction",))
        replayed = [frame["event"]["payload"] for frame in harness.take("web", "interaction")]

        self.assertEqual([(payload["phase"], payload["text"]) for payload in replayed],
                         [("complete", "go"), ("complete", "Hello"), ("delta", "cut"), ("delta", " off"), ("complete", "")])

    def test_only_the_operator_tier_reads_interaction_content(self) -> None:
        harness = _Harness()
        harness.client("device", tier="device", streams=("interaction",))

        self.assertEqual(harness.closed["device"], "not_permitted")

    def test_console_resume_sends_only_missed_deltas(self) -> None:
        harness = _Harness()
        harness.client("web")
        harness.host("host")
        cursor = harness.take("web", "delta")[-1]["seq"]
        harness.fact("host", "ready")
        harness.client("again", streams=())
        harness.send("again", {"type": "subscribe", "stream": "console", "after": {"instance_id": "test", "seq": cursor}})
        harness.client("stale", streams=())
        harness.send("stale", {"type": "subscribe", "stream": "console", "after": {"instance_id": "old", "seq": cursor}})

        resumed = harness.take("again")
        self.assertEqual([frame["type"] for frame in resumed[1:]], ["delta"])
        self.assertEqual(harness.take("stale")[-1]["type"], "snapshot")

    def test_a_fact_that_changes_no_session_view_sends_no_delta(self) -> None:
        harness = _Harness()
        harness.client("web")
        harness.host("host")
        harness.fact("host", "ready")
        harness.take("web")
        for text in ("Hel", "lo"):
            harness.fact("host", "message", {"message_ref": "m1", "role": "assistant", "phase": "delta"}, {"text": text})
        quiet = harness.take("web", "delta")
        harness.fact("host", "turn_started", {"turn_ref": "t1"})
        busy = harness.take("web", "delta")

        self.assertEqual(quiet, [])
        self.assertEqual([change["session"]["activity"] for change in busy[0]["changes"]], ["working"])

    def test_each_pushed_catalog_counts_in_the_console(self) -> None:
        harness = _Harness()
        harness.client("web")
        harness.host("host")
        harness.take("web")
        harness.fact("host", "catalog", content={"catalog": {"models": [{"id": "m"}]}})
        first = harness.take("web", "delta")
        harness.fact("host", "catalog", content={"catalog": {"models": [{"id": "n"}]}})
        second = harness.take("web", "delta")

        self.assertEqual(first[0]["changes"][0]["session"]["catalog_rev"], 1)
        self.assertEqual(second[0]["changes"][0]["session"]["catalog_rev"], 2)

    def test_catalog_query_reads_the_cached_catalog(self) -> None:
        harness = _Harness()
        harness.host("host")
        harness.fact("host", "catalog", content={"catalog": {"models": [{"id": "m"}]}})
        harness.client("web", streams=())
        harness.client("voice", tier="voice", streams=())
        for connection in ("web", "voice"):
            harness.send(connection, {"type": "query", "query_id": "q", "query": "catalog", "session": KEY.to_dict()})

        self.assertEqual(harness.take("web", "query_result")[0]["catalog"], {"models": [{"id": "m"}]})
        self.assertEqual(harness.take("voice", "query_result")[0]["status"], "not_permitted")

    def test_capabilities_fact_replaces_the_matrix(self) -> None:
        harness = _Harness()
        harness.host("host")
        harness.client("web", streams=())
        harness.fact("host", "capabilities", {"capabilities": {**CAPS, "approvals": "none"}})
        harness.approval("host")
        approval_id = harness.core.session(KEY)["approvals"][0]["approval_id"]
        harness.command("web", "approve", {"approval_id": approval_id})

        self.assertEqual(harness.core.session(KEY)["capabilities"]["approvals"], "none")
        self.assertEqual(harness.take("web", "command_result")[0]["code"], "unsupported")

    def test_session_end_removes_the_session(self) -> None:
        harness = _Harness()
        harness.client("web")
        harness.host("host")
        harness.take("web")
        harness.fact("host", "session_ended", {"reason": "vendor exited"})

        self.assertIsNone(harness.core.session(KEY))
        self.assertEqual(harness.take("web", "delta")[0]["changes"][0]["op"], "session_removed")


if __name__ == "__main__":
    unittest.main()
